"""Collector for Google News RSS search feeds (query-defined sources).

Used for topics without a single publisher feed — published opinion polls
and underground-betting developments.  The feed URL in ``config/sources.yaml``
carries the search query; the collector itself never filters by keyword
(AGENTS.md collector rule 5): relevance is decided centrally by
``app.special_topics.gate``.

Google News item specifics handled here:
- titles end with `` - <Publisher>``; the suffix is removed and the
  publisher (``<source>`` element) becomes ``source_name``;
- descriptions are HTML link lists that merely repeat the title, so no
  teaser is stored (summaries are enriched later by the summarizer);
- article links are Google News redirect URLs; they are stable per story
  and are used as-is for URL dedup.
"""

from __future__ import annotations

import email.utils
from datetime import datetime

import feedparser

from ..models import Article
from ..source_health import SourceOutcome
from ..time_utils import TAIPEI
from .base import BaseCollector


def _publisher(entry) -> str:
    source = entry.get("source")
    if isinstance(source, dict):
        return str(source.get("title") or "").strip()
    return str(getattr(source, "title", "") or "").strip()


def _clean_title(title: str, publisher: str) -> str:
    """Remove the trailing `` - <Publisher>`` (a bare suffix yields "")."""
    title = title.strip()
    if publisher and title.endswith(publisher):
        head = title[: -len(publisher)].rstrip()
        if head == "" or head.endswith(("-", "–", "—", "|")):
            return head.rstrip("-–—|").strip()
    return title


def _published(entry) -> datetime | None:
    pub_str = entry.get("published", "")
    if pub_str:
        try:
            parsed = email.utils.parsedate_to_datetime(pub_str)
            if parsed.tzinfo is not None:
                return parsed.astimezone(TAIPEI)
        except (TypeError, ValueError, OverflowError):
            pass
    if entry.get("published_parsed"):
        try:
            return datetime(*entry.published_parsed[:6], tzinfo=TAIPEI)
        except (TypeError, ValueError):
            return None
    return None


class GoogleNewsRSSCollector(BaseCollector):
    """Google News RSS search collector (max 20 items, no body fetch)."""

    def collect(self) -> list[Article]:
        response = self.get_with_retry(self.url)
        status = int(getattr(response, "status_code", 0) or 0)
        if not 200 <= status < 300:
            self.mark_outcome(http_status=status, schema_valid=False, item_count=0, error_code="http")
            raise ValueError(f"HTTP {status}")
        feed = feedparser.parse(response.text)
        if not feed.entries and feed.get("bozo"):
            self.mark_outcome(http_status=status, schema_valid=False, item_count=0, error_code="schema")
            raise ValueError(f"RSS feed parse error: {feed.get('bozo_exception') or 'no valid entries'}")
        now = datetime.now()
        articles: list[Article] = []
        for entry in feed.entries:
            if len(articles) >= self.MAX_ITEMS:
                break
            publisher = _publisher(entry)
            title = _clean_title(str(entry.get("title", "") or ""), publisher)
            link = str(entry.get("link", "") or "").strip()
            if not title or not link:
                continue
            articles.append(Article(
                source_id=self.source_id,
                source_name=publisher or self.source_name,
                category=self.category,
                title=title,
                url=self.normalize_url(link),
                published_at=_published(entry),
                fetched_at=now,
                position=len(articles) + 1,
                summary=None,
                summary_source=None,
            ))
        self.last_outcome = SourceOutcome(status, True, len(articles))
        return articles
