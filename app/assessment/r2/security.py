"""Phase R2 delivery security gate (Feishu credential rotation status)."""

from __future__ import annotations

import os


def feishu_gate(config: dict) -> dict:
    """Return current Feishu technical/credential/delivery readiness."""
    security = config.get("security") or {}
    rotated = security.get("feishu_credentials_rotated_after_incident") is True
    acknowledged_at = security.get("feishu_rotation_acknowledged_at")
    app_id = bool((os.getenv("FEISHU_APP_ID") or "").strip())
    app_secret = bool((os.getenv("FEISHU_APP_SECRET") or "").strip())
    chat_id = bool((os.getenv("FEISHU_CHAT_ID") or "").strip())
    disable_send = (os.getenv("DISABLE_FEISHU_SEND") or "").strip().lower() in ("1", "true", "yes")
    technical_live_ready = app_id and app_secret and chat_id and not disable_send
    return {
        "feishu_technical_live_ready": technical_live_ready,
        "feishu_credentials_rotated_after_incident": rotated,
        "feishu_rotation_acknowledged_at": acknowledged_at,
        "production_delivery_ready": bool(rotated and technical_live_ready),
        "blocker": "" if rotated and technical_live_ready else "MANUAL_FEISHU_CREDENTIAL_ROTATION_REQUIRED",
    }
