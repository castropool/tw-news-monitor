"""Convert parsed Newsletter items into the existing Article dataclass."""

from datetime import datetime
import re

from ..models import Article
from ..time_utils import TAIPEI
from .models import NewsletterMessage
from .parser import parse_message
from .policy import SourcePolicy
from .url_policy import URLPolicy


class NewsletterCollector:
    """Source-aware, in-memory Newsletter collector.

    It intentionally has no HTTP client.  A collector instance retains URL and
    normalized-title fingerprints across messages in one run, preventing the
    same article in a morning/evening/breaking newsletter from being emitted
    twice while leaving the database's existing URL uniqueness untouched.
    """

    def __init__(self, source: dict, *, policy: SourcePolicy | None = None, url_policy: URLPolicy | None = None):
        self.source = dict(source)
        self.policy = policy
        self.url_policy = url_policy or URLPolicy()
        self._seen_urls: set[str] = set()
        self._seen_titles: set[str] = set()

    def collect(self, message: NewsletterMessage) -> list[Article]:
        if not isinstance(message, NewsletterMessage):
            raise TypeError("NewsletterCollector.collect expects NewsletterMessage")
        items = parse_message(
            message, policy=self.policy, url_policy=self.url_policy,
            source_id=self.source.get("id", ""),
            max_items=(self.policy.max_items if self.policy else 20),
        )
        fetched_at = datetime.now(TAIPEI)
        result: list[Article] = []
        for item in items:
            fingerprint = title_fingerprint(item.title)
            if item.url in self._seen_urls or (fingerprint and fingerprint in self._seen_titles):
                continue
            self._seen_urls.add(item.url)
            if fingerprint:
                self._seen_titles.add(fingerprint)
            result.append(
                Article(
                    source_id=self.source.get("id", item.source_id),
                    source_name=self.source.get("name", self.source.get("id", "Newsletter")),
                    category="international",
                    title=item.title,
                    url=item.url,
                    published_at=item.published_at,
                    fetched_at=fetched_at,
                    position=len(result) + 1,
                    summary=item.summary,
                    summary_source="newsletter" if item.summary else None,
                    section=self.source.get("section") or self.source.get("newsletter_name"),
                    language=self.source.get("language", "en"),
                    access_level="newsletter",
                )
            )
        return result


def title_fingerprint(title: str) -> str:
    """Stable cross-email title fingerprint, ignoring case and punctuation."""
    return "".join(ch.lower() for ch in title if ch.isalnum())

