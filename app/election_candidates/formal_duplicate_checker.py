"""Read-only duplicate check of candidate events against formal events.

This module opens election_context.db with mode=ro and only executes SELECT
statements.  It never imports or calls formal write methods.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from .article_normalizer import normalize_domain
from .action_normalizer import action_family, normalize_action


EVENT_TYPE_ALIASES: dict[str, set[str]] = {
    "primary_procedure": {"primary_procedure", "primary_debate", "primary_result"},
    "primary_result": {"primary_result", "primary_procedure"},
    "party_nomination": {"party_nomination", "candidate_announcement"},
    "candidate_announcement": {"candidate_announcement", "party_nomination"},
    "campaign_attack": {"campaign_attack", "scandal_allegation"},
    "scandal_allegation": {"scandal_allegation", "campaign_attack", "judicial_event"},
    "alliance_proposal": {"alliance_proposal", "alliance_agreement"},
    "alliance_agreement": {"alliance_agreement", "alliance_proposal"},
    "poll_release": {"poll_release"},
    "policy_proposal": {"policy_proposal"},
    "campaign_launch": {"campaign_launch", "joint_campaign", "fundraising"},
    "joint_campaign": {"joint_campaign", "campaign_launch"},
    "governance_event": {"governance_event", "disaster_response"},
    "disaster_response": {"disaster_response", "governance_event"},
}


def _parse_date(value: str) -> datetime | None:
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is not None:
            dt = dt.astimezone(timezone(timedelta(hours=8)))
            dt = dt.replace(tzinfo=None)
        return dt
    except ValueError:
        return None


def _date_score(candidate_date: str, formal_date: str, config) -> float:
    cd = _parse_date(candidate_date)
    fd = _parse_date(formal_date)
    if cd is None or fd is None:
        return 0.0
    days = abs((cd - fd).days)
    if days <= int(config.get("duplicate_detection.date_window_days.exact", 1)):
        return 1.0
    if days <= int(config.get("duplicate_detection.date_window_days.close", 7)):
        return 0.7
    if days <= int(config.get("duplicate_detection.date_window_days.loose", 30)):
        return 0.3
    return 0.0


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 0.0
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)


def _actor_set(raw_actors: Any) -> set[str]:
    if isinstance(raw_actors, str):
        try:
            raw_actors = json.loads(raw_actors)
        except json.JSONDecodeError:
            raw_actors = [raw_actors]
    if not isinstance(raw_actors, list):
        return set()
    return {str(a).strip() for a in raw_actors if str(a).strip()}


def _keyword_tokens(text: str, terms: list[str]) -> set[str]:
    if not text:
        return set()
    return {t for t in terms if t and t in text}


def load_formal_events(db_path: str | Path, election_id: str, config) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            "SELECT event_id, occurred_at, event_type, title, fact_summary, "
            "actors_json, issues_json, analysis_json FROM election_events WHERE election_id=? "
            "ORDER BY event_id",
            (election_id,),
        ).fetchall()
        src_rows = conn.execute(
            "SELECT es.event_id, s.url FROM event_sources es "
            "JOIN sources s ON s.source_id = es.source_id"
        ).fetchall()
        sources_by_event: dict[str, set[str]] = {}
        for r in src_rows:
            sources_by_event.setdefault(r[0], set()).add(normalize_domain(r[1] or ""))
        result = []
        for r in rows:
            d = dict(r)
            d["actors"] = _actor_set(d.get("actors_json"))
            d["issues"] = _actor_set(d.get("issues_json"))
            d["source_domains"] = sources_by_event.get(d["event_id"], set())
            result.append(d)
        return result
    finally:
        conn.close()


def load_formal_sources(db_path: str | Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in conn.execute(
            "SELECT source_id, publisher, title, url, published_at FROM sources ORDER BY source_id"
        ).fetchall()]
    finally:
        conn.close()


def check_candidate_duplicates(
    candidate: dict[str, Any],
    formal_events: list[dict[str, Any]],
    config,
    run_id: str,
    candidate_source_domains: set[str] | None = None,
) -> list[dict[str, Any]]:
    likely = float(config.get("duplicate_detection.likely_duplicate_threshold", 0.90))
    possible = float(config.get("duplicate_detection.possible_match_threshold", 0.65))
    weights = config.get("duplicate_detection.weights", {}) or {}
    w_date = float(weights.get("date", 0.20))
    w_actor = float(weights.get("actor", 0.25))
    w_type = float(weights.get("event_type", 0.15))
    w_action = float(weights.get("action", 0.20))
    w_keyword = float(weights.get("keyword", 0.15))
    w_source = float(weights.get("source_overlap", 0.05))

    candidate_actors = set(
        [candidate.get("primary_actor", "") if candidate.get("primary_actor") else ""]
    )
    candidate_actors.update(_actor_set(candidate.get("secondary_actors_json", "[]")))
    candidate_actors.discard("")
    candidate_issues = _actor_set(candidate.get("themes_json", "[]"))
    candidate_keywords = _actor_set(candidate.get("keywords_json", "[]"))
    candidate_date = candidate.get("canonical_event_date", "")
    candidate_type = candidate.get("candidate_event_type", "")
    candidate_text = f"{candidate.get('candidate_title', '')} {candidate.get('candidate_summary', '')}"
    terms = sorted(set(candidate_keywords) | set(candidate_issues) | set(candidate_actors))
    if not terms:
        terms = list(candidate_actors)
    candidate_tokens = _keyword_tokens(candidate_text, terms)
    candidate_domains = candidate_source_domains or set()

    suggestions: list[dict[str, Any]] = []
    for evt in formal_events:
        reasons: list[str] = []
        conflicts: list[str] = []
        d_score = _date_score(candidate_date, evt.get("occurred_at", ""), config)
        a_score = _jaccard(candidate_actors, evt["actors"])
        if candidate_type and evt.get("event_type"):
            if candidate_type == evt["event_type"]:
                t_score = 1.0
            elif candidate_type in EVENT_TYPE_ALIASES and evt["event_type"] in EVENT_TYPE_ALIASES[candidate_type]:
                t_score = 0.7
            else:
                t_score = 0.2
        else:
            t_score = 0.3
        formal_text = (
            f"{evt.get('title', '')} {evt.get('fact_summary', '')} {evt.get('analysis_json', '')}"
        )
        formal_tokens = _keyword_tokens(formal_text, terms)
        cand_action, _ = normalize_action(candidate_text, config)
        formal_action, _ = normalize_action(formal_text, config)
        if cand_action and formal_action:
            if cand_action == formal_action:
                action_score = 1.0
            elif action_family(cand_action) == action_family(formal_action):
                action_score = 0.6
            else:
                action_score = 0.1
        else:
            action_score = 0.0
        kw_score = _jaccard(candidate_keywords | candidate_issues, evt["issues"])
        source_score = _jaccard(candidate_domains, evt["source_domains"])

        total = (
            w_date * d_score
            + w_actor * a_score
            + w_type * t_score
            + w_action * action_score
            + w_keyword * kw_score
            + w_source * source_score
        )

        if d_score >= 0.7:
            reasons.append(f"date_within_{int(config.get('duplicate_detection.date_window_days.close', 7))}_days")
        elif d_score > 0:
            reasons.append("date_within_30_days")
        else:
            conflicts.append("date_not_close_or_unknown")
        if a_score >= 0.5:
            reasons.append("actor_overlap")
        else:
            conflicts.append("actors_differ")
        if t_score >= 0.7:
            reasons.append("event_type_match")
        if action_score >= 0.4:
            reasons.append("action_keyword_overlap")
        else:
            conflicts.append("action_keywords_differ")
        if kw_score >= 0.4:
            reasons.append("keyword_overlap")
        if source_score >= 0.5:
            reasons.append("source_overlap")

        if candidate_date and not evt.get("occurred_at"):
            conflicts.append("formal_event_date_missing")
        if not candidate_date:
            conflicts.append("candidate_date_unknown")

        if total >= likely:
            action = "likely_duplicate"
        elif total >= possible:
            if not candidate_date or not candidate_actors:
                action = "manual_review"
            elif d_score >= 0.7 and a_score >= 0.5:
                action = "possible_subevent"
            else:
                action = "possible_related_event"
        else:
            action = "no_material_match"

        suggestion_id = "dup_" + hashlib.sha256(
            f"{candidate['candidate_id']}|{evt['event_id']}".encode("utf-8")
        ).hexdigest()[:12]
        suggestions.append(
            {
                "suggestion_id": suggestion_id,
                "candidate_id": candidate["candidate_id"],
                "formal_event_id": evt["event_id"],
                "similarity_score": round(total, 4),
                "date_score": round(d_score, 4),
                "actor_score": round(a_score, 4),
                "event_type_score": round(t_score, 4),
                "keyword_score": round(kw_score, 4),
                "source_overlap_score": round(source_score, 4),
                "matching_reasons_json": json.dumps(reasons, ensure_ascii=False),
                "conflicting_reasons_json": json.dumps(conflicts, ensure_ascii=False),
                "suggested_action": action,
                "created_run_id": run_id,
            }
        )

    suggestions.sort(key=lambda s: (-s["similarity_score"], s["formal_event_id"]))
    return suggestions[: int(config.get("duplicate_detection.max_suggestions_per_candidate", 5))]


def formal_event_ids(db_path: str | Path, election_id: str) -> set[str]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    try:
        return {
            r[0]
            for r in conn.execute(
                "SELECT event_id FROM election_events WHERE election_id=?", (election_id,)
            ).fetchall()
        }
    finally:
        conn.close()
