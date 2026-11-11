"""DeepSeek Provider（OpenAI 兼容 Chat Completions + JSON Output）。"""

from __future__ import annotations

import json
import hashlib
import os
import time
import uuid
from typing import Any

from .base_provider import LLMProvider, ProviderResult
from .errors import (
    DeepSeekEmptyContentError,
    DeepSeekJSONParseError,
    DeepSeekTruncatedOutputError,
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMProviderError,
    LLMRateLimitError,
    LLMStructuredOutputError,
    LLMTimeoutError,
)
from .provider_output_normalizer import OutputNormalizationError, normalize_json_object


LEGACY_MODELS = {"deepseek-chat", "deepseek-reasoner"}


def _business_hash(value: Any) -> str:
    serialized = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def build_deepseek_request_envelope(user_payload: dict, output_schema: dict) -> dict:
    """Keep the v1.0 input contract intact and transmit the loaded v1.1 schema."""
    schema_version = (
        (((output_schema.get("properties") or {}).get("schema_version") or {}).get("const"))
        or "unspecified"
    )
    events = list(user_payload.get("period_events") or []) + list(
        user_payload.get("background_events") or []
    )
    polls = list(user_payload.get("polls") or [])
    allowed_event_ids = sorted(
        event["event_id"] for event in events if event.get("event_id")
    )
    allowed_poll_ids = sorted(
        poll["poll_id"] for poll in polls if poll.get("poll_id")
    )
    allowed_source_ids = sorted(
        source["source_id"]
        for source in (user_payload.get("sources") or [])
        if source.get("source_id")
    )
    allowed_gap_ids = sorted(
        gap.get("gap_id") or gap.get("stable_gap_id")
        for gap in (user_payload.get("coverage_gaps") or [])
        if gap.get("gap_id") or gap.get("stable_gap_id")
    )
    allowed_dimensions = sorted(
        dimension["dimension"]
        for dimension in ((user_payload.get("state_diff") or {}).get("dimensions") or [])
        if dimension.get("dimension")
    )
    data_status = user_payload.get("data_status") or {}
    eligibility = user_payload.get("generation_eligibility") or {}
    mandatory_disclosure_texts = list(
        eligibility.get("required_disclosures") or []
    )
    poll_cutoff = str(data_status.get("poll_cutoff") or "")
    if poll_cutoff:
        mandatory_disclosure_texts.extend(
            [f"正式民调截止至 {poll_cutoff}", "本期没有新增正式民调"]
        )
    return {
        "request_type": "tainan_assessment_report",
        "input_contract": user_payload,
        "output_contract": {
            "schema_name": output_schema.get("title") or "structured_json_object",
            "schema_version": schema_version,
            "strict": True,
            "json_schema": output_schema,
        },
        "allowed_reference_ids": {
            "event_ids": allowed_event_ids,
            "poll_ids": allowed_poll_ids,
            "source_ids": allowed_source_ids,
            "gap_ids": allowed_gap_ids,
            "snapshot_dimensions": allowed_dimensions,
        },
        "mandatory_disclosure_texts": mandatory_disclosure_texts,
        "request_contract_rules": [
            "Only IDs listed in allowed_reference_ids may appear in claims.",
            "required_disclosures must contain claim IDs, never disclosure text.",
            "Every data_disclosure claim ID must appear in required_disclosures.",
            "For each substantive claim, supporting_source_ids must be the flat union needed to cover every cited event/poll; each source must link to at least one cited event/poll and every cited event/poll must have at least one listed source.",
            "A forward_outlook claim must cite at least two event or poll IDs and use explicit forecast language.",
            "Each claim must be atomic and claim strength must not exceed evidence strength.",
            "Actor statements and allegations must retain speaker attribution and may not be promoted to objective facts without independent observed_fact evidence.",
        ],
    }


