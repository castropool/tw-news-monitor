"""Provider 异常类型。"""


class LLMProviderError(RuntimeError):
    """Provider 通用错误。"""

    def __init__(self, message: str, *, provider_error_code: str = "", provider_error_category: str = ""):
        super().__init__(message)
        self.provider_error_code = provider_error_code
        self.provider_error_category = provider_error_category


class LLMConfigurationError(LLMProviderError):
    """配置错误（密钥/模型缺失、非法 provider 等）。"""


class LLMAuthenticationError(LLMProviderError):
    """认证失败。"""


class LLMTimeoutError(LLMProviderError):
    """请求超时。"""


class LLMRateLimitError(LLMProviderError):
    """限流。"""


class LLMStructuredOutputError(LLMProviderError):
    """模型不支持或未返回严格结构化输出。"""


class DeepSeekEmptyContentError(LLMStructuredOutputError):
    """DeepSeek 返回空 content。"""


class DeepSeekJSONParseError(LLMStructuredOutputError):
    """DeepSeek JSON 解析失败。"""


class DeepSeekTruncatedOutputError(LLMStructuredOutputError):
    """DeepSeek 输出被截断（finish_reason=length）。"""
