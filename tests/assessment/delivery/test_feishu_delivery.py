import json

import httpx
import pytest

from app.assessment.delivery import create_delivery
from app.assessment.delivery.errors import (
    DeliveryConfigurationError,
    DeliveryCredentialError,
    DeliveryServerError,
)
from app.assessment.delivery.feishu_delivery import FeishuDelivery


def _config(**overrides):
    base = {
        "delivery": {
            "enabled": True,
            "mode": "webhook_summary",
            "fallback_mode": "none",
            "webhook": {"env": "FEISHU_WEBHOOK"},
            "app": {
                "app_id_env": "FEISHU_APP_ID",
                "app_secret_env": "FEISHU_APP_SECRET",
                "chat_id_env": "FEISHU_CHAT_ID",
            },
            "feishu": {
                "timeout_seconds": 10,
                "max_attempts": 2,
                "send_summary": True,
                "send_artifact": False,
            },
        }
    }
    feishu_keys = {
        "send_summary",
        "send_artifact",
        "timeout_seconds",
        "max_attempts",
    }
    feishu_overrides = {k: v for k, v in overrides.items() if k in feishu_keys}
    top_level_overrides = {k: v for k, v in overrides.items() if k not in feishu_keys}
    base["delivery"]["feishu"].update(feishu_overrides)
    base["delivery"].update(top_level_overrides)
    return base