class DeepSeekProvider(LLMProvider):
    def __init__(
        self,
        config: dict | None = None,
        model_override: str | None = None,
        thinking_mode: str = "disabled",
        client: Any = None,
    ):
        self.config = config or {}
        llm_cfg = self.config.get("llm", {}) or {}
        self.cfg = llm_cfg.get("deepseek", {}) or {}
        self.api_key_env = self.cfg.get("api_key_env", "DEEPSEEK_API_KEY")
        self.model_env = self.cfg.get("model_env", "DEEPSEEK_MODEL")
        self.base_url = self.cfg.get("base_url", "https://api.deepseek.com")
        self.default_model = self.cfg.get("default_model", "deepseek-v4-flash")
        self.allowed_models = set(self.cfg.get("allowed_models") or [self.default_model])
        self.timeout = float(self.cfg.get("request_timeout_seconds", 120))
        self.max_output_tokens = int(self.cfg.get("max_output_tokens", 12000))
        self.max_attempts = int(self.cfg.get("max_generation_attempts", 2))
        self.thinking_mode = thinking_mode
        if thinking_mode not in ("enabled", "disabled"):
            raise LLMConfigurationError(f"thinking_mode 必须为 enabled/disabled：{thinking_mode!r}")
        self.model_override = model_override
        self._client = client
        self._resolved_model: str | None = None

    def _resolve_model(self) -> str:
        if self._resolved_model:
            return self._resolved_model
        model = (
            (self.model_override or os.getenv(self.model_env, "")).strip()
            or self.default_model
        )
        if model in LEGACY_MODELS:
            raise LLMConfigurationError(
                f"已弃用模型名 {model}；请使用 deepseek-v4-flash 或 deepseek-v4-pro",
                provider_error_code="legacy_model",
                provider_error_category="configuration",
            )
        if model not in self.allowed_models:
            raise LLMConfigurationError(
                f"模型 {model} 不在允许列表 {sorted(self.allowed_models)}",
                provider_error_code="model_not_allowed",
                provider_error_category="configuration",
            )
        self._resolved_model = model
        return model

    def _get_client(self):
        if self._client is not None:
            return self._client
        key = os.getenv(self.api_key_env, "").strip()
        if not key:
            raise LLMConfigurationError(
                f"缺少 API 密钥环境变量 {self.api_key_env}（deepseek provider 需要）",
                provider_error_code="missing_api_key",
                provider_error_category="configuration",
            )
        try:
            from openai import OpenAI

            self._client = OpenAI(api_key=key, base_url=self.base_url, timeout=self.timeout)
            return self._client
        except Exception as exc:
            raise LLMConfigurationError(f"初始化 DeepSeek 客户端失败: {exc}") from exc

    def generate_structured_report(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        output_schema: dict,
        request_metadata: dict,
    ) -> ProviderResult:
        model = self._resolve_model()
        client = self._get_client()
        client_request_id = str(
            request_metadata.get("client_request_id") or uuid.uuid4()
        )
        request_envelope = build_deepseek_request_envelope(user_payload, output_schema)
        output_schema_business_hash = _business_hash(output_schema)
        messages = [
            {"role": "system", "content": system_prompt},
            {
                "role": "user",
                "content": json.dumps(
                    request_envelope,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            },
        ]
        last_error: LLMProviderError | None = None
        for attempt in range(1, self.max_attempts + 1):
            started = time.monotonic()
            try:
                response = client.chat.completions.create(
                    model=model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    stream=False,
                    max_tokens=self.max_output_tokens,
                    extra_body={"thinking": {"type": self.thinking_mode}},
                )
            except Exception as exc:
                duration_ms = int((time.monotonic() - started) * 1000)
                raise _map_deepseek_error(exc, duration_ms) from exc

            duration_ms = int((time.monotonic() - started) * 1000)
            choice = (response.choices or [None])[0]
            message = getattr(choice, "message", None) if choice else None
            content = getattr(message, "content", None) or ""
            # reasoning_content 一律不保存、不进入任何输出
            finish_reason = getattr(choice, "finish_reason", "") if choice else ""
            usage = getattr(response, "usage", None)
            input_tokens = getattr(usage, "prompt_tokens", None) if usage else None
            output_tokens = getattr(usage, "completion_tokens", None) if usage else None
            total_tokens = getattr(usage, "total_tokens", None) if usage else None
            cache_hit = getattr(usage, "prompt_cache_hit_tokens", None) if usage else None
            cache_miss = getattr(usage, "prompt_cache_miss_tokens", None) if usage else None

            if not content.strip():
                last_error = DeepSeekEmptyContentError(
                    "DeepSeek 返回空 content",
                    provider_error_code="empty_content",
                    provider_error_category="empty_content",
                )
                continue
            if finish_reason == "length":
                last_error = DeepSeekTruncatedOutputError(
                    "DeepSeek 输出被截断（finish_reason=length）",
                    provider_error_code="truncated",
                    provider_error_category="truncated_output",
                )
                continue
            try:
                normalized = normalize_json_object(content)
                structured = normalized.value
            except OutputNormalizationError as exc:
                last_error = DeepSeekJSONParseError(
                    f"DeepSeek JSON 解析失败: {exc}",
                    provider_error_code="json_parse_error",
                    provider_error_category="json_output",
                )
                continue
            if not isinstance(structured, dict):
                last_error = DeepSeekJSONParseError(
                    "DeepSeek 输出不是 JSON 对象",
                    provider_error_code="json_not_object",
                    provider_error_category="json_output",
                )
                continue

            provider_request_id = getattr(response, "id", "") or ""
            return ProviderResult(
                provider="deepseek",
                model=model,
                structured_output=structured,
                client_request_id=client_request_id,
                response_id=provider_request_id,
                provider_request_id_supported=bool(provider_request_id),
                input_token_count=input_tokens,
                output_token_count=output_tokens,
                total_token_count=total_tokens,
                prompt_cache_hit_tokens=cache_hit,
                prompt_cache_miss_tokens=cache_miss,
                finish_status=finish_reason or "",
                request_duration_ms=duration_ms,
                provider_warnings=[
                    f"provider_output_normalization:{operation}"
                    for operation in normalized.operations
                ],
                request_audit={
                    "client_request_id": client_request_id,
                    "provider_request_id": provider_request_id or None,
                    "provider_request_id_supported": bool(provider_request_id),
                    "request_envelope_version": "deepseek_contract_envelope_v1",
                    "input_contract_version": str(user_payload.get("contract_version") or ""),
                    "report_output_schema_version": str(
                        request_envelope["output_contract"]["schema_version"]
                    ),
                    "output_schema_business_hash": output_schema_business_hash,
                    "output_schema_serialized_to_request": True,
                    "response_format": "json_object",
                    "native_json_schema": False,
                    "server_side_strict_schema": False,
                    "stream": False,
                    "thinking_mode": self.thinking_mode,
                    "normalization_operations": list(normalized.operations),
                },
            )
        if last_error is not None:
            raise last_error
        raise LLMProviderError("DeepSeek 生成失败：超过最大尝试次数")


def _map_deepseek_error(exc: Exception, duration_ms: int) -> LLMProviderError:
    name = type(exc).__name__
    if "AuthenticationError" in name:
        return LLMAuthenticationError(
            f"DeepSeek 认证失败: {exc}",
            provider_error_code="authentication_error",
            provider_error_category="authentication",
        )
    if "APITimeoutError" in name or "Timeout" in name:
        return LLMTimeoutError(
            f"DeepSeek 请求超时（{duration_ms}ms）: {exc}",
            provider_error_code="timeout",
            provider_error_category="timeout",
        )
    if "RateLimitError" in name:
        return LLMRateLimitError(
            f"DeepSeek 限流: {exc}",
            provider_error_code="rate_limit",
            provider_error_category="rate_limit",
        )
    if "BadRequestError" in name:
        return LLMStructuredOutputError(
            f"DeepSeek 请求被拒绝: {exc}",
            provider_error_code="bad_request",
            provider_error_category="request",
        )
    return LLMProviderError(
        f"DeepSeek provider 错误: {exc}",
        provider_error_code=name,
        provider_error_category="provider",
    )
