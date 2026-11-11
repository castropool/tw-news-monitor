import json

import pytest

from app.assessment.delivery import (
    MockDelivery,
    create_delivery,
)
from app.assessment.delivery.errors import (
    DeliveryCredentialError,
    DeliveryRateLimitError,
    DeliveryServerError,
    DeliveryTimeoutError,
)


def _ctx(tmp_path):
    return {"receipt_path": str(tmp_path / "mock_delivery_receipt.json")}


class TestMockDelivery:
    def test_success(self, tmp_path):
        result = MockDelivery("success").deliver(
            report_metadata={"title": "t"},
            summary_text="摘要",
            artifact_paths=[],
            delivery_context=_ctx(tmp_path),
        )
        assert result.success is True
        assert result.provider == "mock"
        assert result.message_id.startswith("mock-msg-")
        receipt = json.loads((tmp_path / "mock_delivery_receipt.json").read_text(encoding="utf-8"))
        assert receipt["delivery_mode"] == "mock"
        assert receipt["network_calls"] == 0

    def test_timeout(self, tmp_path):
        with pytest.raises(DeliveryTimeoutError):
            MockDelivery("timeout").deliver(
                report_metadata={},
                summary_text="",
                artifact_paths=[],
                delivery_context=_ctx(tmp_path),
            )

    def test_rate_limit(self, tmp_path):
        with pytest.raises(DeliveryRateLimitError):
            MockDelivery("rate_limit").deliver(
                report_metadata={},
                summary_text="",
                artifact_paths=[],
                delivery_context=_ctx(tmp_path),
            )

    def test_server_error(self, tmp_path):
        with pytest.raises(DeliveryServerError):
            MockDelivery("server_error").deliver(
                report_metadata={},
                summary_text="",
                artifact_paths=[],
                delivery_context=_ctx(tmp_path),
            )

    def test_invalid_credentials(self, tmp_path):
        with pytest.raises(DeliveryCredentialError):
            MockDelivery("invalid_credentials").deliver(
                report_metadata={},
                summary_text="",
                artifact_paths=[],
                delivery_context=_ctx(tmp_path),
            )

    def test_partial_success(self, tmp_path):
        artifact = tmp_path / "a.docx"
        artifact.write_bytes(b"docx")
        result = MockDelivery("partial_success").deliver(
            report_metadata={},
            summary_text="",
            artifact_paths=[str(artifact)],
            delivery_context=_ctx(tmp_path),
        )
        assert result.success is True
        assert result.warnings
        assert result.file_ids

    def test_unknown_fixture_rejected(self):
        with pytest.raises(ValueError):
            MockDelivery("unknown")

    def test_factory_development_default_mock(self, tmp_path):
        delivery = create_delivery("mock", mode="development")
        result = delivery.deliver(
            report_metadata={},
            summary_text="s",
            artifact_paths=[],
            delivery_context=_ctx(tmp_path),
        )
        assert result.success is True
        assert result.network_calls == 0

    def test_factory_production_mock_rejected(self):
        with pytest.raises(Exception):
            create_delivery("mock", mode="production")
