"""统一交付接口与结果模型。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Protocol


@dataclass
class DeliveryResult:
    provider: str
    delivery_mode: str
    success: bool
    message_id: str = ""
    file_ids: list[str] = field(default_factory=list)
    delivered_at: str = ""
    attempt_count: int = 0
    duration_ms: int = 0
    warnings: list[str] = field(default_factory=list)
    network_calls: int = 0

    def to_dict(self) -> dict:
        return {
            "provider": self.provider,
            "delivery_mode": self.delivery_mode,
            "success": self.success,
            "message_id": self.message_id,
            "file_ids": list(self.file_ids),
            "delivered_at": self.delivered_at,
            "attempt_count": self.attempt_count,
            "duration_ms": self.duration_ms,
            "warnings": list(self.warnings),
            "network_calls": self.network_calls,
        }


class ReportDelivery(Protocol):
    def deliver(
        self,
        *,
        report_metadata: dict,
        summary_text: str,
        artifact_paths: list[str],
        delivery_context: dict,
    ) -> DeliveryResult:
        ...
