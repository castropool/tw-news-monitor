from datetime import datetime
from urllib.parse import urlsplit

from ..time_utils import TAIPEI
from .base import BaseCollector
from ..models import Article
from ..summarizer import rss_summary_from_entry
import feedparser

LTN_ALLOWED_HOSTS = frozenset({"news.ltn.com.tw", "ec.ltn.com.tw"})


class LtnRSSCollector(BaseCollector):
    """RSS collector for Liberty Times (自由時報).

    Adds domain whitelist, description HTML cleaning, and
    Content-Type/size validation on top of RSS parsing.
    """

    MAX_RESPONSE_BYTES = 2 * 1024 * 1024

    def collect(self) -> list[Article]:
        resp = self.client.get(self.url)
        resp.raise_for_status()

        content_type = (resp.headers.get("content-type") or "").lower()
        if "xml" not in content_type and "rss" not in content_type and "text" not in content_type:
            print(f"  [WARN] Unexpected Content-Type: {content_type}")

        if len(resp.content) > self.MAX_RESPONSE_BYTES:
            print(f"  [WARN] Response too large: {len(resp.content)} bytes")
            return []

        feed = feedparser.parse(resp.text)
        articles: list[Article] = []
        now = datetime.now()

        for i, entry in enumerate(feed.entries[:self.MAX_ITEMS]):
            title = (entry.get("title") or "").strip()
            link = (entry.get("link") or "").strip()
            if not title or not link:
                continue

            norm_url = self.normalize_url(link)
            parts = urlsplit(norm_url)
            host = (parts.hostname or "").lower()
            if host not in LTN_ALLOWED_HOSTS:
                continue

            published = None
            pub_str = entry.get("published", "") or entry.get("pubDate", "")
            if pub_str:
                try:
                    import email.utils
                    parsed = email.utils.parsedate_to_datetime(pub_str)
                    if parsed.tzinfo is not None:
                        published = parsed.astimezone(TAIPEI)
                    else:
                        published = parsed.replace(tzinfo=TAIPEI)
                except (ValueError, TypeError, OverflowError):
                    pass

            summary = rss_summary_from_entry(entry)
            articles.append(Article(
                source_id=self.source_id,
                source_name=self.source_name,
                category=self.category,
                title=title,
                url=norm_url,
                published_at=published,
                fetched_at=now,
                position=i + 1,
                summary=summary,
                summary_source="rss" if summary else None,
            ))

        return articles
