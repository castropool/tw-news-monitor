"""Short-lived, in-memory clustering for international media coverage.

The event layer deliberately does not add a database table.  Articles remain
individually persisted and this module only decides how the current delivery
run presents them.  It is conservative: a missing timestamp, conflicting
locations, a different event family, or a cross-day follow-up prevents a
merge unless the canonical URL is identical.
"""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .international import is_international_media, normalize_tokens, title_similarity
from .models import Article


_SOURCE_IDS = {
    "reuters_international": "Reuters",
    "ft_alphaville": "Financial Times",
    "wsj_newsletter": "Wall Street Journal",
    "bloomberg_newsletter": "Bloomberg",
}
_SOURCE_PRIORITY = {
    "reuters": 0,
    "financial times": 1,
    "wall street journal": 2,
    "bloomberg": 3,
}

# Event families are intentionally narrower than relevance keywords.  Sharing
# "China" or "Taiwan" alone is never sufficient to merge two reports.
_EVENT_GROUPS: dict[str, tuple[str, ...]] = {
    "military_drill": (
        "military", "drill", "drills", "exercise", "exercises", "maneuver",
        "maneuvers", "military exercise", "military drills",
    ),
    "arms_sale": ("arms", "arm", "arms sales", "arms sale", "weapon", "weapons"),
    "chip_export": (
        "chip", "chips", "semiconductor", "export", "exports", "export controls",
        "export control", "advanced chip",
    ),
    "fab_investment": ("fab", "fabs", "foundry", "factory", "plant"),
    "talks": ("talks", "talk", "negotiations", "dialogue", "resume", "restart"),
    "tariff": ("tariff", "tariffs", "duties", "trade war"),
    "sanctions": ("sanction", "sanctions", "penalty", "penalties"),
    "diplomacy": ("summit", "visit", "visits", "agreement", "treaty", "statement"),
}

_ENTITY_ALIASES: dict[str, tuple[str, ...]] = {
    "Taiwan": ("taiwan", "taipei", "taiwanese", "taiwan strait", "cross-strait"),
    "China": ("china", "chinese", "beijing", "xi jinping"),
    "United States": ("united states", "us", "u.s.", "washington", "white house"),
    "TSMC": ("tsmc", "taiwan semiconductor manufacturing"),
    "Philippines": ("philippines", "philippine"),
    "Japan": ("japan", "japanese", "tokyo"),
    "Arizona": ("arizona",),
    "European Union": ("eu", "european union", "european"),
    "South Korea": ("south korea", "korea", "seoul"),
}

# These entities describe an explicit destination/setting.  If both titles
# contain different members from this set, they are almost certainly separate
# stories even if the broad title wording is similar.
_CONFLICTING_LOCATIONS = frozenset(
    {"Taiwan", "Philippines", "Japan", "Arizona", "European Union", "United States", "South Korea"}
)
_TOKEN_RE = re.compile(r"[A-Za-z0-9]+(?:[-'][A-Za-z0-9]+)?")
_TRACKING_PARAMS = frozenset({"utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content", "gclid"})


@dataclass(slots=True)
class EventCluster:
    """One current-run event and its source coverage."""

    event_id: str
    canonical: Article
    members: list[Article] = field(default_factory=list)
    coverage: list[Article] = field(default_factory=list)
    topics: list[str] = field(default_factory=list)
    time_window: tuple[datetime | None, datetime | None] = (None, None)


def normalize_canonical_url(url: str | None) -> str:
    """Normalize a URL for identity matching without following redirects."""

    raw = str(url or "").strip()
    if not raw:
        return ""
    parts = urlsplit(raw)
    scheme = parts.scheme.lower()
    netloc = parts.netloc.lower()
    path = parts.path or "/"
    if path != "/":
        path = path.rstrip("/")
    query = urlencode(
        sorted(
            (key, value)
            for key, value in parse_qsl(parts.query, keep_blank_values=True)
            if key.lower() not in _TRACKING_PARAMS
        )
    )
    return urlunsplit((scheme, netloc, path, query, ""))


def _source_label(article: Article) -> str:
    source_id = str(getattr(article, "source_id", "") or "").strip().lower()
    return _SOURCE_IDS.get(source_id, str(getattr(article, "source_name", "") or "").strip())


def _is_international(article: Article, config: dict[str, Any]) -> bool:
    source_id = str(getattr(article, "source_id", "") or "").strip().lower()
    source_name = str(getattr(article, "source_name", "") or "").strip().lower()
    return source_id in _SOURCE_IDS or source_name in {name.lower() for name in _SOURCE_PRIORITY} or is_international_media(article.source_name, config)


def _text(article: Article) -> str:
    return f"{article.title or ''} {article.summary or ''}".strip()


def _has_phrase(text: str, phrase: str) -> bool:
    tokens = [token.lower() for token in _TOKEN_RE.findall(text or "")]
    wanted = [token.lower() for token in _TOKEN_RE.findall(phrase)]
    if not wanted:
        return False
    if len(wanted) == 1:
        return wanted[0] in tokens
    return any(tokens[i : i + len(wanted)] == wanted for i in range(len(tokens) - len(wanted) + 1))


