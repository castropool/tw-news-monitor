"""WSJ official-newsletter collector.

This source has no HTTP path.  It consumes normalized messages from the
provider-neutral readonly mailbox and delegates parsing to the Wave 1
NewsletterAdapter.  A mailbox object is injected by the runtime or tests;
the YAML source itself never contains credentials or a service object.
"""

from __future__ import annotations

from collections.abc import Iterable
from urllib.parse import urlparse

from ..models import Article
from ..newsletter import WSJNewsletterAdapter
from ..newsletter_ingestion.models import NewsletterMessage
from ..newsletter_ingestion.oauth import MAILBOX_AUTH_REQUIRED
from ..newsletter_ingestion.policy import SourcePolicy
from ..newsletter_ingestion.collector import title_fingerprint
from ..source_health import SourceOutcome
from .base import BaseCollector


class WSJNewsletterCollector(BaseCollector):
    """Convert allowlisted mailbox messages into newsletter Articles."""

    DEFAULT_LABEL = "InternationalNews"
    DEFAULT_SENDER_ALLOWLIST = frozenset({"wsj.com", "dowjones.com"})
    DEFAULT_ARTICLE_ALLOWLIST = frozenset({"wsj.com"})

    def __init__(self, source: dict, *, mailbox=None, adapter=None):
        super().__init__(source)
        self.mailbox = mailbox or source.get("mailbox_client")
        self.adapter = adapter or WSJNewsletterAdapter(source)
        self.policy = SourcePolicy(
            label=str(source.get("mailbox_label") or self.DEFAULT_LABEL),
            allowed_domains=_domains(source.get("sender_allowlist"), self.DEFAULT_SENDER_ALLOWLIST),
            allowed_article_domains=_domains(source.get("article_allowlist"), self.DEFAULT_ARTICLE_ALLOWLIST),
            source_id=source.get("id"),
        )
        self._seen_urls: set[str] = set()
        self._seen_titles: set[str] = set()
        self.parse_errors = 0
        self.last_error_message: str | None = None

    def collect(self, messages: Iterable[NewsletterMessage] | NewsletterMessage | None = None):
        if isinstance(messages, NewsletterMessage):
            messages = [messages]
        if messages is None:
            # The runtime always injects a GmailMailboxClient.  Missing or
            # unauthorized auth is a source failure, not a healthy empty
            # mailbox; this prevents a disabled OAuth boundary from being
            # mistaken for a valid zero-item feed.
            if self.mailbox is None or (
                getattr(self.mailbox, "auth", None) is not None
                and not getattr(self.mailbox.auth, "authorized", False)
            ):
                self.last_error_message = MAILBOX_AUTH_REQUIRED
                self.mark_outcome(
                    http_status=0,
                    schema_valid=False,
                    item_count=0,
                    error_code="auth",
                )
                return []
            try:
                messages = self._read_mailbox()
            except Exception as exc:  # noqa: BLE001 - isolate provider failure
                self.last_error_message = f"mailbox read failed: {type(exc).__name__}"
                self.mark_outcome(
                    http_status=0,
                    schema_valid=False,
                    item_count=0,
                    error_code="http",
                )
                return []
        articles = []
        for message in messages:
            if not isinstance(message, NewsletterMessage):
                self.parse_errors += 1
                continue
            try:
                decision = self.policy.check(message.label, message.sender)
                if not decision.accepted:
                    continue
                payload = message.html or message.text
                if not payload:
                    continue
                fmt = "html" if message.html or "<a" in payload.lower() else "text"
                # The only parser invoked here is the media adapter.  It is
                # intentionally given mailbox content, never an article URL.
                parsed = self.adapter.parse(payload, fmt=fmt)
                for article in parsed:
                    host = urlparse(article.url).hostname or ""
                    if not self.policy.article_host_allowed(host):
                        continue
                    fingerprint = title_fingerprint(article.title)
                    if article.url in self._seen_urls or (fingerprint and fingerprint in self._seen_titles):
                        continue
                    self._seen_urls.add(article.url)
                    if fingerprint:
                        self._seen_titles.add(fingerprint)
                    if article.published_at is None:
                        article.published_at = message.received_at
                    article.position = len(articles) + 1
                    articles.append(article)
            except Exception:
                self.parse_errors += 1
        self.mark_outcome(
            http_status=200,
            schema_valid=self.parse_errors == 0,
            item_count=len(articles),
            error_code="parse" if self.parse_errors else None,
        )
        return articles[: self.MAX_ITEMS]

    def _read_mailbox(self) -> list[NewsletterMessage]:
        if self.mailbox is None:
            return []
        result = self.mailbox.list_messages(
            self.policy.label,
            set(self.policy.allowed_domains),
            str(self.source.get("mailbox_since") or "1d"),
        )
        return list(result or [])


def _domains(value, fallback) -> set[str]:
    if value is None:
        value = fallback
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, (set, frozenset, list, tuple)):
        value = fallback
    return {str(item).strip().lower().lstrip("@").rstrip(".") for item in value if str(item).strip()}


__all__ = ["WSJNewsletterCollector"]
