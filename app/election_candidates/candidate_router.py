"""Automatic candidate status routing (Phase 1.5)."""

from __future__ import annotations

from typing import Any


def route_candidate(
    candidate: dict[str, Any],
    scores: dict[str, float],
    profile: dict[str, Any],
    config,
    existing_duplicate: bool = False,
) -> tuple[str, list[str]]:
    router = config.get("router", {}) or {}
    reasons: list[str] = []
    label = candidate.get("relevance_label", "")

    if label in ("irrelevant", "collection_error"):
        return "auto_reject", reasons + [f"relevance_label_{label}"]

    if label == "contextual":
        if not profile.get("has_observed_fact") and not profile.get("has_actor_statement"):
            return "hold", reasons + ["contextual_without_actionable_assertion"]
        return "context_only", reasons + ["contextual_no_direct_event_or_statement"]

    if existing_duplicate or scores["formal_duplicate_score"] >= float(
        config.get("duplicate_detection.likely_duplicate_threshold", 0.90)
    ):
        return "duplicate_candidate", reasons + ["formal_duplicate_or_existing_candidate"]

    if label == "direct_event":
        if not profile.get("has_observed_fact"):
            reasons.append("direct_event_without_observed_fact")
        if candidate.get("event_date_basis", "") == "unknown":
            reasons.append("date_unknown")
        if not candidate.get("source_count", 0):
            reasons.append("no_valid_source")
        if reasons:
            return "hold", list(dict.fromkeys(reasons))
        return "review_required", ["direct_event_eligible"]

    if label == "direct_statement":
        if not profile.get("has_actor_statement"):
            reasons.append("direct_statement_without_actor_statement")
        if candidate.get("risk_level", "low") == "low" and router.get(
            "direct_statement_min_risk", "medium"
        ) != "low":
            reasons.append("direct_statement_risk_too_low")
        if candidate.get("event_date_basis", "") == "unknown":
            reasons.append("date_unknown")
        if not candidate.get("source_count", 0):
            reasons.append("no_valid_source")
        if reasons:
            return "hold", list(dict.fromkeys(reasons))
        return "review_required", ["direct_statement_eligible"]

    # Fallback for candidates without an explicit relevance label.
    if not profile.get("has_observed_fact") and not profile.get("has_actor_statement"):
        reasons.append("no_observed_fact_no_statement")
    if candidate.get("event_date_basis", "") == "unknown" and router.get("date_unknown_hold", True):
        reasons.append("date_unknown")
    if scores["cluster_confidence"] < float(router.get("min_cluster_confidence_for_review", 0.35)):
        reasons.append("low_cluster_confidence")
    if scores["source_confidence"] < float(router.get("min_source_confidence_for_review", 0.30)):
        reasons.append("low_source_confidence")
    if scores["completeness_score"] < float(router.get("min_completeness_for_review", 0.40)):
        reasons.append("low_completeness")
    if reasons:
        return "hold", list(dict.fromkeys(reasons))
    return "review_required", ["eligible_for_review"]
