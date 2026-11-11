"""Explainable relevance calibration for mayoral election news (per-election via config)."""

from __future__ import annotations

from .action_normalizer import normalize_action
from .candidate_models import NormalizedArticle


def _contains_any(text: str, markers: list[str]) -> bool:
    return any(m and m in text for m in markers)


def assign_relevance_label(article: NormalizedArticle, config) -> tuple[str, list[str], dict[str, bool]]:
    """Return (label, reasons, evidence)."""
    title = article.raw_title or article.normalized_title
    text = f"{title} {article.summary}"
    rel = config.get("relevance", {}) or {}
    national = rel.get("national_topic_markers", []) or []
    other_race = rel.get("other_race_markers", []) or []
    party_internal = rel.get("party_internal_markers", []) or []
    direct_action_markers = rel.get("direct_action_markers", []) or []
    statement_verbs = rel.get("direct_statement_verbs", []) or []

    region = bool(article.match.region_match)
    actors = list(article.match.matched_people)
    election = bool(article.match.election_context_match or article.match.matched_issues)
    action, phrase = normalize_action(title, config)
    statement = _contains_any(title, statement_verbs) or ("：" in title or ":" in title)
    media_markers = (config.get("assertion_classifier.media_interpretation_markers", []) or [])
    media = _contains_any(title, media_markers)
    uncertain_markers = (config.get("assertion_classifier.uncertain_markers", []) or [])
    uncertain = _contains_any(title, uncertain_markers)

    has_national = _contains_any(text, national)
    has_other_race = _contains_any(text, other_race)
    has_party_internal = _contains_any(text, party_internal)
    has_direct_action = _contains_any(title, direct_action_markers) or action in {
        "party_nomination", "registration", "campaign_rally", "support_organization",
        "joint_campaign_display", "campaign_launch", "campaign_event", "campaign_visit",
        "primary_procedure", "primary_result", "poll_release",
        "campaign_attack", "campaign_response", "alliance_proposal", "alliance_coordination",
        "primary_debate", "primary_registration",
    }

    evidence = {
        "region_evidence": region,
        "election_evidence": election,
        "actor_evidence": bool(actors),
        "action_evidence": bool(action) or has_direct_action,
        "negative_evidence": has_national or has_other_race or has_party_internal,
    }
    reasons: list[str] = []
    if region:
        reasons.append("region_evidence")
    if election:
        reasons.append("election_evidence")
    if actors:
        reasons.append(f"actor_evidence:{','.join(actors)}")
    if action:
        reasons.append(f"action_evidence:{phrase}")
    if has_national:
        reasons.append("negative_evidence:national_topic")
    if has_other_race:
        reasons.append("negative_evidence:other_race")
    if has_party_internal:
        reasons.append("negative_evidence:party_internal")

    collection_markers = config.get("input_filter.collection_error_markers", []) or []
    if not title.strip() and not article.summary.strip():
        return "collection_error", reasons + ["no_valid_text"], evidence
    if _contains_any(text, collection_markers):
        return "collection_error", reasons + ["collection_error_marker"], evidence

    if has_other_race and not region and not actors:
        return "irrelevant", reasons + ["other_race_without_region_or_actor"], evidence

    if uncertain:
        return "contextual", reasons + ["uncertain_report_not_direct"], evidence

    if (region or actors) and statement and election and not has_national and not has_other_race:
        return "direct_statement", reasons + ["actor_region_statement_election"], evidence

    if region and has_direct_action and not has_national:
        return "direct_event", reasons + ["region_plus_direct_action"], evidence
    if actors and has_direct_action and not has_national and not has_other_race:
        return "direct_event", reasons + ["actor_plus_direct_action"], evidence

    if (region or actors) and (
        has_national or has_other_race or has_party_internal or media or not (action or statement)
    ):
        return "contextual", reasons + ["related_but_not_direct"], evidence

    if actors and statement and not election:
        return "contextual", reasons + ["statement_without_election_context"], evidence

    if election and has_national:
        return "contextual", reasons + ["election_background_national"], evidence

    return "irrelevant", reasons + ["no_direct_election_relation"], evidence
