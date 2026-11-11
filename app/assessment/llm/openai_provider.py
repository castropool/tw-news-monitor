"""OpenAI Responses API Provider（严格 JSON Schema 结构化输出，无外部工具）。"""

from __future__ import annotations

import json
import os
import time
from typing import Any

from .base_provider import LLMProvider, ProviderResult
from .errors import (
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
    LLMStructuredOutputError,
    LLMTimeoutError,
)


class OpenAIProvider(LLMProvider):
    def __init__(self, config: dict | None = None, model_override: str | None = None):
        self.config = config or {}
        llm_cfg = self.config.get("llm", {}) or {}
        ocfg = llm_cfg.get("openai", {}) or {}
        self.api_key_env = ocfg.get("api_key_env", llm_cfg.get("api_key_env", "OPENAI_API_KEY"))
        self.model_env = ocfg.get("model_env", llm_cfg.get("model_env", "OPENAI_MODEL"))
        self.timeout = float(
            ocfg.get("request_timeout_seconds", llm_cfg.get("request_timeout_seconds", 120))
        )
        self.max_output_tokens = int(
            ocfg.get("max_output_tokens", llm_cfg.get("max_output_tokens", 12000))
        )
        self.model_override = model_override
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        key = os.getenv(self.api_key_env, "").strip()
        if not key:
            raise LLMConfigurationError(
                f"缺少 API 密钥环境变量 {self.api_key_env}（openai provider 需要）"
            )
        model = (self.model_override or os.getenv(self.model_env, "")).strip()
        if not model:
            raise LLMConfigurationError(
                f"缺少模型名（{self.model_env} 或 --model）"
            )
        try:
            from openai import OpenAI

            self._client = OpenAI(api_key=key, timeout=self.timeout)
            self._model = model
            return self._client
        except Exception as exc:
            raise LLMConfigurationError(f"初始化 OpenAI 客户端失败: {exc}") from exc

    def generate_structured_report(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        output_schema: dict,
        request_metadata: dict,
    ) -> ProviderResult:
        client = self._get_client()
        started = time.monotonic()
        user_text = json.dumps(user_payload, ensure_ascii=False, sort_keys=True)
        try:
            response = client.responses.create(
                model=self._model,
                input=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_text},
                ],
                text={
                    "format": {
                        "type": "json_schema",
                        "name": "tainan_assessment_report",
                        "schema": output_schema,
                        "strict": True,
                    }
                },
                tools=[],  # 不启用任何外部工具
                max_output_tokens=self.max_output_tokens,
            )
        except Exception as exc:
            duration_ms = int((time.monotonic() - started) * 1000)
            raise _map_openai_error(exc, duration_ms) from exc

        duration_ms = int((time.monotonic() - started) * 1000)
        usage = getattr(response, "usage", None) or {}
        try:
            structured = json.loads(response.output_text)
        except Exception as exc:
            raise LLMStructuredOutputError(f"模型输出不是合法 JSON: {exc}") from exc

        return ProviderResult(
            provider="openai",
            model=self._model,
            structured_output=structured,
            response_id=getattr(response, "id", "") or "",
            input_token_count=int(usage.get("input_tokens") or 0),
            output_token_count=int(usage.get("output_tokens") or 0),
            total_token_count=int(usage.get("total_tokens") or 0),
            finish_status=getattr(response, "status", "") or "",
            request_duration_ms=duration_ms,
            provider_warnings=[],
        )


def _map_openai_error(exc: Exception, duration_ms: int) -> LLMProviderError:
    module = type(exc).__module__
    name = type(exc).__name__
    if "AuthenticationError" in name:
        return LLMAuthenticationError(f"OpenAI 认证失败: {exc}")
    if "APITimeoutError" in name or "Timeout" in name:
        return LLMTimeoutError(f"OpenAI 请求超时（{duration_ms}ms）: {exc}")
    if "RateLimitError" in name:
        return LLMRateLimitError(f"OpenAI 限流: {exc}")
    if "BadRequestError" in name and ("schema" in str(exc).lower() or "structured" in str(exc).lower()):
        return LLMStructuredOutputError(f"OpenAI 不支持严格结构化输出: {exc}")
    return LLMProviderError(f"OpenAI provider 错误: {exc}")