def _entities(article: Article) -> set[str]:
    text = _text(article)
    return {label for label, aliases in _ENTITY_ALIASES.items() if any(_has_phrase(text, alias) for alias in aliases)}


def _event_groups(article: Article) -> set[str]:
    text = _text(article)
    return {group for group, aliases in _EVENT_GROUPS.items() if any(_has_phrase(text, alias) for alias in aliases)}


def _within_window(left: Article, right: Article, hours: float) -> bool:
    first, second = left.published_at, right.published_at
    if first is None or second is None:
        return False
    if (first.tzinfo is None) != (second.tzinfo is None):
        return False
    return abs((first - second).total_seconds()) <= hours * 3600


def _location_conflict(left_entities: set[str], right_entities: set[str]) -> bool:
    left_locations = left_entities & _CONFLICTING_LOCATIONS
    right_locations = right_entities & _CONFLICTING_LOCATIONS
    return bool(left_locations and right_locations and left_locations != right_locations)


def _core_features(left: Article, right: Article, config: dict[str, Any]) -> int:
    dedup = config.get("dedup", {}) or {}
    synonyms = dedup.get("synonyms", {}) or {}
    left_tokens = normalize_tokens(_text(left), synonyms)
    right_tokens = normalize_tokens(_text(right), synonyms)
    core = set()
    for value in dedup.get("core_words", []) or []:
        core.update(normalize_tokens(str(value), synonyms))
    shared_core = len(left_tokens & right_tokens & core)
    shared_event = len(_event_groups(left) & _event_groups(right))
    return shared_core + shared_event


def _same_event(left: Article, right: Article, config: dict[str, Any]) -> bool:
    if normalize_canonical_url(left.url) and normalize_canonical_url(left.url) == normalize_canonical_url(right.url):
        return True
    if not _within_window(left, right, float((config.get("dedup", {}) or {}).get("window_hours", 24))):
        return False
    left_entities, right_entities = _entities(left), _entities(right)
    left_events, right_events = _event_groups(left), _event_groups(right)
    if not left_entities & right_entities or not left_events & right_events:
        return False
    if _location_conflict(left_entities, right_entities):
        return False
    dedup = config.get("dedup", {}) or {}
    synonyms = dedup.get("synonyms", {}) or {}
    similarity = title_similarity(_text(left), _text(right), synonyms)
    threshold = float(dedup.get("similarity_threshold", 0.70))
    # A small semantic allowance handles editorial synonyms such as
    # restrict/tighten and resume/restart, but still requires event/entity/core
    # agreement.  It does not lower the URL identity rule or time boundary.
    if similarity < max(0.50, threshold - 0.15):
        return False
    return _core_features(left, right, config) >= 2


def _published_key(article: Article, index: int, priorities: dict[str, int]) -> tuple[float, int, int]:
    published = article.published_at
    try:
        timestamp = published.timestamp() if published is not None else float("inf")
    except (AttributeError, OverflowError, ValueError):
        timestamp = float("inf")
    source = _source_label(article).strip().lower()
    return timestamp, priorities.get(source, len(priorities) + 1), index


_IDENTITY_GENERIC_TOKENS = frozenset(
    {
        # These words describe editorial framing rather than the event.  They
        # are already mostly removed by ``normalize_tokens``; keeping this
        # small explicit set makes the fingerprint less sensitive to a wire
        # story's boilerplate while preserving named entities and actions.
        "report",
        "reports",
        "say",
        "says",
        "said",
        "latest",
        "update",
        "updates",
        "new",
    }
)


def _title_identity_tokens(article: Article, config: dict[str, Any]) -> set[str]:
    """Return stable, source-independent title tokens for an event identity."""

    dedup = config.get("dedup", {}) or {}
    tokens = normalize_tokens(article.title or "", dedup.get("synonyms", {}) or {})
    return tokens - _IDENTITY_GENERIC_TOKENS


