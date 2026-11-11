"""Local OpenAI-compatible gateway that fronts the Command Code CLI.

The news-monitor summarizer (``app/summarizer.py`` -> ``DeepSeekClient``)
speaks the OpenAI chat-completions wire format to ``DEEPSEEK_BASE_URL``.
Instead of pointing that at api.deepseek.com, this module exposes the same
wire locally and answers each request by running the installed Command Code
CLI in headless mode on the configured Command Code model (default
``deepseek/deepseek-v4-flash``).

Why: Command Code serves models through its own subscription/CLI gateway and
does not publish a public OpenAI-compatible HTTP endpoint, so a local shim is
the smallest change that lets the existing summarizer keep its code path.

Run (stays in the foreground):

    .venv\\Scripts\\python.exe -m app.cmd_gateway

Then point the project at it in ``.env``:

    SUMMARIZER_BASE_URL=http://127.0.0.1:8765/v1
    SUMMARIZER_MODEL=deepseek/deepseek-v4-flash
    SUMMARIZER_API_KEY=cmd-local   # any non-empty placeholder; gateway does not validate

Notes
-----
* Only the non-streaming chat-completions path is implemented (the summarizer
  never streams).
* Every system message and user message is joined in order and sent to the CLI
  as one prompt; the CLI's final text answer is returned as
  ``message.content`` verbatim (including a ```json fence when the model
  emits one - the existing DeepSeekClient already strips fences before
  parsing).
* Every request runs one headless CLI process. Keep the summarizer batch size
  modest (the default of 40 articles fits one call, but a run takes as long as
  one model turn).
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import logging
import os
import shutil
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from .settings import get_settings, load_environment

logger = logging.getLogger("cmd_gateway")

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
DEFAULT_MODEL = "deepseek/deepseek-v4-flash"
SERVICE_NAME = "tw-news-monitor-cmd-gateway"
DEFAULT_MAX_BODY_BYTES = 1024 * 1024
DEFAULT_MAX_CONCURRENCY = 1
MAX_CONCURRENCY_LIMIT = 8


class RequestTooLarge(ValueError):
    pass


def _is_loopback_host(host: str) -> bool:
    value = str(host or "").strip().lower()
    if value == "localhost":
        return True
    try:
        return ipaddress.ip_address(value).is_loopback
    except ValueError:
        return False

def _resolve_cmd_command() -> list[str]:
    """Return the CLI launch command as a list of argv pieces.

    The npm shims on Windows (cmd.cmd / cmd) are batch files that need a
    shell; launching the underlying ``node index.mjs`` directly is the most
    reliable and is what the gateway uses by default.
    """
    env = os.getenv("CMD_CLI_COMMAND", "").strip()
    if env:
        # Allow either a single executable or an "exe args..." string.
        return env.split(" ", 1) if " " in env else [env]
    node = shutil.which("node")
    if node:
        npm_entry = (
            Path.home() / "AppData" / "Roaming" / "npm" / "node_modules"
            / "command-code" / "dist" / "index.mjs"
        )
        if npm_entry.exists():
            return [node, str(npm_entry)]
    # Non-Windows fallback: plain `cmd` on PATH (npm-installed shell shim).
    found = shutil.which("cmd")
    return [found or "cmd"]


# Resolve once per process and expose for tests.
CMD_COMMAND = _resolve_cmd_command()

#: Extra flags applied to every headless run.
EXTRA_FLAGS = [
    "--output-format", "text",
    "--skip-onboarding",
    "--max-turns", "1",
]


def run_headless(prompt: str, model: str | None = None, timeout: float = 300.0) -> str:
    """Run the Command Code CLI headless with the given prompt; return stdout.

    Raises ``RuntimeError`` when the CLI exits non-zero or produces no text.
    """
    argv = CMD_COMMAND + ["-p", prompt] + EXTRA_FLAGS
    if model:
        argv += ["--model", model]
    logger.info("cmd headless start: model=%s", model or DEFAULT_MODEL)
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
    except subprocess.TimeoutExpired:
        raise RuntimeError(f"cmd headless timed out after {timeout:.0f}s")
    except OSError as exc:
        raise RuntimeError(f"failed to start cmd CLI ({CMD_COMMAND!r}): {exc}") from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or "").strip()[-2000:]
        raise RuntimeError(f"cmd headless exited {proc.returncode}: {detail}")
    text = (proc.stdout or "").strip()
    if not text:
        raise RuntimeError("cmd headless returned empty output")
    return text


class _Handler(BaseHTTPRequestHandler):
    server_version = "CmdGateway/1.0"

    def log_message(self, fmt, *args):  # keep stdlib quiet; we log ourselves
        logger.debug("http: " + fmt, *args)

    def _read_json_body(self) -> dict:
        raw_length = self.headers.get("Content-Length")
        try:
            length = int(raw_length or 0)
        except (TypeError, ValueError) as exc:
            raise ValueError("invalid Content-Length") from exc
        if length <= 0:
            raise ValueError("empty request body")
        if length > self.server.max_body_bytes:
            raise RequestTooLarge(
                f"request body exceeds {self.server.max_body_bytes} bytes"
            )
        raw = self.rfile.read(length)
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ValueError(f"invalid JSON body: {exc}") from exc

    def _reply_json(self, payload: dict, status: int = 200) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _reply_error(self, status: int, message: str) -> None:
        logger.warning("reply %d: %s", status, message)
        self._reply_json(
            {"error": {"message": message, "type": "cmd_gateway_error", "code": status}},
            status=status,
        )

    def do_GET(self):  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path in ("/health", "/healthz"):
            self._reply_json({
                "status": "ok",
                "service": SERVICE_NAME,
                "model": self.server.model,
            })
            return
        self._reply_error(404, f"not found: {parsed.path}")

    def do_POST(self):  # noqa: N802 - stdlib handler API
        parsed = urlparse(self.path)
        if parsed.path not in ("/v1/chat/completions", "/chat/completions"):
            self._reply_error(404, f"not found: {parsed.path}")
            return
        try:
            body = self._read_json_body()
        except RequestTooLarge as exc:
            self._reply_error(413, str(exc))
            return
        except ValueError as exc:
            self._reply_error(400, str(exc))
            return

        messages = body.get("messages")
        if not isinstance(messages, list) or not messages:
            self._reply_error(400, "messages must be a non-empty list")
            return
        system_parts = [
            m.get("content", "")
            for m in messages
            if isinstance(m, dict) and m.get("role") == "system"
        ]
        user_parts = [
            m.get("content", "")
            for m in messages
            if isinstance(m, dict) and m.get("role") == "user"
        ]
        if not user_parts:
            self._reply_error(400, "messages must contain a user message")
            return
        prompt = "\n\n".join([*system_parts, *user_parts]).strip()
        if not prompt:
            self._reply_error(400, "user message content is empty")
            return

        model = body.get("model") or self.server.model or DEFAULT_MODEL
        if not self.server.cli_semaphore.acquire(blocking=False):
            self._reply_error(429, "maximum concurrent CLI requests reached")
            return
        try:
            started = time.monotonic()
            content = run_headless(prompt, model=model)
        except RuntimeError as exc:
            logger.error("cmd headless failed: %s", exc)
            self._reply_error(502, str(exc))
            return
        finally:
            self.server.cli_semaphore.release()

        elapsed = time.monotonic() - started
        payload = {
            "id": f"chatcmpl-cmd-{int(time.time())}",
            "object": "chat.completion",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "message": {"role": "assistant", "content": content},
                    "finish_reason": "stop",
                }
            ],
            "usage": {
                "prompt_tokens": 0,
                "completion_tokens": 0,
                "total_tokens": 0,
            },
            "cmd_gateway": {"elapsed_seconds": round(elapsed, 2)},
        }
        self._reply_json(payload)


def make_server(
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    model: str | None = None,
    *,
    allow_non_loopback: bool = False,
    max_concurrency: int = DEFAULT_MAX_CONCURRENCY,
    max_body_bytes: int = DEFAULT_MAX_BODY_BYTES,
) -> ThreadingHTTPServer:
    if not allow_non_loopback and not _is_loopback_host(host):
        raise ValueError(
            "CMD Gateway must bind to loopback; pass allow_non_loopback=True "
            "only for an explicit trusted deployment"
        )
    max_concurrency = max(1, min(int(max_concurrency), MAX_CONCURRENCY_LIMIT))
    max_body_bytes = max(1024, min(int(max_body_bytes), 2 * 1024 * 1024))
    server = ThreadingHTTPServer((host, port), _Handler)
    server.model = model or DEFAULT_MODEL
    server.service_name = SERVICE_NAME
    server.max_body_bytes = max_body_bytes
    server.cli_semaphore = threading.BoundedSemaphore(max_concurrency)
    server.max_concurrency = max_concurrency
    return server


def main(argv: list[str] | None = None) -> None:
    root = load_environment(Path(__file__).resolve().parent.parent)
    settings = get_settings(root, load_env=False)
    parser = argparse.ArgumentParser(
        prog="python -m app.cmd_gateway",
        description=(
            "Local OpenAI-compatible gateway over the Command Code CLI "
            "(default model deepseek/deepseek-v4-flash)."
        ),
    )
    parser.add_argument("--host", default=settings.cmd_gateway_host)
    parser.add_argument("--port", type=int, default=settings.cmd_gateway_port)
    parser.add_argument(
        "--model",
        default=os.getenv("CMD_GATEWAY_MODEL", DEFAULT_MODEL),
        help=f"Command Code model id (default: {DEFAULT_MODEL})",
    )
    parser.add_argument("--log-level", default=settings.log_level)
    parser.add_argument(
        "--allow-non-loopback", action="store_true",
        default=settings.cmd_gateway_allow_non_loopback,
        help="explicitly allow binding outside loopback",
    )
    parser.add_argument(
        "--max-concurrency", type=int,
        default=settings.cmd_gateway_max_concurrency,
    )
    parser.add_argument(
        "--max-body-bytes", type=int,
        default=settings.cmd_gateway_max_body_bytes,
    )
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = make_server(
        args.host, args.port, model=args.model,
        allow_non_loopback=args.allow_non_loopback,
        max_concurrency=args.max_concurrency,
        max_body_bytes=args.max_body_bytes,
    )
    logger.info(
        "cmd gateway listening on http://%s:%d (model=%s, cli=%s)",
        args.host,
        args.port,
        args.model,
        CMD_COMMAND,
    )
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
