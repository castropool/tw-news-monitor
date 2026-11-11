"""Bloomberg official-newsletter collector (mailbox-only, readonly)."""

from __future__ import annotations

from .wsj_newsletter import WSJNewsletterCollector, _domains
from ..newsletter import BloombergNewsletterAdapter
from ..newsletter_ingestion.collector import NewsletterCollector
from ..newsletter_ingestion.policy import SourcePolicy


class BloombergNewsletterCollector(WSJNewsletterCollector):
    """Use the shared mailbox boundary with the Bloomberg adapter."""

    DEFAULT_SENDER_ALLOWLIST = frozenset({"bloomberg.com", "bloombergmedia.com"})
    DEFAULT_ARTICLE_ALLOWLIST = frozenset({"bloomberg.com"})

    def __init__(self, source: dict, *, mailbox=None, adapter=None):
        # Do not call the WSJ adapter constructor: it would bind the wrong
        # media adapter.  The shared BaseCollector state is still identical.
        from .base import BaseCollector

        BaseCollector.__init__(self, source)
        self.mailbox = mailbox or source.get("mailbox_client")
        self.adapter = adapter or BloombergNewsletterAdapter(source)
        self.policy = SourcePolicy(
            label=str(source.get("mailbox_label") or self.DEFAULT_LABEL),
            allowed_domains=_domains(source.get("sender_allowlist"), self.DEFAULT_SENDER_ALLOWLIST),
            allowed_article_domains=_domains(source.get("article_allowlist"), self.DEFAULT_ARTICLE_ALLOWLIST),
            source_id=source.get("id"),
        )
        # ``collect`` is inherited from WSJNewsletterCollector and keeps
        # per-run URL/title fingerprints on the collector instance.  The
        # Bloomberg constructor is intentionally explicit so it can bind the
        # Bloomberg adapter; initialize the same dedup state here.
        self._seen_urls: set[str] = set()
        self._seen_titles: set[str] = set()
        self._collector = NewsletterCollector(source, policy=self.policy)
        self.parse_errors = 0


__all__ = ["BloombergNewsletterCollector"]
