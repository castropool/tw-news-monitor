"""Ensure the local Command Code gateway is listening; start it if not.

Used by scheduled runs (run_monitor.bat) so the summarizer's LLM endpoint is
available in unattended scenarios without a separately registered task.

Cheap guard: skips the start attempt if one happened recently (configurable
via CMD_GATEWAY_SELF_HEAL_MINUTES, default 30) so a gateway that cannot start
is not respawned on every 30-minute monitor tick.

Exit codes: 0 = already up / started; 1 = could not start (caller may
continue - the summarizer is best-effort).
"""

from __future__ import annotations

import os
import json
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen
from pathlib import Path

from app.cmd_gateway import SERVICE_NAME
from app.settings import get_settings, load_environment

PROJECT_ROOT = Path(__file__).resolve().parent.parent
PYTHON_EXE = PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"
LOG_DIR = PROJECT_ROOT / "data" / "logs"
STAMP = PROJECT_ROOT / "data" / "logs" / ".cmd_gateway_heal_stamp"
load_environment(PROJECT_ROOT)
SETTINGS = get_settings(PROJECT_ROOT, load_env=False)
HOST = SETTINGS.cmd_gateway_host
PORT = SETTINGS.cmd_gateway_port
MODEL = os.getenv("CMD_GATEWAY_MODEL", "deepseek/deepseek-v4-flash")


def _healthy() -> bool:
    try:
        request = Request(f"http://{HOST}:{PORT}/health", method="GET")
        with urlopen(request, timeout=1.0) as response:
            payload = json.loads(response.read().decode("utf-8"))
        return (
            payload.get("status") == "ok"
            and payload.get("service") == SERVICE_NAME
        )
    except (OSError, URLError, ValueError, json.JSONDecodeError):
        return False


# Compatibility name for existing callers/tests; it now means a verified
# Gateway health response rather than a bare TCP listener.
_listening = _healthy


def _recent_stamp(minutes: int) -> bool:
    try:
        age = time.time() - STAMP.stat().st_mtime
        return age < minutes * 60
    except OSError:
        return False


def main() -> int:
    if _healthy():
        return 0
    heal_minutes = int(os.getenv("CMD_GATEWAY_SELF_HEAL_MINUTES", "30"))
    if _recent_stamp(heal_minutes):
        print(f"[cmd_gateway] not listening on {HOST}:{PORT}; recent heal attempt "
              f"<{heal_minutes}m ago, skipping", file=sys.stderr)
        return 1
    if not PYTHON_EXE.exists():
        print(f"[cmd_gateway] project python not found: {PYTHON_EXE}", file=sys.stderr)
        return 1
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = LOG_DIR / "cmd_gateway.log"
    STAMP.parent.mkdir(parents=True, exist_ok=True)
    STAMP.write_text(time.strftime("%Y-%m-%dT%H:%M:%S"), encoding="utf-8")
    # Detach from the scheduled task so the gateway keeps running after this
    # script exits (new console on Windows).
    argv = [
        str(PYTHON_EXE),
        "-m", "app.cmd_gateway",
        "--host", HOST,
        "--port", str(PORT),
        "--model", MODEL,
    ]
    if SETTINGS.cmd_gateway_allow_non_loopback:
        argv.append("--allow-non-loopback")
    creationflags = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0
    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            subprocess.Popen(
                argv,
                cwd=str(PROJECT_ROOT),
                creationflags=creationflags,
                stdin=subprocess.DEVNULL,
                stdout=log_file,
                stderr=subprocess.STDOUT,
                close_fds=True,
            )
    except OSError as exc:
        print(f"[cmd_gateway] failed to start gateway: {exc}", file=sys.stderr)
        return 1
    # Give it a moment, then verify.
    for _ in range(10):
        time.sleep(0.5)
        if _healthy():
            print(f"[cmd_gateway] started on {HOST}:{PORT} (model={MODEL})")
            return 0
    print(f"[cmd_gateway] launch attempted but not yet listening on "
          f"{HOST}:{PORT} (log: {log_path})", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
