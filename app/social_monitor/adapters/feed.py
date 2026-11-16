"""Generic RSS/Atom feed adapter for social accounts.

Used whenever an account carries ``feed_url``: podcasts, blogs/Substack,
YouTube channel feeds, or an operator-run RSS bridge for a platform whose
official adapter is not implemented (Facebook/Threads/X).  The account keeps
its real ``platform`` label; only the transport is a feed.
"""

from __future__ import annotations

import email.utils
import hashlib
from datetime import datetime
from typing import Any

import feedparser
import httpx

from ..models import to_utc_iso
from ..normalize import normalize_social_text, normalize_url
from .base import SocialAdapter, SocialAdapterError

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)


def _entry_time(entry: Any) -> str | None:
    for key in ("published_parsed", "updated_parsed"):
        value = entry.get(key) if hasattr(entry, "get") else None
        if value:
            try:
                return datetime(*value[:6]).replace(microsecond=0).isoformat() + "+00:00"
            except (TypeError, ValueError):
                continue
    raw = (entry.get("published") or entry.get("updated")) if hasattr(entry, "get") else None
    if not raw:
        return None
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).isoformat()
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(str(raw))
        return parsed.isoformat() if parsed.tzinfo else None
    except (TypeError, ValueError):
        return None


def _strip_html(text: str) -> str:
    if "<" not in text:
        return text
    try:
        from bs4 import BeautifulSoup

        return BeautifulSoup(text, "html.parser").get_text(" ", strip=True)
    except Exception:  # noqa: BLE001 - plain text fallback
        return text


class FeedAdapter(SocialAdapter):
    platform = "feed"

    def __init__(self, account: dict[str, Any], *, client: Any = None):
        super().__init__(account, client=client)
        if self.client is None:
            self.client = httpx.Client(
                timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT}
            )

    @property
    def feed_url(self) -> str:
        url = str(self.account.get("feed_url") or "").strip()
        if not url:
            raise SocialAdapterError("account has no feed_url", code="missing_feed_url")
        return url

    def _parse(self):
        response = self.client.get(self.feed_url)
        self.handle_response(response)
        parsed = feedparser.parse(getattr(response, "text", "") or "")
        if not parsed.entries and parsed.get("bozo"):
            raise SocialAdapterError("malformed feed payload", code="malformed_payload")
        return parsed

    def fetch_account(self) -> dict[str, Any]:
        parsed = self._parse()
        return {
            "platform_user_id": self.account.get("platform_user_id"),
            "display_name": parsed.feed.get("title"),
            "handle": self.account.get("handle"),
            "canonical_url": parsed.feed.get("link") or self.account.get("canonical_url"),
        }

    def fetch_recent_posts(self, *, since: datetime | None = None, limit: int = 30) -> list[dict[str, Any]]:
        parsed = self._parse()
        posts: list[dict[str, Any]] = []
        for entry in (parsed.entries or [])[: max(1, limit)]:
            published = _entry_time(entry)
            if since is not None and published:
                try:
                    if datetime.fromisoformat(str(published).replace("Z", "+00:00")) < since:
                        continue
                except ValueError:
                    pass
            link = str(entry.get("link") or "").strip()
            entry_id = str(entry.get("yt_videoid") or entry.get("id") or link or "").strip()
            if not entry_id:
                continue
            summary = entry.get("summary") or ""
            if not summary and entry.get("content"):
                summary = (entry.get("content") or [{}])[0].get("value") or ""
            posts.append({
                "platform_post_id": "feed_" + hashlib.sha256(entry_id.encode("utf-8")).hexdigest()[:24],
                "published_at": published,
                "text": _strip_html(str(summary)),
                "title": entry.get("title"),
                "canonical_url": link or self.account.get("canonical_url"),
                "raw": {"id": entry_id, "link": link},
            })
        return posts

    def fetch_post(self, platform_post_id: str) -> dict[str, Any] | None:
        for raw in self.fetch_recent_posts(limit=50):
            if str(raw.get("platform_post_id")) == str(platform_post_id):
                return raw
        return None

    def normalize_post(self, raw: dict[str, Any]) -> dict[str, Any]:
        text = raw.get("text") or ""
        return {
            "platform": str(self.account.get("platform") or "other"),
            "platform_post_id": str(raw.get("platform_post_id") or ""),
            "text": text,
            "normalized_text": normalize_social_text(text),
            "title": raw.get("title"),
            "canonical_url": normalize_url(raw.get("canonical_url")),
            "published_at": to_utc_iso(raw.get("published_at")),
            "thumbnail_url": None,
            "raw": raw.get("raw") or {},
        }
