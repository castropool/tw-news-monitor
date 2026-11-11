"""Config-driven event type classification using the formal event enum."""

from __future__ import annotations

from typing import Any


def load_definitions(config) -> dict[str, Any]:
    return config.get("event_types", {}) or {}


def classify_event_type(title: str, summary: str = "", config=None) -> str:
    defs = load_definitions(config)
    priority = defs.get("priority", []) or []
    definitions = defs.get("definitions", {}) or {}
    text = f"{title} {summary}".strip()
    best_type = "unknown"
    best_hits = 0
    for event_type in priority:
        d = definitions.get(event_type, {}) or {}
        positives = d.get("positive_patterns", []) or []
        negatives = d.get("negative_patterns", []) or []
        required = d.get("required_actor_or_action", "")
        hits = sum(1 for p in positives if p and p in text)
        if hits == 0:
            continue
        if any(n and n in text for n in negatives):
            continue
        if required and required not in text:
            continue
        if hits > best_hits:
            best_type = event_type
            best_hits = hits
    return best_type


def event_type_aliases() -> dict[str, set[str]]:
    """Canonical aliases for duplicate checking (kept in sync with formal enum)."""
    return {
        "primary_procedure": {"primary_procedure", "primary_debate", "primary_result"},
        "primary_result": {"primary_result", "primary_procedure"},
        "party_nomination": {"party_nomination", "candidate_announcement"},
        "campaign_attack": {"campaign_attack", "scandal_allegation"},
        "alliance_proposal": {"alliance_proposal", "alliance_agreement"},
        "campaign_launch": {"campaign_launch", "joint_campaign", "campaign_event"},
        "campaign_event": {"campaign_event", "campaign_launch", "joint_campaign"},
        "governance_event": {"governance_event", "disaster_response"},
        "disaster_response": {"disaster_response", "governance_event"},
    }
