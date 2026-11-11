"""Assessment LLM Adapter（research-driven 专用，provider/model/temperature/max_tokens 配置化）。

与旧 Claim-centric 路径的 ``app/assessment/llm`` Provider 层解耦：
旧 Provider 的请求信封强耦合旧输入契约（period_events/claims/allowed_reference_ids），
新路径只需要一次干净的 system+user JSON 调用，因此这里提供独立的薄适配层：

- provider: deepseek / openai / mock，由 config ``llm.research_driven`` 配置；
- model / temperature / max_output_tokens / timeout 均配置化，可用环境变量覆盖；
- mock 后端完全确定性（无网络、无密钥），用于单元测试与离线验收；
- 除 ``structured`` 输出外同时返回 ``raw_text``，便于审计与失败诊断。
"""

from __future__ import annotations

import json
import os
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from .mock_adapter import MockAdapter

try:  # 仅 deepseek/openai 需要 openai SDK；mock 路径不需要
    from openai import OpenAI
except Exception:  # pragma: no cover - import guard
    OpenAI = None  # type: ignore[assignment]


class AdapterError(RuntimeError):
    """Assessment LLM Adapter 调用失败。"""

    def __init__(self, message: str, *, category: str = "provider", retryable: bool = True):
        super().__init__(message)
        self.category = category
        self.retryable = retryable


@dataclass
class AdapterResult:
    provider: str
    model: str
    structured: dict | None = None
    raw_text: str = ""
    client_request_id: str = ""
    response_id: str = ""
    input_tokens: int | None = 0
    output_tokens: int | None = 0
    total_tokens: int | None = 0
    finish_status: str = ""
    request_duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "provider": self.provider,
            "model": self.model,
            "structured": self.structured,
            "raw_text": self.raw_text[:2000],
            "client_request_id": self.client_request_id,
            "response_id": self.response_id,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "total_tokens": self.total_tokens,
            "finish_status": self.finish_status,
            "request_duration_ms": self.request_duration_ms,
            "warnings": list(self.warnings),
        }


def resolve_research_llm_config(
    config: dict,
    provider: str | None = None,
    model: str | None = None,
) -> dict:
    """解析 research_driven LLM 配置（provider/model/temperature/max_output_tokens）。

    优先级：函数参数 > 环境变量 > config llm.research_driven > llm.deepseek 兜底。
    """
    llm = config.get("llm", {}) or {}
    rd = dict(llm.get("research_driven", {}) or {})
    ds = dict(llm.get("deepseek", {}) or {})

    resolved_provider = (provider or rd.get("provider") or llm.get("default_provider") or "deepseek").strip()
    api_key_env = rd.get("api_key_env") or ds.get("api_key_env") or "DEEPSEEK_API_KEY"
    model_env = rd.get("model_env") or ds.get("model_env") or "DEEPSEEK_MODEL"
    base_url = rd.get("base_url") or ds.get("base_url") or "https://api.deepseek.com"
    default_model = rd.get("default_model") or ds.get("default_model") or "deepseek-v4-flash"
    allowed_models = list(rd.get("allowed_models") or ds.get("allowed_models") or [default_model])
    resolved_model = (
        (model or os.getenv(model_env, "").strip()) or default_model
    )
    if resolved_provider != "mock" and allowed_models and resolved_model not in allowed_models:
        raise AdapterError(
            f"模型 {resolved_model} 不在允许列表 {sorted(allowed_models)}",
            category="configuration",
            retryable=False,
        )
    return {
        "provider": resolved_provider,
        "model": resolved_model,
        "api_key_env": api_key_env,
        "base_url": base_url,
        "temperature": float(rd.get("temperature", 0.7)),
        "max_output_tokens": int(
            rd.get("max_output_tokens") or ds.get("max_output_tokens") or 12000
        ),
        "timeout_seconds": float(
            rd.get("request_timeout_seconds") or ds.get("request_timeout_seconds") or 120
        ),
        "max_attempts": int(rd.get("max_attempts") or 2),
        "json_output": bool(rd.get("json_output", True)),
    }


