"""Shared one-line annotations for poll / betting items in Word and digests.

Rendering recomputes the annotation from the article text with the same pure
analyzers used at collection time (also covers ``--export-word`` and other
historical re-renders), so no DB lookup is needed and output is identical.
"""

from __future__ import annotations

from functools import lru_cache

from .betting import format_betting_line
from .config import load_special_topics_config
from .gate import analyze_article
from .polls import format_poll_line


@lru_cache(maxsize=1)
def _default_config() -> dict:
    return load_special_topics_config()


def special_topic_line(article, config: dict | None = None) -> str:
    """Return e.g. ``民调机构：…｜关键数据：…`` or ``类型：…｜涉及选举：是``."""
    category = str(getattr(article, "category", "") or "")
    if category not in ("poll", "betting"):
        return ""
    try:
        annotation = analyze_article(article, config or _default_config())
    except Exception:  # noqa: BLE001 - an annotation must never break rendering
        return ""
    if category == "poll":
        return format_poll_line(annotation)
    return format_betting_line(annotation)
