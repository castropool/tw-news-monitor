"""Program 4 event grouping: the week's in-scope articles as events, with their facts.

Reuses the existing, tested military event pipeline (``app.military.events``:
deterministic complete-link clustering with inspectable pair decisions and
editorial importance) instead of adding another clusterer.  Each event gets
the numbers of the week's facts (``pilot.claims``) whose source article is in
it, so the weekly draft can show "what happened" once per event, with the
supporting facts cited.

If the military rules cannot be loaded the feature degrades to "no events";
the rest of the draft is unaffected.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta

from ..database import Database
from . import claims as cl

logger = logging.getLogger(__name__)
LEVEL_LABELS = {"major": "重大", "important": "重要", "normal": "一般", "low": "次要", "factual": "事實"}


def week_articles(conn: sqlite3.Connection, start: date, end: date):
    cats = sorted(cl.inscope_categories())
    marks = ",".join("?" * len(cats))
    rows = conn.execute(
        Database._ARTICLE_SELECT
        + f" WHERE category IN ({marks}) AND (delivery_eligible = 1 OR filter_reason = 'research_only')"
        " AND substr(COALESCE(published_at, fetched_at), 1, 10) BETWEEN ? AND ?",
        (*cats, start.isoformat(), end.isoformat()),
    ).fetchall()
    return [Database._row_to_article(r) for r in rows]


def week_events(conn: sqlite3.Connection, start: date, end: date, facts: list[dict], *, rules=None) -> list[dict]:
    """Events for [start, end], each with ``fact_numbers`` (1-based, as in ``facts``)."""
    articles = week_articles(conn, start, end)
    if not articles:
        return []
    try:
        from ..military.config import load_rules
        from ..military.events import build_events

        events = build_events(articles, rules or load_rules())
    except Exception as exc:  # noqa: BLE001 - grouping is optional
        logger.warning("Pilot event grouping unavailable: %s", exc)
        return []
    by_url: dict[str, list[int]] = {}
    for n, f in enumerate(facts, 1):
        by_url.setdefault(f["url"], []).append(n)
    for e in events:
        e["fact_numbers"] = sorted(n for a in e["articles"] for n in by_url.get(a["url"], []))
        e["merged_by_facts"] = False
    return merge_by_shared_facts(events, facts)


def fact_key(f: dict) -> tuple | None:
    """Identity of a reported fact; None when too vague to merge on."""
    if not f.get("event_date") or f.get("quantity") is None:
        return None
    return (f["claim_type"], f["event_date"], f["quantity"], (f.get("unit") or ""), (f.get("actor") or ""))


def merge_by_shared_facts(events: list[dict], facts: list[dict]) -> list[dict]:
    """Merge events that report the same checked fact (type, date, count, unit, actor).

    The military clusterer needs a specific anchor (weapon, named exercise) and
    so keeps daily-count reports from different outlets apart; a shared,
    quote-verified fact is direct evidence that they describe one event.
    """
    parent = list(range(len(events)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    owner: dict[tuple, int] = {}
    for i, e in enumerate(events):
        for n in e["fact_numbers"]:
            key = fact_key(facts[n - 1])
            if key is None:
                continue
            if key in owner:
                parent[find(i)] = find(owner[key])
            else:
                owner[key] = i
    groups: dict[int, list[dict]] = {}
    for i, e in enumerate(events):
        groups.setdefault(find(i), []).append(e)
    merged = []
    for members in groups.values():
        if len(members) == 1:
            merged.append(members[0])
            continue
        lead = max(members, key=lambda e: (e["importance"] or -1, e["article_count"], e["event_id"]))
        e = dict(lead)
        e["articles"] = sorted((a for m in members for a in m["articles"]), key=lambda a: a["url"])
        e["article_count"] = len(e["articles"])
        e["sources"] = sorted({s for m in members for s in m["sources"]})
        e["fact_numbers"] = sorted({n for m in members for n in m["fact_numbers"]})
        e["first_seen"] = min(m["first_seen"] for m in members)
        e["last_updated"] = max(m["last_updated"] for m in members)
        scores = [m["importance"] for m in members if m["importance"] is not None]
        e["importance"] = max(scores) if scores else None
        e["merged_by_facts"] = True
        e["merged_event_ids"] = sorted(m["event_id"] for m in members)
        merged.append(e)
    merged.sort(key=lambda e: (e["importance"] is None, -(e["importance"] or 0), e["first_seen"], e["event_id"]))
    return merged


def render_events_markdown(events: list[dict], *, limit: int = 15) -> str:
    if not events:
        return "（本週試點範圍內沒有可歸併的軍事事件。）"
    lines = []
    for e in events[:limit]:
        level = LEVEL_LABELS.get(e["level"], e["level"])
        score = f"{e['importance']}分" if e["importance"] is not None else "不評分"
        cites = "".join(f"[{n}]" for n in e["fact_numbers"]) or "（尚無抽取事實）"
        lines.append(
            f"- **{e['title']}**（{e['category_name']}；{level}，{score}；{e['article_count']} 篇，"
            f"來源：{'、'.join(e['sources'])}；首見 {e['first_seen'][:16].replace('T', ' ')}）事實：{cites}"
        )
    if len(events) > limit:
        lines.append(f"- 另有 {len(events) - limit} 個較次要事件未列出。")
    return "\n".join(lines)


def events_payload(events: list[dict], *, limit: int = 15) -> list[dict]:
    return [
        {"title": e["title"], "category": e["category_name"], "importance": e["importance"],
         "articles": e["article_count"], "sources": e["sources"], "facts": e["fact_numbers"]}
        for e in events[:limit]
    ]