class AssessmentLLMAdapter:
    """研究路径 LLM 适配器：一次 system+user 调用，返回结构化 JSON 或原文。"""

    def __init__(self, config: dict, provider: str | None = None, model: str | None = None):
        self.cfg = resolve_research_llm_config(config, provider=provider, model=model)
        self.provider = self.cfg["provider"]
        self.model = self.cfg["model"]
        self._client = None
        self._mock = None
        if self.provider == "mock":
            self._mock = MockAdapter()

    def _get_client(self):
        if self._client is not None:
            return self._client
        if self.provider not in ("deepseek", "openai"):
            raise AdapterError(f"不支持的 provider: {self.provider}", category="configuration", retryable=False)
        if OpenAI is None:
            raise AdapterError("openai SDK 未安装", category="configuration", retryable=False)
        api_key = os.getenv(self.cfg["api_key_env"], "").strip()
        if not api_key:
            raise AdapterError(
                f"缺少 API 密钥环境变量 {self.cfg['api_key_env']}",
                category="configuration",
                retryable=False,
            )
        self._client = OpenAI(
            api_key=api_key,
            base_url=self.cfg["base_url"],
            timeout=self.cfg["timeout_seconds"],
        )
        return self._client

    def complete(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        json_mode: bool = True,
        request_id: str | None = None,
    ) -> AdapterResult:
        """调用一次模型。json_mode=True 时强制 JSON 输出并解析为 dict。"""
        if self.provider == "mock":
            result = self._mock.complete(
                system_prompt=system_prompt,
                user_payload=user_payload,
                json_mode=json_mode,
            )
            return AdapterResult(
                provider="mock",
                model="mock-model",
                structured=result.get("structured"),
                raw_text=result.get("raw_text", ""),
                client_request_id=request_id or str(uuid.uuid4()),
                finish_status="completed",
                request_duration_ms=1,
            )

        client = self._get_client()
        client_request_id = request_id or str(uuid.uuid4())
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(user_payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
            },
        ]
        last_error: AdapterError | None = None
        for attempt in range(1, self.cfg["max_attempts"] + 1):
            started = time.monotonic()
            try:
                kwargs: dict[str, Any] = {
                    "model": self.model,
                    "messages": messages,
                    "stream": False,
                    "max_tokens": self.cfg["max_output_tokens"],
                    "temperature": self.cfg["temperature"],
                }
                if json_mode:
                    kwargs["response_format"] = {"type": "json_object"}
                if self.provider == "deepseek":
                    kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
                response = client.chat.completions.create(**kwargs)
            except Exception as exc:  # noqa: BLE001
                duration_ms = int((time.monotonic() - started) * 1000)
                name = type(exc).__name__
                if "AuthenticationError" in name:
                    last_error = AdapterError(f"认证失败: {exc}", category="authentication", retryable=False)
                elif "RateLimitError" in name:
                    last_error = AdapterError(f"限流: {exc}", category="rate_limit", retryable=True)
                elif "Timeout" in name or "APITimeoutError" in name:
                    last_error = AdapterError(f"请求超时({duration_ms}ms): {exc}", category="timeout", retryable=True)
                elif "BadRequestError" in name:
                    last_error = AdapterError(f"请求被拒绝: {exc}", category="request", retryable=False)
                else:
                    last_error = AdapterError(f"provider 错误: {exc}", category="provider", retryable=True)
                continue

            duration_ms = int((time.monotonic() - started) * 1000)
            choice = (response.choices or [None])[0]
            message = getattr(choice, "message", None) if choice else None
            content = getattr(message, "content", None) or ""
            finish_reason = getattr(choice, "finish_reason", "") if choice else ""
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
            output_tokens = getattr(usage, "completion_tokens", None) if usage else None
            total_tokens = getattr(usage, "total_tokens", None) if usage else None

            if not content.strip():
                last_error = AdapterError("模型返回空 content", category="empty_content", retryable=True)
                continue
            if finish_reason == "length":
                last_error = AdapterError("模型输出被截断（finish_reason=length）", category="truncated_output", retryable=True)
                continue
            if json_mode:
                try:
                    structured = _extract_json_object(content)
                except AdapterError as exc:
                    last_error = exc
                    continue
                if not isinstance(structured, dict):
                    last_error = AdapterError("模型输出不是 JSON 对象", category="json_output", retryable=True)
                    continue
            else:
                structured = None
            return AdapterResult(
                provider=self.provider,
                model=self.model,
                structured=structured,
                raw_text=content,
                client_request_id=client_request_id,
                response_id=getattr(response, "id", "") or "",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                total_tokens=total_tokens,
                finish_status=finish_reason or "",
                request_duration_ms=duration_ms,
            )
        if last_error is not None:
            raise last_error
        raise AdapterError("模型生成失败：超过最大尝试次数", category="provider", retryable=True)


def _extract_json_object(content: str) -> Any:
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
        # 兜底：提取第一个平衡的 JSON 对象
        start = text.find("{")
        if start == -1:
            raise AdapterError("模型输出中没有 JSON 对象", category="json_output", retryable=True)
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
        raise AdapterError("模型输出 JSON 解析失败", category="json_output", retryable=True)
