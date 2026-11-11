"""Provider 工厂。"""

from __future__ import annotations

from .base_provider import LLMProvider
from .errors import LLMConfigurationError
from .deepseek_provider import DeepSeekProvider
from .mock_provider import MockProvider
from .openai_provider import OpenAIProvider


REGISTERED_PROVIDERS = ("mock", "deepseek", "openai")


def create_provider(
    provider: str,
    *,
    config: dict | None = None,
    model: str | None = None,
    fixture: str | None = None,
    thinking_mode: str = "disabled",
) -> LLMProvider:
    config = config or {}
    if provider == "mock":
        return MockProvider(model=model or "mock-model", fixture=fixture or "valid_draft_with_gap")
    if provider == "deepseek":
        return DeepSeekProvider(config=config, model_override=model, thinking_mode=thinking_mode)
    if provider == "openai":
        return OpenAIProvider(config=config, model_override=model)
    raise LLMConfigurationError(
        f"未注册的 provider: {provider!r}；可用：{list(REGISTERED_PROVIDERS)}"
    )
