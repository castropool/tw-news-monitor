"""Layered deterministic event clustering.

Layers:
  1. article dedup (normalized URL)
  2. event date window
  3. primary actor combination
  4. canonical action normalization
  5. event object / issue
  6. report relationship judgement

The existing merge_articles_into_events helper is reused only for articles
without a canonical action (statement/unknown headlines) so identical reprints
still merge while different statements stay separate.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from app.election_event_merge import merge_articles_into_events

from .action_normalizer import normalize_action
from .candidate_models import NormalizedArticle


@dataclass(slots=True)
class ArticleCluster:
    articles: list[NormalizedArticle] = field(default_factory=list)
    coarse_title_group: str = ""
    relationship_type: str = "same_event"
    low_confidence_reasons: list[str] = field(default_factory=list)
    merge_reasons: list[str] = field(default_factory=list)
    non_merge_reasons: list[str] = field(default_factory=list)

    @property
    def anchor(self) -> NormalizedArticle | None:
        if not self.articles:
            return None
        return min(self.articles, key=lambda a: (a.published_at or "9999", str(a.news_article_id)))

    def sorted_articles(self) -> list[NormalizedArticle]:
        return sorted(self.articles, key=lambda a: (a.published_at or "9999", str(a.news_article_id)))


def _primary_actor(article: NormalizedArticle) -> str:
    if article.match.matched_people:
        return article.match.matched_people[0]
    if article.match.matched_parties:
        return article.match.matched_parties[0]
    return ""


def _date_value(text: str) -> datetime | None:
    try:
        dt = datetime.fromisoformat(text)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone(timedelta(hours=8)))
            dt = dt.replace(tzinfo=None)
        return dt
    except (ValueError, TypeError):
        return None


def _dates_close(a: NormalizedArticle, b: NormalizedArticle, window_days: int) -> bool:
    da = _date_value(a.published_at)
    db = _date_value(b.published_at)
    if da is None or db is None:
        return True
    return abs((da - db).days) <= window_days


def extract_event_date(
    article: NormalizedArticle,
    config,
) -> tuple[str, str, str]:
    """Return (event_date, basis, confidence)."""
    title = article.raw_title or article.normalized_title
    summary = article.summary or ""
    base = _date_value(article.published_at)

    patterns = [
        r"(?P<y>20\d{2})年(?P<m>\d{1,2})月(?P<d>\d{1,2})日",
        r"(?P<m>\d{1,2})月(?P<d>\d{1,2})日",
        r"(?P<y>20\d{2})-(?P<m>\d{1,2})-(?P<d>\d{1,2})",
        r"(?P<m>\d{1,2})/(?P<d>\d{1,2})",
        r"(?<!\d)(?P<d>\d{1,2})日",
    ]
    for field_name, text in (("title", title), ("summary", summary)):
        for pat in patterns:
            m = re.search(pat, text)
            if not m:
                continue
            y = int(m.group("y")) if "y" in m.groupdict() and m.group("y") else (base.year if base else None)
            mo = int(m.group("m")) if "m" in m.groupdict() and m.group("m") else (base.month if base else None)
            d = int(m.group("d"))
            if y is None or mo is None:
                continue
            try:
                dt = datetime(y, mo, d)
            except ValueError:
                continue
            basis = "explicit_in_title" if field_name == "title" else "explicit_in_summary"
            confidence = "high"
            if "m" not in m.groupdict() and base is not None:
                confidence = "medium" if mo != base.month else "high"
            if dt.date() == (base.date() if base else None):
                return dt.isoformat(), basis, confidence
            return dt.isoformat(), basis, confidence

    relative = {
        "昨日": -1,
        "昨天": -1,
        "今日": 0,
        "今天": 0,
        "明日": 1,
        "明天": 1,
    }
    for field_name, text in (("title", title), ("summary", summary)):
        for word, delta in relative.items():
            if word in text and base is not None:
                dt = base + timedelta(days=delta)
                basis = "explicit_in_title" if field_name == "title" else "explicit_in_summary"
                return dt.isoformat(), basis, "medium"

    if base is not None:
        return base.isoformat(), "inferred_from_publication", "low"
    return "", "unknown", "unknown"


def _statement_pseudo_action(article: NormalizedArticle, config) -> str:
    verbs = config.get("relevance.direct_statement_verbs", []) or []
    title = article.normalized_title
    if any(v in title for v in verbs) or "：" in title or ":" in title:
        return "statement"
    return ""


def _cluster_key(article: NormalizedArticle, config) -> tuple[str, str, str, str]:
    action, _ = normalize_action(article.normalized_title, config)
    if not action:
        action = _statement_pseudo_action(article, config)
    issue = article.match.matched_issues[0] if article.match.matched_issues else ""
    return (_primary_actor(article), action, issue, "")


def _merge_groups_by_date(articles: list[NormalizedArticle], window_days: int) -> list[list[NormalizedArticle]]:
    articles = sorted(articles, key=lambda a: (a.published_at or "9999", str(a.news_article_id)))
    buckets: list[list[NormalizedArticle]] = []
    for a in articles:
        placed = False
        for bucket in buckets:
            if any(_dates_close(a, other, window_days) for other in bucket):
                bucket.append(a)
                placed = True
                break
        if not placed:
            buckets.append([a])
    return buckets


def _shared_phrase_len(a_text: str, b_text: str, min_len: int = 6) -> int:
    a = re.sub(r"\s+", "", a_text or "")
    b = re.sub(r"\s+", "", b_text or "")
    best = 0
    for start in range(len(a)):
        for end in range(start + min_len, min(len(a), start + 30) + 1):
            if a[start:end] in b:
                best = max(best, end - start)
    return best


def cluster_articles(
    articles: list[NormalizedArticle],
    config,
) -> list[ArticleCluster]:
    if not articles:
        return []
    window = int(config.get("clustering.date_window_days", 7))

    # Layer 1: dedup by normalized URL
    seen_urls: set[str] = set()
    unique: list[NormalizedArticle] = []
    for a in articles:
        url = a.normalized_url or a.raw_url
        if url:
            if url in seen_urls:
                continue
            seen_urls.add(url)
        unique.append(a)

    action_articles: dict[tuple[str, str, str, str], list[NormalizedArticle]] = {}
    statement_articles: list[NormalizedArticle] = []
    for a in unique:
        key = _cluster_key(a, config)
        if key[1] in ("", "statement"):
            statement_articles.append(a)
        else:
            action_articles.setdefault(key, []).append(a)

    clusters: list[ArticleCluster] = []
    for key, group in sorted(action_articles.items(), key=lambda kv: kv[0]):
        actor, action, issue, _ = key
        for bucket in _merge_groups_by_date(group, window):
            reasons = [f"actor={actor}", f"action={action}", f"issue={issue}",
                       "date_window_merged"]
            clusters.append(
                ArticleCluster(
                    articles=bucket,
                    coarse_title_group="action_group",
                    merge_reasons=reasons,
                )
            )

    # Articles without canonical action: reuse merge_articles_into_events for
    # identical reprints, otherwise singleton.
    if statement_articles:
        matches = [
            {
                "article_url": a.normalized_url or a.raw_url,
                "city": a.match.city,
                "relevance": a.match.relevance,
                "matched_people": a.match.matched_people,
                "matched_parties": a.match.matched_parties,
                "matched_issues": a.match.matched_issues,
                "matched_basis": a.match.matched_basis,
            }
            for a in statement_articles
        ]
        articles_map = {
            a.normalized_url or a.raw_url: {
                "url": a.normalized_url or a.raw_url,
                "title": a.normalized_title,
                "source_name": a.source_name,
                "published_at": a.published_at,
            }
            for a in statement_articles
        }
        try:
            coarse = merge_articles_into_events(matches, articles_map)
        except Exception:
            coarse = []
        group_by_url: dict[str, NormalizedArticle] = {}
        for a in statement_articles:
            group_by_url.setdefault(a.normalized_url or a.raw_url, a)
        used: set[str] = set()
        for evt in coarse:
            group_articles = [
                group_by_url[src.get("url") or src.get("article_url")]
                for src in evt.get("sources", [])
                if (src.get("url") or src.get("article_url")) in group_by_url
            ]
            group_articles = [a for a in group_articles if a.news_article_id not in used]
            for a in group_articles:
                used.add(a.news_article_id)
            if group_articles:
                clusters.append(
                    ArticleCluster(
                        articles=group_articles,
                        coarse_title_group="title_group",
                        merge_reasons=["identical_normalized_title"],
                    )
                )
        for a in statement_articles:
            if a.news_article_id not in used:
                clusters.append(
                    ArticleCluster(
                        articles=[a],
                        coarse_title_group="singleton_statement",
                        non_merge_reasons=["no_canonical_action_or_shared_title"],
                    )
                )

    clusters = _merge_shared_phrase(clusters, config, window)
    clusters.sort(key=lambda c: (c.anchor.published_at if c.anchor else "9999",
                                 c.anchor.news_article_id if c.anchor else ""))
    return clusters


def _merge_shared_phrase(clusters: list[ArticleCluster], config, window_days: int) -> list[ArticleCluster]:
    merged: list[ArticleCluster] = []
    for cluster in sorted(clusters, key=lambda c: (c.anchor.published_at if c.anchor else "9999",
                                                   c.anchor.news_article_id if c.anchor else "")):
        anchor = cluster.anchor
        target = None
        for m in merged:
            other = m.anchor
            if anchor is None or other is None:
                continue
            if not _dates_close(anchor, other, window_days):
                continue
            if _primary_actor(anchor) != _primary_actor(other):
                continue
            action_a, _ = normalize_action(anchor.normalized_title, config)
            action_b, _ = normalize_action(other.normalized_title, config)
            if action_a != action_b:
                continue
            if _shared_phrase_len(anchor.normalized_title, other.normalized_title) >= 6:
                target = m
                break
        if target is not None:
            target.articles.extend(cluster.articles)
            target.merge_reasons.append("shared_event_phrase")
        else:
            merged.append(cluster)
    return merged


def relationship_between(
    a: ArticleCluster,
    b: ArticleCluster,
    config,
) -> tuple[str, list[str]]:
    from .assertion_classifier import classify_article_assertions

    aa = a.anchor
    ba = b.anchor
    if aa is None or ba is None:
        return "uncertain", ["missing_anchor"]
    actor_a = _primary_actor(aa)
    actor_b = _primary_actor(ba)
    date_a = _date_value(aa.published_at)
    date_b = _date_value(ba.published_at)
    window = int(config.get("clustering.date_window_days", 7))
    dates_close = date_a is not None and date_b is not None and abs((date_a - date_b).days) <= window
    actors_same = bool(actor_a and actor_b and actor_a == actor_b)
    action_a, _ = normalize_action(aa.normalized_title, config)
    action_b, _ = normalize_action(ba.normalized_title, config)
    reasons: list[str] = []
    shared_phrase = _shared_phrase_len(aa.normalized_title, ba.normalized_title) >= 4

    if actors_same and dates_close and action_a and action_b and action_a != action_b:
        reasons.append("same_actor_close_date_different_action")
        return "possible_subevent", reasons
    if actors_same and dates_close:
        reasons.append("same_actor_close_date")
        return "related_event", reasons
    if dates_close:
        kinds_a = {
            x["assertion_kind"]
            for art in a.articles
            for x in classify_article_assertions(art, "rel", "rel", config)
        }
        kinds_b = {
            x["assertion_kind"]
            for art in b.articles
            for x in classify_article_assertions(art, "rel", "rel", config)
        }
        if ("allegation" in kinds_a and "actor_statement" in kinds_b) or (
            "allegation" in kinds_b and "actor_statement" in kinds_a
        ):
            reasons.append("allegation_response_close_date")
            return "related_event", reasons
    if not dates_close:
        reasons.append("date_window_exceeded")
        return "separate_event", reasons
    if not actors_same:
        reasons.append("different_primary_actor")
    if not actors_same:
        reasons.append("different_primary_actor")
        return "separate_event", reasons
    return "uncertain", reasons or ["insufficient_features"]
