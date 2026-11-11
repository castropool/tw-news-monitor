"""Generic runner for frozen golden cases (Phase 1.5)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.election_candidates.assertion_classifier import (
    build_assertion_profile,
    classify_article_assertions,
)
from app.election_candidates.candidate_models import MatchInfo, NormalizedArticle
from app.election_candidates.candidate_router import route_candidate
from app.election_candidates.candidate_scorer import finalize_risk_level, score_candidate
from app.election_candidates.event_clusterer import (
    cluster_articles,
    extract_event_date,
    relationship_between,
)
from app.election_candidates.event_type_dictionary import classify_event_type
from app.election_candidates.formal_duplicate_checker import check_candidate_duplicates
from app.election_candidates.relevance_calibrator import assign_relevance_label
from app.election_candidates.article_normalizer import normalize_domain


FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "election_candidates"

FIXTURE_EVENT_SOURCE_DOMAINS = {
    "evt_golden_nom_20260121": ["cna.com.tw"],
    "evt_golden_rally_20260725": ["cna.com.tw", "news.ebc.net.tw"],
    "evt_golden_visit_20260721": ["cna.com.tw"],
    "evt_golden_poll_20260720": ["cna.com.tw"],
}


def enrich_formal_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    enriched = []
    for e in events:
        d = dict(e)
        d["actors"] = set(json.loads(d.get("actors_json", "[]") or "[]"))
        d["issues"] = set(json.loads(d.get("issues_json", "[]") or "[]"))
        d["source_domains"] = set(FIXTURE_EVENT_SOURCE_DOMAINS.get(d["event_id"], []))
        enriched.append(d)
    return enriched


def load_articles() -> dict[str, dict[str, Any]]:
    rows = {}
    for line in (FIXTURE / "golden_articles_v2.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows[row["id"]] = row
    return rows


def load_cases() -> list[dict[str, Any]]:
    return json.loads((FIXTURE / "golden_candidate_cases_v2.json").read_text(encoding="utf-8"))


def load_formal_events() -> list[dict[str, Any]]:
    rows = []
    for line in (FIXTURE / "golden_formal_events_v2.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_sources() -> list[dict[str, Any]]:
    rows = []
    for line in (FIXTURE / "golden_sources_v2.jsonl").read_text(encoding="utf-8").splitlines():
        if line.strip():
            rows.append(json.loads(line))
    return rows


def load_duplicate_cases() -> list[dict[str, Any]]:
    return json.loads((FIXTURE / "golden_formal_duplicate_cases.json").read_text(encoding="utf-8"))


def article_from_row(row: dict[str, Any]) -> NormalizedArticle:
    m = row.get("match") or {}
    match = MatchInfo(
        city=m.get("city", "tainan"),
        relevance=m.get("relevance", "low"),
        matched_people=list(m.get("matched_people", [])),
        matched_parties=list(m.get("matched_parties", [])),
        matched_issues=list(m.get("matched_issues", [])),
        matched_terms=list(m.get("matched_terms", [])),
        matched_basis=list(m.get("matched_basis", [])),
        match_rule_id="golden_fixture",
        region_match=bool(m.get("region_match", False)),
        election_context_match=bool(m.get("election_context_match", False)),
        match_score=float(m.get("match_score", 0)),
    )
    return NormalizedArticle(
        news_article_id=row["id"],
        raw_title=row.get("title", ""),
        normalized_title=row.get("title", ""),
        raw_url=row.get("url", ""),
        normalized_url=row.get("url", ""),
        source_name=row.get("source_name", ""),
        normalized_source_name=row.get("source_name", ""),
        normalized_domain="",
        category=row.get("category", "politics"),
        summary=row.get("summary", ""),
        published_at=row.get("published_at", ""),
        collected_at=row.get("published_at", ""),
        match=match,
    )


def run_case(
    case: dict[str, Any],
    articles: dict[str, dict[str, Any]],
    formal_events: list[dict[str, Any]],
    config,
) -> dict[str, Any]:
    formal_events = enrich_formal_events(formal_events)
    rows = [articles[aid] for aid in case["article_ids"]]
    arts = [article_from_row(r) for r in rows]
    clusters = cluster_articles(arts, config)
    results = []
    for cluster in clusters:
        cluster_arts = cluster.sorted_articles()
        anchor = cluster.anchor
        label, label_reasons, _ = assign_relevance_label(anchor, config)
        assertions = []
        for a in cluster_arts:
            assertions.extend(classify_article_assertions(a, "cand_x", "run_x", config))
        profile = build_assertion_profile(assertions)
        date, basis, conf = extract_event_date(anchor, config)
        event_type = classify_event_type(anchor.normalized_title, anchor.summary, config)
        sources = [
            {
                "candidate_source_id": f"csrc_{a.source_name}",
                "normalized_source_name": a.source_name,
                "normalized_domain": "",
                "formal_source_id": "",
                "formal_match_status": "new_candidate_source",
                "formal_match_basis": "fixture",
            }
            for a in cluster_arts
        ]
        candidate = {
            "candidate_id": "cand_x",
            "relevance_label": label,
            "candidate_title": anchor.normalized_title,
            "candidate_summary": "據報導",
            "source_count": len({a.source_name for a in cluster_arts if a.source_name}),
            "canonical_event_date": date,
            "event_date_basis": basis,
            "event_date_confidence": conf,
            "candidate_event_type": event_type,
            "primary_actor": anchor.match.matched_people[0] if anchor.match.matched_people else "",
            "secondary_actors_json": "[]",
            "themes_json": "[]",
            "keywords_json": "[]",
            "risk_level": "low",
        }
        candidate_domains = {normalize_domain(a.raw_url) for a in cluster_arts if a.raw_url}
        suggestions = check_candidate_duplicates(
            {
                "candidate_id": "cand_x",
                "primary_actor": candidate["primary_actor"],
                "secondary_actors_json": "[]",
                "themes_json": "[]",
                "keywords_json": json.dumps(list(anchor.match.matched_terms), ensure_ascii=False),
                "canonical_event_date": date,
                "candidate_event_type": event_type,
                "candidate_title": anchor.normalized_title,
                "candidate_summary": "",
            },
            formal_events,
            config,
            "run_x",
            candidate_domains,
        )
        scores = score_candidate(candidate, cluster_arts, sources, assertions, profile, suggestions, config)
        scores["risk_level"] = finalize_risk_level(scores["risk_level"], candidate)
        candidate.update(scores)
        status, reasons = route_candidate(candidate, scores, profile, config)
        results.append(
            {
                "cluster_articles": [a.news_article_id for a in cluster_arts],
                "relevance_label": label,
                "relevance_reasons": label_reasons,
                "event_type": event_type,
                "event_date": date,
                "event_date_basis": basis,
                "assertion_kinds": sorted({a["assertion_kind"] for a in assertions}),
                "assertions": assertions,
                "formal_duplicate_score": scores["formal_duplicate_score"],
                "formal_duplicate_top": [s["formal_event_id"] for s in suggestions[:3]],
                "route_status": status,
                "route_reasons": reasons,
            }
        )
    relationship = "same_event"
    if len(results) >= 2:
        relationship = relationship_between(clusters[0], clusters[1], config)[0]
    return {
        "case_id": case["case_id"],
        "split": case["split"],
        "cluster_count": len(results),
        "clusters": [r["cluster_articles"] for r in results],
        "relationship": relationship,
        "results": results,
    }
