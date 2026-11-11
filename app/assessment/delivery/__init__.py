"""报告交付适配层（Mock / Feishu / dry-run）。"""

from .base_delivery import DeliveryResult, ReportDelivery
from .errors import (
    DeliveryConfigurationError,
    DeliveryCredentialError,
    DeliveryError,
    DeliveryPartialError,
    DeliveryRateLimitError,
    DeliveryServerError,
    DeliveryTimeoutError,
)
from .delivery_factory import create_delivery
from .mock_delivery import MockDelivery, MOCK_DELIVERY_FIXTURES

__all__ = [
    "ReportDelivery",
    "DeliveryResult",
    "DeliveryError",
    "DeliveryConfigurationError",
    "DeliveryCredentialError",
    "DeliveryPartialError",
    "DeliveryRateLimitError",
    "DeliveryServerError",
    "DeliveryTimeoutError",
    "create_delivery",
    "MockDelivery",
    "MOCK_DELIVERY_FIXTURES",
]
