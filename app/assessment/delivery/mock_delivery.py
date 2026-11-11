"""确定性 Mock 飞书交付（无网络、无密钥）。"""

from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path
from typing import Any

from ..evidence_pack_builder import canonical_hash
from .base_delivery import DeliveryResult, ReportDelivery
from .errors import (
    DeliveryCredentialError,
    DeliveryRateLimitError,
    DeliveryServerError,
    DeliveryTimeoutError,
)


MOCK_DELIVERY_FIXTURES = (
    "success",
    "timeout",
    "rate_limit",
    "server_error",
    "invalid_credentials",
    "partial_success",
)


class MockDelivery:
    """Mock 交付：不发送任何网络请求。"""

    def __init__(self, fixture: str = "success"):
        if fixture not in MOCK_DELIVERY_FIXTURES:
            raise ValueError(f"未知 mock delivery fixture: {fixture}")
        self.fixture = fixture

    def deliver(
        self,
        *,
        report_metadata: dict,
        summary_text: str,
        artifact_paths: list[str],
        delivery_context: dict,
    ) -> DeliveryResult:
        if self.fixture == "timeout":
            raise DeliveryTimeoutError("mock delivery timeout")
        if self.fixture == "rate_limit":
            raise DeliveryRateLimitError("mock delivery rate limit")
        if self.fixture == "server_error":
            raise DeliveryServerError("mock delivery server error")
        if self.fixture == "invalid_credentials":
            raise DeliveryCredentialError("mock delivery invalid credentials")

        started = time.perf_counter()
        digest = canonical_hash(
            {
                "report_metadata": report_metadata,
                "summary_text": summary_text,
                "artifact_paths": [Path(p).name for p in artifact_paths],
            }
        )
        message_id = f"mock-msg-{digest[:16]}"
        file_ids = [f"mock-file-{Path(p).name}" for p in artifact_paths]
        warnings: list[str] = []
        if self.fixture == "partial_success":
            warnings.append("mock: 部分文件未上传（模拟）")
            file_ids = file_ids[:1]

        result = DeliveryResult(
            provider="mock",
            delivery_mode="mock",
            success=True,
            message_id=message_id,
            file_ids=file_ids,
            delivered_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            attempt_count=1,
            duration_ms=int((time.perf_counter() - started) * 1000),
            warnings=warnings,
            network_calls=0,
        )
        receipt_path = delivery_context.get("receipt_path")
        if receipt_path:
            path = Path(receipt_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(
                json.dumps(
                    {
                        "delivery_mode": "mock",
                        "message_digest": hashlib.sha256(
                            summary_text.encode("utf-8")
                        ).hexdigest(),
                        "file_names": [Path(p).name for p in artifact_paths],
                        "file_sizes": [
                            Path(p).stat().st_size if Path(p).exists() else -1
                            for p in artifact_paths
                        ],
                        "message_id": message_id,
                        "file_ids": file_ids,
                        "warnings": warnings,
                        "network_calls": 0,
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
                encoding="utf-8",
            )
        return result


def _build_summary(report_metadata: dict) -> dict:
    return {
        "title": report_metadata.get("title"),
        "period": report_metadata.get("period"),
        "report_status": report_metadata.get("report_status"),
        "generation_mode": report_metadata.get("generation_mode"),
    }
