"""YouTube Data API v3 adapter (first production platform)."""

from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx

from .base import SocialAdapter, SocialAdapterError
from ..normalize import normalize_social_text, normalize_url
from ..models import to_utc_iso


API_BASE = "https://www.googleapis.com/youtube/v3"


class YouTubeAdapter(SocialAdapter):
    platform = "youtube"

    def __init__(self, account: dict[str, Any], *, client: Any = None, api_key: str | None = None):
        super().__init__(account, client=client)
        self.api_key = api_key or os.getenv("YOUTUBE_API_KEY", "").strip()
        if self.client is None:
            self.client = httpx.Client(timeout=15)

    @property
    def channel_id(self) -> str:
        return str(self.account.get("platform_user_id") or "").strip()

    def _get(self, path: str, params: dict[str, Any]) -> dict[str, Any]:
        if not self.api_key:
            raise SocialAdapterError("YOUTUBE_API_KEY is not configured", code="missing_api_key")
        payload = dict(params)
        payload["key"] = self.api_key
        response = self.client.get(f"{API_BASE}/{path}", params=payload)
        self.handle_response(response)
        data = response.json()
        if not isinstance(data, dict):
            raise SocialAdapterError("malformed YouTube payload", code="malformed_payload")
        return data

    @property
    def collection_method(self) -> str:
        return "official_api_v3" if self.api_key else "youtube_public_rss"

    def _fetch_rss(self) -> str:
        url = f"https://www.youtube.com/feeds/videos.xml?channel_id={self.channel_id}"
        response = self.client.get(url)
        self.handle_response(response)
        text = getattr(response, "text", None)
        if text is None:
            raise SocialAdapterError("malformed RSS payload", code="malformed_payload")
        return str(text)

    def fetch_account(self) -> dict[str, Any]:
        if not self.api_key:
            parsed = feedparser.parse(self._fetch_rss())
            return {
                "platform_user_id": self.channel_id,
                "display_name": parsed.feed.get("title"),
                "handle": None,
                "canonical_url": f"https://www.youtube.com/channel/{self.channel_id}",
                "uploads_playlist_id": None,
            }
        data = self._get("channels", {"part": "snippet,contentDetails", "id": self.channel_id})
        items = data.get("items") or []
        if not items:
            raise SocialAdapterError("YouTube channel not found", code="account_not_found")
        item = items[0]
        snippet = item.get("snippet") or {}
        return {
            "platform_user_id": item.get("id") or self.channel_id,
            "display_name": snippet.get("title"),
            "handle": snippet.get("customUrl"),
            "canonical_url": f"https://www.youtube.com/channel/{item.get('id') or self.channel_id}",
            "uploads_playlist_id": ((item.get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads"),
        }

    def fetch_recent_posts(self, *, since: datetime | None = None, limit: int = 30):
        if not self.api_key:
            parsed = feedparser.parse(self._fetch_rss())
            posts = []
            for entry in (parsed.entries or [])[: max(1, limit)]:
                published = getattr(entry, "published", None) or getattr(entry, "updated", None)
                if since is not None and published:
                    try:
                        if datetime.fromisoformat(str(published).replace("Z", "+00:00")) < since:
                            continue
                    except ValueError:
                        pass
                video_id = getattr(entry, "yt_videoid", None) or str(getattr(entry, "id", "")).split(":")[-1]
                posts.append({
                    "platform_post_id": video_id,
                    "published_at": published,
                    "text": getattr(entry, "summary", "") or "",
                    "title": getattr(entry, "title", None),
                    "canonical_url": getattr(entry, "link", None) or f"https://www.youtube.com/watch?v={video_id}",
                    "thumbnail_url": None,
                    "raw": {"id": getattr(entry, "id", None)},
                })
            return [p for p in posts if p["platform_post_id"]]
        account = self.fetch_account()
        playlist = account.get("uploads_playlist_id") or self._uploads_playlist(self.channel_id)
        data = self._get(
            "playlistItems",
            {"part": "snippet,contentDetails", "playlistId": playlist, "maxResults": min(max(1, limit), 50)},
        )
        posts = []
        for item in data.get("items") or []:
            snippet = item.get("snippet") or {}
            content = item.get("contentDetails") or {}
            published = content.get("videoPublishedAt") or snippet.get("publishedAt")
            if since is not None and published:
                try:
                    published_dt = datetime.fromisoformat(str(published).replace("Z", "+00:00"))
                    if published_dt < since:
                        continue
                except ValueError:
                    pass
            posts.append({
                "platform_post_id": content.get("videoId") or snippet.get("resourceId", {}).get("videoId"),
                "published_at": published,
                "text": snippet.get("description") or "",
                "title": snippet.get("title"),
                "canonical_url": f"https://www.youtube.com/watch?v={content.get('videoId') or snippet.get('resourceId', {}).get('videoId')}",
                "thumbnail_url": (snippet.get("thumbnails") or {}).get("high", {}).get("url"),
                "raw": item,
            })
        return [p for p in posts if p["platform_post_id"]]

    def fetch_post(self, platform_post_id: str) -> dict[str, Any] | None:
        if not self.api_key:
            for raw in self.fetch_recent_posts(limit=50):
                if str(raw.get("platform_post_id")) == str(platform_post_id):
                    return raw
            return None
        data = self._get("videos", {"part": "snippet,contentDetails", "id": platform_post_id})
        items = data.get("items") or []
        if not items:
            return None
        item = items[0]
        snippet = item.get("snippet") or {}
        return {
            "platform_post_id": item.get("id") or platform_post_id,
            "published_at": snippet.get("publishedAt"),
            "text": snippet.get("description") or "",
            "title": snippet.get("title"),
            "canonical_url": f"https://www.youtube.com/watch?v={item.get('id') or platform_post_id}",
            "raw": item,
        }

    @staticmethod
    def _uploads_playlist(channel_id: str) -> str:
        return "UU" + channel_id[2:] if channel_id.startswith("UC") else channel_id

    def normalize_post(self, raw: dict[str, Any]) -> dict[str, Any]:
        text = raw.get("text") or ""
        return {
            "platform": "youtube",
            "platform_post_id": str(raw.get("platform_post_id") or ""),
            "text": text,
            "normalized_text": normalize_social_text(text),
            "title": raw.get("title"),
            "canonical_url": normalize_url(raw.get("canonical_url")),
            "published_at": to_utc_iso(raw.get("published_at")),
            "thumbnail_url": raw.get("thumbnail_url"),
            "raw": raw.get("raw") or {},
        }
