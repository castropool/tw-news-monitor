"""Command Code Provider API 适配层（OpenAI 兼容，Bearer 认证）。

基于官方 Provider API：https://api.commandcode.ai/provider/v1/chat/completions
认证使用 ~/.commandcode/auth.json 中的 apiKey（Command Code 订阅账号）。

模型 id 示例：
- deepseek/deepseek-v4-pro    （架构师：高性能）
- deepseek/deepseek-v4-flash  （执行者：性价比高）

成本：订阅 credits 计费，分 off-peak（17h/天）与 peak（7h/天）两档。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

try:
    import urllib.request
except Exception:  # pragma: no cover
    urllib_request = None  # type: ignore[assignment]


class RouterError(RuntimeError):
    def __init__(self, message: str, *, category: str = "provider", retryable: bool = True):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


@dataclass
class ChatResult:
    provider: str
    model: str
    content: str
    client_request_id: str = ""
    response_id: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    total_tokens: int = 0
    finish_status: str = ""
    request_duration_ms: int = 0
    cost_usd_estimate: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "content": self.content,
            "client_request_id": self.client_request_id,
            "response_id": self.response_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "finish_status": self.finish_status,
            "request_duration_ms": self.request_duration_ms,
            "cost_usd_estimate": self.cost_usd_estimate,
        }


def resolve_api_key(explicit: str | None = None) -> str:
    """API key 优先级：显式传入 > 环境变量 CMD_API_KEY > ~/.commandcode/auth.json。"""
    if explicit and explicit.strip():
        return explicit.strip()
    env = os.getenv("CMD_API_KEY", "").strip()
    if env:
        return env
    auth_path = Path.home() / ".commandcode" / "auth.json"
    if auth_path.exists():
        try:
            data = json.loads(auth_path.read_text(encoding="utf-8"))
            key = str(data.get("apiKey") or "").strip()
            if key:
                return key
        except Exception:  # noqa: BLE001
            pass
    raise RouterError(
        "未找到 Command Code API key：请设置环境变量 CMD_API_KEY 或登录 ~/.commandcode/auth.json",
        category="configuration",
        retryable=False,
    )


class CommandCodeProvider:
    """Command Code Provider API 客户端（OpenAI Chat Completions 兼容）。"""

    BASE_URL = "https://api.commandcode.ai/provider/v1/chat/completions"
    PROVIDER_NAME = "command-code"

    def __init__(
        self,
        api_key: str | None = None,
        timeout_seconds: float = 180.0,
        max_attempts: int = 2,
    ):
        self.api_key = resolve_api_key(api_key)
        self.timeout = timeout_seconds
        self.max_attempts = max_attempts

    def chat(
        self,
        *,
        model: str,
        messages: list[dict],
        max_tokens: int = 4096,
        temperature: float | None = None,
        json_mode: bool = False,
    ) -> ChatResult:
        """一次 chat 调用。json_mode=True 时请求 JSON 输出（response_format）。"""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        if json_mode:
            payload["response_format"] = {"type": "json_object"}
        last_error: RouterError | None = None
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            req = urllib.request.Request(
                self.BASE_URL,
                data=body,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                method="POST",
            )
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
            except Exception as exc:  # noqa: BLE001
                duration = int((time.monotonic() - started) * 1000)
                name = type(exc).__name__
                detail = ""
                if hasattr(exc, "read"):
                    try:
                        detail = exc.read().decode("utf-8")[:400]  # type: ignore[union-attr]
                    except Exception:  # noqa: BLE001
                        detail = ""
                if "401" in str(exc) or "Authentication" in str(exc):
                    last_error = RouterError(f"认证失败: {exc}", category="authentication", retryable=False)
                elif "429" in str(exc) or "Rate" in str(exc):
                    last_error = RouterError(f"限流: {exc} {detail}", category="rate_limit", retryable=True)
                elif "Timeout" in name or "timeout" in str(exc).lower():
                    last_error = RouterError(f"请求超时({duration}ms)", category="timeout", retryable=True)
                else:
                    last_error = RouterError(f"provider 错误({name}): {exc} {detail}", category="provider", retryable=True)
                continue

            duration = int((time.monotonic() - started) * 1000)
            if "error" in data:
                err = data["error"]
                msg = str(err.get("message") or err)
                code = str(err.get("code") or "")
                if "authentication" in code.lower() or "401" in code:
                    last_error = RouterError(f"认证失败: {msg}", category="authentication", retryable=False)
                elif "rate_limit" in code.lower() or "429" in code:
                    last_error = RouterError(f"限流: {msg}", category="rate_limit", retryable=True)
                else:
                    last_error = RouterError(f"provider 错误: {msg}", category="provider", retryable="invalid_request" not in code.lower())
                continue

            choice = (data.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = str(message.get("content") or "")
            finish = str(choice.get("finish_reason") or "")
            usage = data.get("usage") or {}
            client_request_id = str(uuid.uuid4())
            result = ChatResult(
                provider=self.PROVIDER_NAME,
                model=str(data.get("model") or model),
                content=content,
                client_request_id=client_request_id,
                response_id=str(data.get("id") or ""),
                input_tokens=int(usage.get("prompt_tokens") or 0),
                output_tokens=int(usage.get("completion_tokens") or 0),
                total_tokens=int(usage.get("total_tokens") or 0),
                finish_status=finish,
                request_duration_ms=duration,
            )
            return result
        if last_error is not None:
            raise last_error
        raise RouterError("调用失败：超过最大尝试次数", category="provider", retryable=True)


def extract_json_object(content: str) -> Any:
    """从模型输出中提取 JSON 对象（容忍围栏/前后缀）。"""
    text = content.strip()
    if text.startswith("\ufeff"):
        text = text.lstrip("\ufeff").strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip().startswith("```"):
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except Exception:
        start = text.find("{")
        if start == -1:
            raise RouterError("模型输出中没有 JSON 对象", category="json_output", retryable=False)
        depth = 0
        in_str = False
        escape = False
        for idx in range(start, len(text)):
            ch = text[idx]
            if in_str:
                if escape:
                    escape = False
                elif ch == "\\":
                    escape = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(text[start : idx + 1])
                    except Exception:
                        break
        raise RouterError("模型输出 JSON 解析失败", category="json_output", retryable=False)
