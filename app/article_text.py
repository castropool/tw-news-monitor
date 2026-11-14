"""Internal store of full article text, kept for fact extraction and search.

Text comes from pages the summarizer already downloads in ``hybrid`` mode
(no extra requests).  An optional, capped extra fetch fills text for recent
articles that had an RSS teaser and so were never downloaded.

Rules:

- Only main-text extractions are stored; meta descriptions are not.
- Articles with an ``access_level`` (international media metadata/newsletter
  layer) are never fetched, matching the summarizer's legal boundary.
- Text older than the retention window (default 730 days, about 24 months)
  is deleted; the article row itself is kept.
- Everything here is best effort and never blocks news delivery.

Environment:

- ``ARTICLE_TEXT_STORE``: ``0`` disables storing (default on).
- ``ARTICLE_TEXT_RETENTION_DAYS``: retention window in days (default 730).
- ``ARTICLE_TEXT_FETCH_MAX``: extra pages fetched per run for articles with
  no stored text (default 0 = no extra requests).
"""

from __future__ import annotations

import logging
import os
from datetime import datetime, timedelta, timezone
from typing import Callable, Iterable

from .models import Article

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 730
MIN_TEXT_CHARS = 80
EXTRACTOR = "trafilatura"


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def store_enabled() -> bool:
    return os.getenv("ARTICLE_TEXT_STORE", "1").strip().lower() not in ("0", "false", "no", "off")


def retention_days() -> int:
    return max(1, _int_env("ARTICLE_TEXT_RETENTION_DAYS", DEFAULT_RETENTION_DAYS))


def _keepable(texts: dict[str, str]) -> dict[str, str]:
    return {url: text for url, text in texts.items() if text and len(text.strip()) >= MIN_TEXT_CHARS}


def save_texts(db, texts: dict[str, str], *, now: datetime | None = None) -> int:
    """Store main-text extractions; returns rows written. Never raises."""
    if db is None or not texts or not store_enabled():
        return 0
    try:
        written = db.save_article_texts(
            _keepable(texts), extractor=EXTRACTOR, fetched_at=now or datetime.now(timezone.utc)
        )
        if written:
            logger.info("Article texts stored: %d", written)
        return written
    except Exception as exc:  # noqa: BLE001 - storage must never block delivery
        logger.warning("Article text storage failed safely: %s", exc)
        return 0


def purge_expired(db, *, now: datetime | None = None, days: int | None = None) -> int:
    if db is None:
        return 0
    try:
        cutoff = (now or datetime.now(timezone.utc)) - timedelta(days=days or retention_days())
        removed = db.purge_article_texts(cutoff)
        if removed:
            logger.info("Article texts purged (older than %s): %d", cutoff.date(), removed)
        return removed
    except Exception as exc:  # noqa: BLE001
        logger.warning("Article text purge failed safely: %s", exc)
        return 0


def fetch_missing(
    db,
    articles: Iterable[Article],
    *,
    max_fetch: int | None = None,
    fetcher: Callable[[list[Article]], dict[str, str]] | None = None,
    now: datetime | None = None,
) -> int:
    """Fetch and store text for up to ``max_fetch`` articles that have none."""
    limit = _int_env("ARTICLE_TEXT_FETCH_MAX", 0) if max_fetch is None else max_fetch
    if db is None or limit <= 0 or not store_enabled():
        return 0
    try:
        candidates = [a for a in articles if a.access_level is None and a.url]
        if not candidates:
            return 0
        have = db.get_urls_with_text([a.url for a in candidates])
        missing = [a for a in candidates if a.url not in have][:limit]
        if not missing:
            return 0
        if fetcher is None:
            from .summarizer import fetch_article_full_texts as fetcher  # lazy: avoids import cycle
        return save_texts(db, fetcher(missing), now=now)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Article text fetch failed safely: %s", exc)
        return 0
