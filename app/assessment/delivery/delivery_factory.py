"""交付工厂：mock / feishu / dry-run 包装。"""

from __future__ import annotations

import json
import time
from pathlib import Path

from .base_delivery import DeliveryResult, ReportDelivery
from .errors import DeliveryConfigurationError, DeliveryCredentialError
from .feishu_delivery import FeishuDelivery
from .mock_delivery import MockDelivery


REGISTERED_DELIVERY_PROVIDERS = ("mock", "feishu")


class DryRunFeishuDelivery:
    """dry_run 模式飞书适配：校验配置但绝不发送网络请求。"""

    def __init__(self, config: dict | None = None):
        self._inner = FeishuDelivery(config)

    def deliver(
        self,
        *,
        report_metadata: dict,
        summary_text: str,
        artifact_paths: list[str],
        delivery_context: dict,
    ) -> DeliveryResult:
        warnings = ["dry_run: 未发送任何网络请求"]
        matrix = self._inner.capability_matrix()
        if matrix["configured_mode"] == "delivery_disabled":
            warnings.append("delivery: 配置为显式关闭（dry_run 仅记录）")
        elif not matrix["delivery_preflight_ready"]:
            warnings.append(
                "飞书交付模式凭据不完整（dry_run 仅记录，不视为失败）："
                + ",".join(matrix["missing_environment_variables"])
            )
        result = DeliveryResult(
            provider="feishu",
            delivery_mode="dry_run:" + matrix["configured_mode"],
            success=True,
            message_id="",
            file_ids=[],
            delivered_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            attempt_count=1,
            duration_ms=0,
            warnings=warnings,
            network_calls=0,
        )
        result.warnings = warnings
        _write_receipt(delivery_context, result, summary_text, artifact_paths)
        return result


def create_delivery(
    provider: str,
    *,
    config: dict | None = None,
    mode: str = "development",
    fixture: str | None = None,
) -> ReportDelivery:
    config = config or {}
    if provider not in REGISTERED_DELIVERY_PROVIDERS:
        raise DeliveryConfigurationError(
            f"未注册的 delivery provider: {provider!r}；可用：{list(REGISTERED_DELIVERY_PROVIDERS)}"
        )
    if mode == "production" and provider == "mock":
        raise DeliveryConfigurationError("production 不得使用 Mock delivery")
    if provider == "mock":
        return MockDelivery(fixture=fixture or "success")
    if mode == "production":
        return FeishuDelivery(config)
    return DryRunFeishuDelivery(config)


def _write_receipt(
    delivery_context: dict,
    result: DeliveryResult,
    summary_text: str,
    artifact_paths: list[str],
) -> None:
    receipt_path = delivery_context.get("receipt_path")
    if not receipt_path:
        return
    path = Path(receipt_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = result.to_dict()
    payload["message_digest"] = __import__("hashlib").sha256(
        summary_text.encode("utf-8")
    ).hexdigest()
    payload["file_names"] = [Path(p).name for p in artifact_paths]
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
