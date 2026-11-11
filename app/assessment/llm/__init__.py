"""大模型 provider 适配层（OpenAI / Mock）。"""

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
from .base_provider import LLMProvider, ProviderResult
from .provider_factory import create_provider

__all__ = [
    "LLMProvider",
    "ProviderResult",
    "LLMConfigurationError",
    "LLMAuthenticationError",
    "LLMTimeoutError",
    "LLMRateLimitError",
    "LLMStructuredOutputError",
    "LLMProviderError",
    "DeepSeekEmptyContentError",
    "DeepSeekJSONParseError",
    "DeepSeekTruncatedOutputError",
    "create_provider",
]
