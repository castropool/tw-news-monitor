"""Deterministic, explainable scoring for candidate events."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def _clamp(value: float) -> float:
    return max(0.0, min(1.0, value))


def relevance_score(candidate: dict[str, Any], articles, config) -> float:
    weights = config.get("scoring.relevance", {}) or {}
    w_match = float(weights.get("match_score_weight", 0.5))
    w_cov = float(weights.get("actor_issue_coverage_weight", 0.3))
    w_region = float(weights.get("region_weight", 0.2))
    match_scores = [a.match.match_score for a in articles if a.match.match_score]
    base = max(match_scores) if match_scores else 0.2
    coverage = 0.0
    for a in articles:
        if a.match.matched_people or a.match.matched_parties:
            coverage = max(coverage, 1.0)
        elif a.match.matched_issues:
            coverage = max(coverage, 0.5)
    region = 1.0 if any(a.match.region_match for a in articles) else 0.5
    return _clamp(w_match * base + w_cov * coverage + w_region * region)


def completeness_score(candidate: dict[str, Any], articles, sources, assertions, config) -> float:
    weights = config.get("scoring.completeness", {}) or {}
    parts = {
        "date": 1.0 if candidate.get("canonical_event_date") else 0.0,
        "actors": 1.0 if (
            candidate.get("primary_actor")
            or (candidate.get("secondary_actors_json") or "") not in ("", "[]")
        ) else 0.0,
        "text": 1.0 if any(a.normalized_title or a.summary for a in articles) else 0.0,
        "sources": 1.0 if sources else 0.0,
        "event_type": 1.0 if candidate.get("candidate_event_type") else 0.0,
        "summary": 1.0 if candidate.get("candidate_summary") else 0.0,
    }
    total_weight = sum(float(weights.get(k, 0)) for k in parts)
    if total_weight <= 0:
        total_weight = 1.0
    score = sum(float(weights.get(k, 0)) * v for k, v in parts.items()) / total_weight
    return _clamp(score)


def cluster_confidence(candidate: dict[str, Any], articles, config) -> float:
    weights = config.get("scoring.cluster_confidence", {}) or {}
    w_count = float(weights.get("article_count_weight", 0.35))
    w_src = float(weights.get("source_count_weight", 0.30))
    w_date = float(weights.get("date_consistency_weight", 0.20))
    w_actor = float(weights.get("actor_consistency_weight", 0.15))

    n = len(articles)
    article_part = min(1.0, n / 3.0)
    sources = {a.source_name for a in articles if a.source_name}
    source_part = min(1.0, len(sources) / 2.0)

    dates = [a.published_at[:10] for a in articles if a.published_at]
    date_part = 1.0 if len(set(dates)) <= 1 else max(0.0, 1.0 - (len(set(dates)) - 1) * 0.3)
    actors = [a.match.matched_people[0] for a in articles if a.match.matched_people]
    actor_part = 1.0 if len(set(actors)) <= 1 else max(0.0, 1.0 - (len(set(actors)) - 1) * 0.25)
    return _clamp(w_count * article_part + w_src * source_part + w_date * date_part + w_actor * actor_part)


def date_confidence(candidate: dict[str, Any]) -> float:
    basis = candidate.get("event_date_basis", "")
    if basis == "inferred_from_publication":
        return 0.3
    if basis == "unknown":
        return 0.0
    if basis.startswith("explicit_in"):
        return {"high": 1.0, "medium": 0.6, "low": 0.3}.get(
            candidate.get("event_date_confidence", "medium"), 0.6
        )
    return {"high": 1.0, "medium": 0.6, "low": 0.3, "unknown": 0.0}.get(
        candidate.get("event_date_confidence", "unknown"), 0.0
    )


def source_confidence(candidate: dict[str, Any], sources, config) -> float:
    weights = config.get("scoring.source_confidence", {}) or {}
    w_resolved = float(weights.get("resolved_source_weight", 0.6))
    w_count = float(weights.get("source_count_weight", 0.4))
    if not sources:
        return 0.0
    resolved = sum(1 for s in sources if s.get("formal_match_status") in ("exact", "normalized_match"))
    resolved_part = resolved / len(sources)
    count_part = min(1.0, len(sources) / 2.0)
    return _clamp(w_resolved * resolved_part + w_count * count_part)


def assertion_risk_score(profile: dict[str, Any], config) -> float:
    weights = config.get("scoring.assertion_risk", {}) or {}
    w_alleg = float(weights.get("allegation_weight", 0.35))
    w_unc = float(weights.get("uncertain_weight", 0.30))
    w_media = float(weights.get("media_interpretation_weight", 0.20))
    w_unknown = float(weights.get("unknown_weight", 0.15))
    counts = profile.get("counts", {})
    total = max(1, sum(counts.values()))
    score = (
        w_alleg * counts.get("allegation", 0) / total
        + w_unc * counts.get("uncertain_report", 0) / total
        + w_media * counts.get("media_interpretation", 0) / total
        + w_unknown * counts.get("unknown", 0) / total
    )
    return _clamp(score)


def risk_level(risk_score: float, profile: dict[str, Any], date_confidence: float, config) -> str:
    levels = config.get("scoring.risk_levels", {}) or {}
    low_max = float(levels.get("low_max_risk", 0.30))
    med_max = float(levels.get("medium_max_risk", 0.60))
    if risk_score >= float(levels.get("high_min_risk", 0.60)):
        return "high"
    if date_confidence == 0.0:
        return "high"
    if not profile.get("has_observed_fact") and (
        profile.get("has_allegation")
        or profile.get("has_uncertain_report")
        or (profile.get("has_media_interpretation") and not profile.get("has_actor_statement"))
    ):
        return "high"
    if risk_score > med_max:
        return "high"
    if risk_score > low_max:
        return "medium"
    return "low"


def score_candidate(
    candidate: dict[str, Any],
    articles,
    sources,
    assertions,
    profile: dict[str, Any],
    duplicate_suggestions: list[dict[str, Any]],
    config,
) -> dict[str, float]:
    rel = relevance_score(candidate, articles, config)
    comp = completeness_score(candidate, articles, sources, assertions, config)
    cc = cluster_confidence(candidate, articles, config)
    dc = date_confidence(candidate)
    sc = source_confidence(candidate, sources, config)
    ar = assertion_risk_score(profile, config)
    fds = max((s["similarity_score"] for s in duplicate_suggestions), default=0.0)
    return {
        "relevance_score": round(rel, 4),
        "completeness_score": round(comp, 4),
        "cluster_confidence": round(cc, 4),
        "date_confidence": round(dc, 4),
        "source_confidence": round(sc, 4),
        "assertion_risk_score": round(ar, 4),
        "formal_duplicate_score": round(fds, 4),
        "risk_level": risk_level(ar, profile, dc, config),
    }


def finalize_risk_level(risk: str, candidate: dict[str, Any]) -> str:
    if candidate.get("relevance_label") == "direct_statement" and risk == "low":
        return "medium"
    return risk


def write_scoring_explanation(output_root, config) -> None:
    payload = {
        "weights": {
            "relevance": config.get("scoring.relevance"),
            "completeness": config.get("scoring.completeness"),
            "cluster_confidence": config.get("scoring.cluster_confidence"),
            "source_confidence": config.get("scoring.source_confidence"),
            "assertion_risk": config.get("scoring.assertion_risk"),
        },
        "risk_levels": config.get("scoring.risk_levels"),
        "routing_rules": {
            "auto_reject": [
                "no valid text/source",
                "relevance_score below router.min_relevance_score",
                "duplicate normalized URL",
                "collection error marker",
            ],
            "duplicate_candidate": [
                "formal duplicate score >= duplicate_detection.likely_duplicate_threshold",
                "same cluster fingerprint as an existing candidate",
            ],
            "hold": [
                "date unknown",
                "cluster confidence below threshold",
                "no observed fact and only allegation/uncertain/media",
                "source unresolved/new",
                "completeness below threshold",
            ],
            "review_required": "default when no blocking condition",
        },
        "notes": [
            "All scores are deterministic; no randomness or set-order dependence.",
            "Duplicate score components are individually reported for explainability.",
        ],
    }
    path = output_root / "candidate_scoring_explanation.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
