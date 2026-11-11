"""FT Alphaville collector via the official RSS 2.0 feed (public).

The feed provides title / short description (teaser) / link / pubDate / guid,
no full text. Collected Articles are marked ``access_level="public"`` and
``language="en"``; a missing description yields ``summary=None`` and must
never be treated as a failure.
"""

import email.utils
from datetime import datetime

import feedparser
from xml.etree import ElementTree

from ..models import Article
from ..summarizer import rss_summary_from_entry
from ..time_utils import TAIPEI
from .base import BaseCollector


def _parse_rss_datetime(entry) -> datetime | None:
    """Parse entry pubDate (RFC822, usually US Eastern offset) into Asia/Taipei."""
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


class FTAlphavilleCollector(BaseCollector):
    """Collector for the FT Alphaville RSS feed (public teasers)."""

    SECTION = "Alphaville"

    def collect(self) -> list[Article]:
        resp = self.get_with_retry(self.url)
        resp.raise_for_status()
        if not _looks_like_rss(resp.content):
            self.mark_outcome(
                http_status=resp.status_code,
                schema_valid=False,
                item_count=0,
                error_code="parse",
            )
            raise ValueError("FT RSS schema changed")
        feed = feedparser.parse(resp.text)
        if not feed.entries and feed.get("bozo"):
            error = feed.get("bozo_exception")
            self.mark_outcome(
                http_status=resp.status_code,
                schema_valid=False,
                item_count=0,
                error_code="parse",
            )
            raise ValueError(f"RSS feed parse error: {error or 'no valid entries'}")
        articles: list[Article] = []
        now = datetime.now(TAIPEI)

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
                section=self.SECTION,
                language="en",
                access_level="public",
            ))
        self.mark_outcome(http_status=resp.status_code, schema_valid=True, item_count=len(articles))
        return articles


def _looks_like_rss(content: bytes) -> bool:
    try:
        root = ElementTree.fromstring(content)
    except (ElementTree.ParseError, TypeError, ValueError):
        return False
    local = root.tag.rsplit("}", 1)[-1].lower()
    if local == "rss":
        return any(child.tag.rsplit("}", 1)[-1].lower() == "channel" for child in root)
    if local == "feed":
        return True
    return False
