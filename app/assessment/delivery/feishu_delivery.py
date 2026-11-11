"""飞书交付适配层（三种交付模式：webhook_summary / app_file_upload / delivery_disabled）。

设计约束：
- app_file_upload 不要求 Webhook 同时存在；
- 凭据不完整时不得自动降级（fallback_mode=none 为默认，本轮不实现自动 fallback）；
- delivery 必须显式启用（delivery.enabled=true），缺凭据不等于关闭交付；
- 不记录 Webhook 完整地址、App Secret 或 Authorization Header。
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from pathlib import Path
from typing import Any

import httpx

from .base_delivery import DeliveryResult, ReportDelivery
from .errors import (
    DeliveryConfigurationError,
    DeliveryCredentialError,
    DeliveryRateLimitError,
    DeliveryServerError,
    DeliveryTimeoutError,
)


DELIVERY_MODES = ("webhook_summary", "app_file_upload", "delivery_disabled")


class FeishuDelivery:
    """真实飞书交付。"""

    def __init__(self, config: dict | None = None):
        config = config or {}
        delivery = config.get("delivery") or {}
        feishu_cfg = delivery.get("feishu") or {}
        webhook_cfg = delivery.get("webhook") or {}
        app_cfg = delivery.get("app") or {}
        self._enabled = bool(delivery.get("enabled", True))
        self._mode = delivery.get("mode") or feishu_cfg.get("mode") or "webhook_summary"
        if self._mode not in DELIVERY_MODES:
            raise DeliveryConfigurationError(f"未知飞书交付模式: {self._mode!r}")
        self._fallback_mode = delivery.get("fallback_mode") or "none"
        self._timeout = float(
            feishu_cfg.get("timeout_seconds", delivery.get("timeout_seconds", 30))
        )
        self._max_attempts = int(
            feishu_cfg.get("max_attempts", delivery.get("max_attempts", 2))
        )
        self._send_summary = bool(
            feishu_cfg.get("send_summary", delivery.get("send_summary", True))
        )
        self._send_artifact = bool(
            feishu_cfg.get("send_artifact", delivery.get("send_artifact", False))
        )

        webhook_env = (
            webhook_cfg.get("env")
            or feishu_cfg.get("webhook_env")
            or "FEISHU_WEBHOOK"
        )
        self._webhook = os.getenv(webhook_env) or os.getenv("FEISHU_WEBHOOK_URL") or ""
        app_id_env = (
            app_cfg.get("app_id_env")
            or feishu_cfg.get("app_id_env")
            or "FEISHU_APP_ID"
        )
        app_secret_env = (
            app_cfg.get("app_secret_env")
            or feishu_cfg.get("app_secret_env")
            or "FEISHU_APP_SECRET"
        )
        chat_id_env = (
            app_cfg.get("chat_id_env")
            or feishu_cfg.get("chat_id_env")
            or "FEISHU_CHAT_ID"
        )
        self._env_names = {
            "webhook": webhook_env,
            "app_id": app_id_env,
            "app_secret": app_secret_env,
            "chat_id": chat_id_env,
        }
        self._app_id = os.getenv(app_id_env) or ""
        self._app_secret = os.getenv(app_secret_env) or ""
        self._chat_id = os.getenv(chat_id_env) or ""
        self._has_webhook = bool(self._webhook)
        self._has_app_credentials = bool(self._app_id and self._app_secret and self._chat_id)

    @property
    def configured_mode(self) -> str:
        if not self._enabled:
            return "delivery_disabled"
        return self._mode

    @property
    def file_delivery_supported(self) -> bool:
        return self._enabled and self._mode == "app_file_upload" and self._has_app_credentials

    @property
    def webhook_available(self) -> bool:
        return self._has_webhook

    def capability_matrix(self) -> dict:
        mode = self.configured_mode
        missing = []
        if mode == "webhook_summary" and not self._has_webhook:
            missing.append(self._env_names["webhook"])
        if mode == "app_file_upload":
            if not self._app_id:
                missing.append(self._env_names["app_id"])
            if not self._app_secret:
                missing.append(self._env_names["app_secret"])
            if not self._chat_id:
                missing.append(self._env_names["chat_id"])
        ready = (
            mode == "delivery_disabled"
            or (mode == "webhook_summary" and self._has_webhook)
            or (mode == "app_file_upload" and self._has_app_credentials)
        )
        return {
            "delivery_enabled": self._enabled,
            "configured_mode": mode,
            "fallback_mode": self._fallback_mode,
            "webhook_summary_ready": self._has_webhook,
            "app_file_upload_ready": self._has_app_credentials,
            "file_delivery_supported": self.file_delivery_supported,
            "delivery_preflight_ready": ready,
            "missing_environment_variables": sorted(missing),
        }

    def _check_mode_ready(self) -> None:
        mode = self.configured_mode
        if mode == "delivery_disabled":
            return
        if self._fallback_mode != "none":
            raise DeliveryConfigurationError(
                "fallback_mode 未实现；本轮禁止自动降级"
            )
        if mode == "webhook_summary" and not self._has_webhook:
            raise DeliveryCredentialError(
                f"webhook_summary 模式缺少 {self._env_names['webhook']}"
            )
        if mode == "app_file_upload":
            if not self._has_app_credentials:
                raise DeliveryCredentialError(
                    "app_file_upload 模式缺少 "
                    f"{self._env_names['app_id']}/{self._env_names['app_secret']}/{self._env_names['chat_id']}"
                )

    def _post_webhook(self, text: str) -> None:
        payload = {"msg_type": "text", "content": {"text": text}}
        for attempt in range(1, self._max_attempts + 1):
            try:
                resp = httpx.post(self._webhook, json=payload, timeout=self._timeout)
                if resp.status_code in (429, 500, 502, 503, 504):
                    if attempt < self._max_attempts:
                        time.sleep(1)
                        continue
                    if resp.status_code == 429:
                        raise DeliveryRateLimitError(f"飞书限流（HTTP {resp.status_code}）")
                    raise DeliveryServerError(f"飞书服务端错误（HTTP {resp.status_code}）")
                if resp.status_code >= 400:
                    raise DeliveryCredentialError(
                        f"飞书 Webhook 请求失败（HTTP {resp.status_code}）"
                    )
                return
            except httpx.TimeoutException as exc:
                if attempt < self._max_attempts:
                    continue
                raise DeliveryTimeoutError("飞书 Webhook 超时") from exc
            except httpx.HTTPError as exc:
                if attempt < self._max_attempts:
                    continue
                raise DeliveryServerError("飞书网络请求失败") from exc

    def _send_file(self, file_path: Path) -> str:
        from ...feishu import upload_file

        file_key = upload_file(file_path, self._app_id, self._app_secret)
        token_url = "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal"
        token_resp = httpx.post(
            token_url,
            json={"app_id": self._app_id, "app_secret": self._app_secret},
            timeout=self._timeout,
        )
        token_data = token_resp.json()
        if token_data.get("code") != 0:
            raise DeliveryCredentialError("飞书获取 tenant_access_token 失败")
        token = token_data["tenant_access_token"]
        content = json.dumps({"file_key": file_key}, ensure_ascii=False)
        resp = httpx.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "chat_id"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json; charset=utf-8",
            },
            json={
                "receive_id": self._chat_id,
                "msg_type": "file",
                "content": content,
            },
            timeout=self._timeout,
        )
        data = resp.json()
        if resp.status_code >= 400 or data.get("code") != 0:
            raise DeliveryServerError("飞书发送文件消息失败")
        return str(data.get("data", {}).get("message_id") or f"feishu-file-{file_key}")

    def deliver(
        self,
        *,
        report_metadata: dict,
        summary_text: str,
        artifact_paths: list[str],
        delivery_context: dict,
    ) -> DeliveryResult:
        started = time.perf_counter()
        mode = self.configured_mode
        if mode == "delivery_disabled":
            result = DeliveryResult(
                provider="feishu",
                delivery_mode="disabled_by_configuration",
                success=True,
                message_id="",
                file_ids=[],
                delivered_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
                attempt_count=1,
                duration_ms=0,
                warnings=["delivery: 已由配置显式关闭，未发送任何消息"],
                network_calls=0,
            )
            self._write_receipt(delivery_context, result, summary_text, artifact_paths)
            return result

        self._check_mode_ready()
        warnings: list[str] = []
        message_ids: list[str] = []
        file_ids: list[str] = []
        network_calls = 0

        if self._send_summary and self._has_webhook:
            self._post_webhook(summary_text)
            network_calls += 1
            message_ids.append(f"feishu-webhook-{int(time.time())}")
        elif self._send_summary and not self._has_webhook:
            warnings.append("webhook 未配置，跳过文本摘要")

        should_upload = self._send_artifact and bool(artifact_paths)
        if should_upload and self.file_delivery_supported:
            for artifact in artifact_paths:
                path = Path(artifact)
                if not path.exists():
                    warnings.append(f"文件不存在，跳过: {path.name}")
                    continue
                mid = self._send_file(path)
                message_ids.append(mid)
                file_ids.append(f"feishu-file-{path.name}")
                network_calls += 4  # 上传(token+文件) + 发消息(token+消息)
        elif should_upload and not self.file_delivery_supported:
            warnings.append(
                "file_delivery_supported=false：仅发送摘要，未上传 Word 文件"
            )

        result = DeliveryResult(
            provider="feishu",
            delivery_mode=mode,
            success=True,
            message_id=";".join(message_ids),
            file_ids=file_ids,
            delivered_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            attempt_count=self._max_attempts,
            duration_ms=int((time.perf_counter() - started) * 1000),
            warnings=warnings,
            network_calls=network_calls,
        )
        self._write_receipt(delivery_context, result, summary_text, artifact_paths)
        return result

    @staticmethod
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
        payload["message_digest"] = hashlib.sha256(summary_text.encode("utf-8")).hexdigest()
        payload["file_names"] = [Path(p).name for p in artifact_paths]
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
