"""Central relevance gate and topic annotations for poll / betting articles.

Collectors never filter by keyword (AGENTS.md collector rule 5).  Query-based
feeds inevitably return some off-topic hits, so — like ``content_filter`` —
this gate runs centrally after dedup: off-topic items are still stored but
marked ``delivery_eligible=False`` with ``filter_reason='topic_relevance'``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .betting import analyze_betting
from .polls import analyze_poll

FILTER_REASON = "topic_relevance"


def analyze_article(article, config: dict[str, Any]) -> dict[str, Any] | None:
    """Return the annotation for a poll/betting article, else ``None``."""
    category = str(getattr(article, "category", "") or "")
    title = getattr(article, "title", None)
    summary = getattr(article, "summary", None)
    if category == "poll":
        return analyze_poll(title, summary, config.get("poll") or {})
    if category == "betting":
        return analyze_betting(title, summary, config.get("betting") or {})
    return None


@dataclass(slots=True)
class TopicGateResult:
    kept: list = field(default_factory=list)
    blocked: list = field(default_factory=list)
    annotations: dict[str, dict[str, Any]] = field(default_factory=dict)


def apply_topic_gate(articles: list, config: dict[str, Any] | None) -> TopicGateResult:
    """Split poll/betting articles into deliverable and off-topic items.

    Articles of other categories (or of a disabled topic) pass through
    unchanged.  Annotations are returned for every analysed article.
    """
    result = TopicGateResult()
    cfg = config or {}
    for article in articles:
        category = str(getattr(article, "category", "") or "")
        section = cfg.get(category) if category in ("poll", "betting") else None
        if not isinstance(section, dict) or not section.get("enabled", True):
            result.kept.append(article)
            continue
        annotation = analyze_article(article, cfg)
        if annotation is None:
            result.kept.append(article)
            continue
        result.annotations[article.url] = annotation
        if annotation.get("relevant"):
            result.kept.append(article)
        else:
            result.blocked.append(article)
    return result


def topic_rows(articles: list, annotations: dict[str, dict[str, Any]], config: dict[str, Any] | None) -> list[dict]:
    """Build ``news_topics`` rows (topic=poll|betting) for persisted articles."""
    version = str((config or {}).get("filter_version") or "current")
    rows: list[dict] = []
    for article in articles:
        annotation = annotations.get(article.url)
        category = str(getattr(article, "category", "") or "")
        if annotation is None or category not in ("poll", "betting"):
            continue
        metadata = dict(annotation)
        metadata["filter_version"] = version
        if category == "poll":
            rows.append({
                "url": article.url,
                "topic": "poll",
                "event_type": "poll_release",
                "source_type": annotation.get("pollster") or None,
                "confidence": 100 if annotation.get("relevant") else 0,
                "metadata": metadata,
            })
        else:
            subtypes = annotation.get("subtypes") or []
            rows.append({
                "url": article.url,
                "topic": "betting",
                "scope": "election" if annotation.get("election_related") else "general",
                "event_type": subtypes[0] if subtypes else "betting",
                "confidence": 100 if annotation.get("relevant") else 0,
                "metadata": metadata,
            })
    return rows