class TestFeishuDeliveryModes:
    def test_webhook_only_preflight_ready(self, monkeypatch):
        monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/h1")
        delivery = FeishuDelivery(_config())
        matrix = delivery.capability_matrix()
        assert matrix["configured_mode"] == "webhook_summary"
        assert matrix["delivery_preflight_ready"] is True
        assert matrix["file_delivery_supported"] is False

    def test_webhook_only_does_not_support_file_upload(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/h2")

        def fake_post(url, **kwargs):
            return httpx.Response(200, json={"code": 0})

        monkeypatch.setattr("app.assessment.delivery.feishu_delivery.httpx.post", fake_post)
        delivery = FeishuDelivery(_config(send_artifact=True))
        artifact = tmp_path / "x.docx"
        artifact.write_bytes(b"docx")
        result = delivery.deliver(
            report_metadata={},
            summary_text="s",
            artifact_paths=[str(artifact)],
            delivery_context={},
        )
        assert result.success is True
        assert result.file_ids == []
        assert any("file_delivery_supported=false" in w for w in result.warnings)

    def test_app_file_upload_does_not_require_webhook(self, monkeypatch, tmp_path):
        monkeypatch.setenv("FEISHU_APP_ID", "appid")
        monkeypatch.setenv("FEISHU_APP_SECRET", "appsecret")
        monkeypatch.setenv("FEISHU_CHAT_ID", "chat123")
        monkeypatch.delenv("FEISHU_WEBHOOK", raising=False)
        delivery = FeishuDelivery(_config(mode="app_file_upload", send_artifact=True))
        matrix = delivery.capability_matrix()
        assert matrix["configured_mode"] == "app_file_upload"
        assert matrix["delivery_preflight_ready"] is True
        assert matrix["webhook_summary_ready"] is False
        assert matrix["file_delivery_supported"] is True

        from app import feishu as feishu_mod

        monkeypatch.setattr(feishu_mod, "upload_file", lambda *a, **k: "file_key_1")

        def fake_post(url, **kwargs):
            if "tenant_access_token" in url:
                return httpx.Response(200, json={"code": 0, "tenant_access_token": "tok"})
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "msg-1"}})

        monkeypatch.setattr("app.assessment.delivery.feishu_delivery.httpx.post", fake_post)
        artifact = tmp_path / "report.docx"
        artifact.write_bytes(b"docx-data")
        result = delivery.deliver(
            report_metadata={},
            summary_text="s",
            artifact_paths=[str(artifact)],
            delivery_context={},
        )
        assert result.success is True
        assert result.file_ids == ["feishu-file-report.docx"]

    def test_app_credentials_incomplete_fails(self, monkeypatch):
        monkeypatch.setenv("FEISHU_APP_ID", "appid")
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        monkeypatch.delenv("FEISHU_CHAT_ID", raising=False)
        delivery = FeishuDelivery(_config(mode="app_file_upload"))
        matrix = delivery.capability_matrix()
        assert matrix["delivery_preflight_ready"] is False
        assert "FEISHU_APP_SECRET" in matrix["missing_environment_variables"]
        with pytest.raises(DeliveryCredentialError):
            delivery.deliver(
                report_metadata={},
                summary_text="",
                artifact_paths=[],
                delivery_context={},
            )

    def test_disabled_requires_explicit_config(self):
        # enabled 缺省为 true，缺凭据不等于关闭
        delivery = FeishuDelivery(_config(mode="app_file_upload"))
        assert delivery.configured_mode == "app_file_upload"
        delivery_disabled = FeishuDelivery(_config(enabled=False))
        assert delivery_disabled.configured_mode == "delivery_disabled"
        matrix = delivery_disabled.capability_matrix()
        assert matrix["delivery_preflight_ready"] is True

    def test_disabled_delivery_succeeds_without_network(self, tmp_path):
        delivery = FeishuDelivery(_config(enabled=False))
        result = delivery.deliver(
            report_metadata={},
            summary_text="s",
            artifact_paths=[],
            delivery_context={"receipt_path": str(tmp_path / "r.json")},
        )
        assert result.success is True
        assert result.delivery_mode == "disabled_by_configuration"
        assert result.network_calls == 0
        assert (tmp_path / "r.json").exists()

    def test_no_automatic_fallback(self, monkeypatch):
        monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/h3")
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        delivery = FeishuDelivery(
            _config(mode="app_file_upload", fallback_mode="webhook_summary")
        )
        with pytest.raises(DeliveryConfigurationError):
            delivery.deliver(
                report_metadata={},
                summary_text="",
                artifact_paths=[],
                delivery_context={},
            )

    def test_missing_credentials_not_treated_as_disabled(self):
        delivery = FeishuDelivery(_config(mode="webhook_summary"))
        assert delivery.configured_mode == "webhook_summary"
        assert delivery.capability_matrix()["delivery_preflight_ready"] is False

    def test_dry_run_no_network(self, tmp_path, monkeypatch):
        monkeypatch.delenv("FEISHU_WEBHOOK", raising=False)
        delivery = create_delivery("feishu", config=_config(), mode="dry_run")
        result = delivery.deliver(
            report_metadata={},
            summary_text="s",
            artifact_paths=[],
            delivery_context={"receipt_path": str(tmp_path / "receipt.json")},
        )
        assert result.success is True
        assert result.network_calls == 0
        assert "dry_run" in result.delivery_mode
        assert (tmp_path / "receipt.json").exists()

    def test_unknown_provider_rejected(self):
        with pytest.raises(DeliveryConfigurationError):
            create_delivery("unknown", mode="development")

    def test_production_mock_rejected(self):
        with pytest.raises(Exception):
            create_delivery("mock", mode="production")

    def test_webhook_retry_then_success(self, monkeypatch):
        monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/h4")
        calls = []

        def fake_post(url, **kwargs):
            calls.append(1)
            if len(calls) == 1:
                return httpx.Response(500, json={})
            return httpx.Response(200, json={"code": 0})

        monkeypatch.setattr("app.assessment.delivery.feishu_delivery.httpx.post", fake_post)
        result = FeishuDelivery(_config(max_attempts=2)).deliver(
            report_metadata={},
            summary_text="s",
            artifact_paths=[],
            delivery_context={},
        )
        assert result.success is True
        assert len(calls) == 2

    def test_webhook_exhausted_fails(self, monkeypatch):
        monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/h5")

        def fake_post(url, **kwargs):
            return httpx.Response(503, json={})

        monkeypatch.setattr("app.assessment.delivery.feishu_delivery.httpx.post", fake_post)
        with pytest.raises(DeliveryServerError):
            FeishuDelivery(_config(max_attempts=1)).deliver(
                report_metadata={},
                summary_text="s",
                artifact_paths=[],
                delivery_context={},
            )

    def test_webhook_not_in_receipt(self, tmp_path, monkeypatch):
        monkeypatch.setenv("FEISHU_WEBHOOK", "https://open.feishu.cn/open-apis/bot/v2/hook/supersecret999")

        def fake_post(url, **kwargs):
            return httpx.Response(200, json={"code": 0})

        monkeypatch.setattr("app.assessment.delivery.feishu_delivery.httpx.post", fake_post)
        delivery = FeishuDelivery(_config())
        result = delivery.deliver(
            report_metadata={},
            summary_text="s",
            artifact_paths=[],
            delivery_context={"receipt_path": str(tmp_path / "r.json")},
        )
        blob = json.dumps(result.to_dict()) + (tmp_path / "r.json").read_text(encoding="utf-8")
        assert "supersecret999" not in blob
        assert "Bearer" not in blob
