"""Common adapter contract and error/rate-limit types."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


class SocialAdapterError(RuntimeError):
    """Standard adapter error with a stable machine-readable code."""

    def __init__(self, message: str, *, code: str = "social_adapter_error", retry_after: int | None = None):
        super().__init__(message)
        self.code = code
        self.retry_after = retry_after


@dataclass(slots=True)
class RateLimitState:
    remaining: int | None = None
    reset_at: str | None = None
    retry_after: int | None = None
    last_status: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "remaining": self.remaining,
            "reset_at": self.reset_at,
            "retry_after": self.retry_after,
            "last_status": self.last_status,
        }


class SocialAdapter(ABC):
    """Platform adapter interface. Adapters never write Word or business tables."""

    platform = "unknown"

    def __init__(self, account: dict[str, Any], *, client: Any = None):
        self.account = account
        self.client = client
        self.rate_limit = RateLimitState()

    @abstractmethod
    def fetch_account(self) -> dict[str, Any]:
        raise NotImplementedError

    @abstractmethod
    def fetch_recent_posts(self, *, since: datetime | None = None, limit: int = 30) -> list[dict[str, Any]]:
        raise NotImplementedError

    @abstractmethod
    def fetch_post(self, platform_post_id: str) -> dict[str, Any] | None:
        raise NotImplementedError

    @abstractmethod
    def normalize_post(self, raw: dict[str, Any]) -> dict[str, Any]:
        raise NotImplementedError

    def health_check(self) -> dict[str, Any]:
        return {"platform": self.platform, "account_id": self.account.get("account_id"), "ok": True}

    def handle_response(self, response: Any) -> Any:
        status = int(getattr(response, "status_code", 200) or 200)
        self.rate_limit.last_status = status
        headers = getattr(response, "headers", {}) or {}
        if status == 429:
            retry_after = _header_int(headers, "retry-after")
            self.rate_limit.retry_after = retry_after
            raise SocialAdapterError("rate limited", code="rate_limited", retry_after=retry_after)
        if status >= 400:
            raise SocialAdapterError(f"HTTP {status}", code=f"http_{status}")
        return response


def _header_int(headers: Any, name: str) -> int | None:
    try:
        lower = {str(k).lower(): v for k, v in headers.items()}
        return int(lower.get(name)) if lower.get(name) is not None else None
    except Exception:
        return None
