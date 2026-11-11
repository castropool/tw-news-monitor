"""飞书/交付适配层异常（不保存任何凭据或完整地址）。"""

from __future__ import annotations


class DeliveryError(RuntimeError):
    """交付失败基类。"""


class DeliveryConfigurationError(DeliveryError):
    """交付配置错误。"""


class DeliveryCredentialError(DeliveryError):
    """交付凭据缺失或认证失败。"""


class DeliveryTimeoutError(DeliveryError):
    """交付网络超时。"""


class DeliveryRateLimitError(DeliveryError):
    """交付限流。"""


class DeliveryServerError(DeliveryError):
    """交付服务端 5xx 错误。"""


class DeliveryPartialError(DeliveryError):
    """交付部分失败。"""
