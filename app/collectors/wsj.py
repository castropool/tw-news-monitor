"""Wall Street Journal RSS collector (metadata only, disabled in Phase I).

The Dow Jones RSS endpoint (feeds.a.dj.com) still responds but the feed is
frozen (lastBuildDate ~2025-01-27) and every entry is marked PAID, so the
source is disabled in config/sources.yaml (``enabled: false``). The collector
is fully implemented for a future re-enable: PAID entries are collected
normally and never cause failure or a workaround. Articles carry
``access_level="metadata_only"`` (URL metadata only, no readable content).
"""

import email.utils
from datetime import datetime

import feedparser

from ..models import Article
from ..summarizer import rss_summary_from_entry
from ..time_utils import TAIPEI
from .base import BaseCollector


def _parse_rss_datetime(entry) -> datetime | None:
    """Parse entry pubDate (RFC822) into Asia/Taipei."""
    pub_str = entry.get("published", "") or entry.get("pubDate", "")
    if pub_str:
        try:
            parsed_dt = email.utils.parsedate_to_datetime(pub_str)
        except (ValueError, TypeError, OverflowError):
            parsed_dt = None
        if parsed_dt is not None:
            if parsed_dt.tzinfo is not None:
                return parsed_dt.astimezone(TAIPEI)
            return parsed_dt.replace(tzinfo=TAIPEI)
    if entry.get("published_parsed"):
        return datetime(*entry.published_parsed[:6], tzinfo=TAIPEI)
    return None


class WSJRSSCollector(BaseCollector):
    """Collector for the WSJ Dow Jones RSS feed (metadata only, disabled)."""

    def collect(self) -> list[Article]:
        resp = self.client.get(self.url)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        if not feed.entries and feed.get("bozo"):
            error = feed.get("bozo_exception")
            raise ValueError(f"RSS feed parse error: {error or 'no valid entries'}")
        articles: list[Article] = []
        now = datetime.now()

        for i, entry in enumerate(feed.entries[: self.MAX_ITEMS]):
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            summary = rss_summary_from_entry(entry)
            articles.append(Article(
                source_id=self.source_id,
                source_name=self.source_name,
                category=self.category,
                title=title,
                url=self.normalize_url(link),
                published_at=_parse_rss_datetime(entry),
                fetched_at=now,
                position=i + 1,
                summary=summary,
                summary_source="rss" if summary else None,
                section=self.source.get("section") or None,
                language="en",
                access_level="metadata_only",
            ))
        return articles