def _event_time_bucket(members: list[Article], bucket_hours: float = 24.0) -> str:
    """Return an order-independent UTC time bucket for the event.

    The date-sized default separates a later-day follow-up with otherwise
    similar wording, while keeping the four-source fixture in one bucket.
    Missing timestamps are explicit instead of silently using wall-clock time.
    """

    timestamps = [article.published_at for article in members if article.published_at is not None]
    if not timestamps:
        return "time-unknown"
    first = min(timestamps)
    # Normalize aware timestamps to UTC before taking the date boundary.  Naive
    # timestamps retain their local date because their timezone is unknown.
    if first.tzinfo is not None:
        first = first.astimezone(timezone.utc)
    hours = max(float(bucket_hours), 1.0)
    if hours == 24.0:
        return first.date().isoformat()
    epoch = first.timestamp()
    bucket = int(epoch // (hours * 3600.0))
    return str(bucket)


def _event_id(
    members: list[Article],
    canonical: Article,
    config: dict[str, Any] | None = None,
) -> str:
    """Derive a stable event ID from event content, never from sample IDs.

    The identity intentionally excludes source IDs, source order and article
    URLs for multi-member clusters.  For a cluster, the intersection of
    normalized title tokens captures the content shared by editorial variants;
    entities and event groups add semantic anchors; a UTC time bucket keeps a
    later follow-up from inheriting the old event ID.  A URL is only a last
    resort when an article has no usable title at all.
    """

    cfg = config or {}
    token_sets = [_title_identity_tokens(article, cfg) for article in members]
    common_tokens = set.intersection(*token_sets) if token_sets else set()
    entities = set().union(*(_entities(article) for article in members)) if members else set()
    groups = set().union(*(_event_groups(article) for article in members)) if members else set()
    if not common_tokens:
        # Empty titles are invalid collector output, but metadata-only feeds
        # can still reach this boundary.  Keep their identity deterministic.
        common_tokens = {
            normalize_canonical_url(canonical.url)
        } if normalize_canonical_url(canonical.url) else {"content-unknown"}
    bucket_hours = float((cfg.get("dedup", {}) or {}).get("event_id_bucket_hours", 24.0))
    canonical_identity = (
        normalize_canonical_url(canonical.url) if len(members) == 1 else ""
    )
    identity = "\x1f".join(
        (
            "international-event-v1",
            _event_time_bucket(members, bucket_hours),
            "canonical=" + canonical_identity,
            "entities=" + ",".join(sorted(entities)),
            "groups=" + ",".join(sorted(groups)),
            "tokens=" + ",".join(sorted(common_tokens)),
        )
    )
    digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
    return f"evt_{digest}"


def _cluster_topics(members: list[Article]) -> list[str]:
    groups = set().union(*(_event_groups(article) for article in members))
    entities = set().union(*(_entities(article) for article in members))
    result: list[str] = []
    if "military_drill" in groups:
        result.append("military")
    if "arms_sale" in groups or "diplomacy" in groups or "talks" in groups:
        result.append("diplomacy")
    if "chip_export" in groups or "fab_investment" in groups:
        result.append("semiconductor")
    if "tariff" in groups:
        result.append("trade")
    if "Taiwan" in entities:
        result.append("taiwan")
    return result or ["international"]


def cluster_international_articles(
    articles: list[Article], config: dict[str, Any] | None = None,
) -> tuple[list[EventCluster], dict[str, list[Article]]]:
    """Cluster only international articles and return canonical coverage."""

    cfg = config or {}
    if not cfg.get("enabled", True):
        clusters = [
            EventCluster(
                _event_id([article], article, cfg),
                article,
                [article],
                [article],
                ["international"],
                (article.published_at, article.published_at),
            )
            for article in articles
        ]
        return clusters, {article.url: [article] for article in articles}

    international = [
        index for index, article in enumerate(articles)
        if _is_international(article, cfg)
    ]

    # Build conservative cliques rather than a transitive connected graph.
    # With a graph, A↔B and B↔C can merge even when A↔C are more than 24
    # hours apart, which is exactly the cross-day follow-up failure this layer
    # must avoid.  Every member must independently match every existing member
    # of a cluster, so the cluster's complete time span stays bounded.
    groups: dict[int, list[int]] = {}
    next_group = 0
    for index in international:
        placed = False
        for group_indices in groups.values():
            if all(
                _same_event(articles[index], articles[other], cfg)
                for other in group_indices
            ):
                group_indices.append(index)
                placed = True
                break
        if not placed:
            groups[next_group] = [index]
            next_group += 1

    # Local/Taiwan articles are retained as singleton pass-through entries so
    # callers can use this helper without accidentally dropping their lane.
    for index, article in enumerate(articles):
        if index not in international:
            groups[next_group] = [index]
            next_group += 1

    priorities_cfg = (cfg.get("dedup", {}) or {}).get("source_priority", []) or []
    priorities = {str(value).strip().lower(): rank for rank, value in enumerate(priorities_cfg)}
    priorities.update(_SOURCE_PRIORITY)
    built: list[tuple[int, EventCluster]] = []
    coverage: dict[str, list[Article]] = {}
    for indices in groups.values():
        members = [articles[index] for index in indices]
        canonical = min(
            ((index, article) for index, article in zip(indices, members)),
            key=lambda pair: _published_key(pair[1], pair[0], priorities),
        )[1]
        start = min((article.published_at for article in members if article.published_at), default=None)
        end = max((article.published_at for article in members if article.published_at), default=None)
        cluster = EventCluster(
            event_id=_event_id(members, canonical, cfg),
            canonical=canonical,
            members=members,
            coverage=list(members),
            topics=_cluster_topics(members),
            time_window=(start, end),
        )
        first_index = min(indices)
        built.append((first_index, cluster))
        coverage[canonical.url] = list(members)
    built.sort(key=lambda item: item[0])

    return [cluster for _, cluster in built], coverage


__all__ = ["EventCluster", "cluster_international_articles", "normalize_canonical_url"]
