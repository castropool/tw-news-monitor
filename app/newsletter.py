"""Backward-compatible facade for the Newsletter ingestion package.

The historical API accepted ``parse_newsletter(payload, fmt)`` and source
dicts.  Those names remain stable while all parsing now lives in the offline,
bounded implementation under :mod:`app.newsletter_ingestion`.
"""

import email.utils
from datetime import datetime

from .models import Article
from .collectors.base import BaseCollector
from .newsletter_ingestion.collector import NewsletterCollector
from .newsletter_ingestion.models import NewsletterItem, NewsletterMessage
from .newsletter_ingestion.parser import parse_message
from .time_utils import TAIPEI

# Private compatibility alias retained for older callers that imported the
# helper from the original monolithic module.  New parsing uses the stricter
# HTTPS-only URL policy instead.
_normalize_url = BaseCollector.normalize_url

__all__ = [
    "NewsletterItem",
    "parse_newsletter",
    "NewsletterParser",
    "NewsletterAdapter",
    "WSJNewsletterAdapter",
    "BloombergNewsletterAdapter",
]


def parse_newsletter(text_or_bytes, fmt: str) -> list[NewsletterItem]:
    """Parse HTML, plain text, or EML while preserving the legacy signature."""
    if fmt not in {"html", "eml", "text"}:
        raise ValueError(
            f"unsupported newsletter format {fmt!r} "
            "(expected 'html', 'eml' or 'text')"
        )
    if fmt == "eml":
        return parse_message(text_or_bytes)
    if isinstance(text_or_bytes, bytes):
        text_or_bytes = text_or_bytes.decode("utf-8", errors="replace")
    if not isinstance(text_or_bytes, str):
        text_or_bytes = str(text_or_bytes)
    message = NewsletterMessage(
        message_id="legacy", sender="", received_at=None, subject="",
        html=text_or_bytes if fmt == "html" else None,
        text=text_or_bytes if fmt == "text" else None, label="",
    )
    return parse_message(message)


class NewsletterParser:
    """Convert one local newsletter payload into existing Article objects."""

    def __init__(self, source: dict):
        self.source = dict(source)

    def parse(self, text_or_bytes, fmt: str = "html") -> list[Article]:
        if fmt not in {"html", "eml", "text"}:
            raise ValueError(
                f"unsupported newsletter format {fmt!r} "
                "(expected 'html', 'eml' or 'text')"
            )
        if fmt == "eml":
            items = parse_message(text_or_bytes, source_id=self.source.get("id", ""))
            # Build an already-parsed message for collector conversion while
            # retaining the EML-derived dates and teaser values.
            fetched_at = datetime.now(TAIPEI)
            return [
                _article_from_item(self.source, item, fetched_at, position)
                for position, item in enumerate(items, 1)
            ]
        if isinstance(text_or_bytes, bytes):
            text_or_bytes = text_or_bytes.decode("utf-8", errors="replace")
        message = NewsletterMessage(
            message_id="legacy", sender="", received_at=None, subject="",
            html=text_or_bytes if fmt == "html" else None,
            text=text_or_bytes if fmt == "text" else None, label="",
        )
        return NewsletterCollector(self.source).collect(message)


class NewsletterAdapter(NewsletterParser):
    """Compatibility name for source-aware newsletter parsing."""


class WSJNewsletterAdapter(NewsletterAdapter):
    """Adapter for legally received WSJ Newsletter payloads."""


class BloombergNewsletterAdapter(NewsletterAdapter):
    """Adapter for legally received Bloomberg Newsletter payloads."""


def _article_from_item(source: dict, item: NewsletterItem, fetched_at: datetime, position: int) -> Article:
    return Article(
        source_id=source.get("id", item.source_id),
        source_name=source.get("name", source.get("id", "Newsletter")),
        category="international",
        title=item.title,
        url=item.url,
        published_at=item.published_at,
        fetched_at=fetched_at,
        position=position,
        summary=item.summary,
        summary_source="newsletter" if item.summary else None,
        section=source.get("section") or source.get("newsletter_name"),
        language=source.get("language", "en"),
        access_level="newsletter",
    )


def _parse_email_date(value) -> datetime | None:
    """Legacy helper retained for callers that imported it indirectly."""
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    return parsed.astimezone(TAIPEI) if parsed.tzinfo else parsed.replace(tzinfo=TAIPEI)


def _parse_html(value):
    return parse_newsletter(value, "html")


def _parse_text(value):
    return parse_newsletter(value, "text")


def _parse_eml(value):
    return parse_newsletter(value, "eml")
