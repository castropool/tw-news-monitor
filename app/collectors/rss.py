from datetime import datetime
import email.utils

from ..time_utils import TAIPEI
from ..summarizer import rss_summary_from_entry
import feedparser

from ..models import Article
from .base import BaseCollector


class RSSCollector(BaseCollector):
    """Collector for RSS/Atom feeds."""

    def collect(self) -> list[Article]:
        resp = self.client.get(self.url)
        resp.raise_for_status()
        feed = feedparser.parse(resp.text)
        if not feed.entries and feed.get("bozo"):
            error = feed.get("bozo_exception")
            raise ValueError(f"RSS feed parse error: {error or 'no valid entries'}")
        articles: list[Article] = []
        now = datetime.now()

        for i, entry in enumerate(feed.entries[:self.MAX_ITEMS]):
            title = entry.get("title", "").strip()
            link = entry.get("link", "").strip()
            if not title or not link:
                continue
            published = None
            pub_str = entry.get("published", "")
            if pub_str:
                try:
                    parsed_dt = email.utils.parsedate_to_datetime(pub_str)
                    if parsed_dt.tzinfo is not None:
                        published = parsed_dt.astimezone(TAIPEI)
                    elif entry.get("published_parsed"):
                        published = datetime(*entry.published_parsed[:6], tzinfo=TAIPEI)
                except (ValueError, TypeError, OverflowError):
                    if entry.get("published_parsed"):
                        published = datetime(*entry.published_parsed[:6], tzinfo=TAIPEI)
            summary = rss_summary_from_entry(entry)
            articles.append(Article(
                source_id=self.source_id,
                source_name=self.source_name,
                category=self.category,
                title=title,
                url=self.normalize_url(link),
                published_at=published,
                fetched_at=now,
                position=i + 1,
                summary=summary,
                summary_source="rss" if summary else None,
            ))
        return articles
