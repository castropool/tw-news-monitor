"""Dedicated information-source topics: published polls and underground betting.

Both topics are ordinary article categories (``poll`` / ``betting``) collected
through ``config/sources.yaml``.  This package holds the pure, deterministic
pieces shared by collection and rendering:

- ``config``: fail-safe loading of ``config/special_topics.yaml``;
- ``polls`` / ``betting``: text analyzers producing explainable annotations;
- ``gate``: the central relevance gate and ``news_topics`` row builder.

No network access and no LLM calls happen here.
"""

from __future__ import annotations

TOPIC_CATEGORIES: tuple[str, ...] = ("poll", "betting")


def is_dedicated_topic_article(article) -> bool:
    """Whether an article belongs to a dedicated topic section.

    Such articles keep their own digest/Word section and are never re-routed
    into the international-media or 九合一 sections.
    """
    return str(getattr(article, "category", "") or "") in TOPIC_CATEGORIES


__all__ = ["TOPIC_CATEGORIES", "is_dedicated_topic_article"]
