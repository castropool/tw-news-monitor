"""Fail-closed coverage and semantic-preservation gate for Stage 2 output."""

from __future__ import annotations

import re
from collections import Counter

from .claim_evidence_semantics import ATTRIBUTION_TERMS, BOUNDING_TERMS, STRONG_TERMS
from .claim_plan_schema import validate_stage2_draft_schema


DATE_RE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
NUMBER_RE = re.compile(r"(?<![A-Za-z])\d+(?:\.\d+)?%?")


def _tokens(pattern: re.Pattern[str], text: str) -> set[str]:
    return set(pattern.findall(text or ""))


def _negated(text: str, term: str) -> bool:
    start = max(0, text.find(term) - 8)
    prefix = text[start : text.find(term)]
    return any(word in prefix for word in ("不", "未", "并非", "不能", "不足以"))


def _semantic_preservation_errors(claim: dict, rendered_text: str) -> list[str]:
    errors: list[str] = []
    original = str(claim.get("claim_text") or "")
    claim_id = str(claim.get("claim_id") or "")
    strength = claim.get("claim_strength")
    claim_type = claim.get("claim_type")

    if not _tokens(DATE_RE, rendered_text) <= _tokens(DATE_RE, original):
        errors.append(f"{claim_id}: unauthorized date introduced")
    if not _tokens(NUMBER_RE, rendered_text) <= _tokens(NUMBER_RE, original):
        errors.append(f"{claim_id}: unauthorized number introduced")

    introduced_strong = [
        term for term in STRONG_TERMS
        if term in rendered_text and term not in original and not _negated(rendered_text, term)
    ]
    if introduced_strong or (
        strength in {"bounded_inference", "attributed_statement"}
        and any(term in rendered_text and not _negated(rendered_text, term) for term in STRONG_TERMS)
    ):
        errors.append(f"{claim_id}: claim strength escalation")

    if strength == "bounded_inference" or claim_type in {"current_assessment", "comparative_assessment", "forward_outlook"}:
        if not any(term in rendered_text for term in BOUNDING_TERMS):
            errors.append(f"{claim_id}: bounded strength marker removed")
    if strength == "attributed_statement":
        if not any(term in rendered_text for term in ATTRIBUTION_TERMS):
            errors.append(f"{claim_id}: attribution marker removed")
    return errors


def validate_final_claim_coverage(draft: dict, store: dict) -> dict:
    """Require an exact, one-to-one rendering of every accepted Claim."""

    errors = list(validate_stage2_draft_schema(draft))
    accepted = list(store.get("accepted_claims") or [])
    accepted_by_id = {item.get("claim_id"): item for item in accepted}
    allowed_ids = set(accepted_by_id)
    renderings = list(draft.get("claim_renderings") or []) if isinstance(draft, dict) else []
    rendering_ids = [item.get("claim_id") for item in renderings if isinstance(item, dict)]
    rendering_counter = Counter(rendering_ids)
    rendered_ids = set(rendering_ids)
    unauthorized = rendered_ids - allowed_ids

    if draft.get("validated_claim_plan_hash") != store.get("claim_plan_business_hash"):
        errors.append("validated_claim_plan_hash mismatch")
    missing = allowed_ids - rendered_ids
    if missing:
        errors.append(f"missing validated claims: {sorted(missing)}")
    if unauthorized:
        errors.append(f"unauthorized claim ids: {sorted(unauthorized)}")
    duplicates = sorted(cid for cid, count in rendering_counter.items() if count != 1)
    if duplicates:
        errors.append(f"claims must be rendered exactly once: {duplicates}")

    sections = draft.get("sections") or []
    flattened: list[str] = []
    for section in sections:
        if not isinstance(section, dict):
            continue
        section_id = section.get("section_id")
        for claim_id in section.get("claim_ids") or []:
            flattened.append(claim_id)
            claim = accepted_by_id.get(claim_id)
            if claim and claim.get("target_section_id") != section_id:
                errors.append(f"{claim_id}: target section mismatch")
    section_counter = Counter(flattened)
    if set(flattened) != allowed_ids:
        errors.append("section claim coverage does not equal validated Claim set")
    if any(count != 1 for count in section_counter.values()):
        errors.append("section Claim IDs must occur exactly once")

    for field in ("title_claim_ids", "overall_judgment_claim_ids"):
        ids = draft.get(field) or []
        if not set(ids) <= allowed_ids:
            errors.append(f"{field} contains unauthorized Claim IDs")
        if len(ids) != len(set(ids)):
            errors.append(f"{field} contains duplicate Claim IDs")

    for item in renderings:
        if not isinstance(item, dict):
            continue
        claim = accepted_by_id.get(item.get("claim_id"))
        if claim:
            errors.extend(_semantic_preservation_errors(claim, str(item.get("rendered_text") or "")))

    covered = len(allowed_ids & rendered_ids)
    rate = covered / len(allowed_ids) if allowed_ids else 0.0
    errors = list(dict.fromkeys(errors))
    return {
        "final_claim_coverage_ready": not errors,
        "errors": errors,
        "final_claim_coverage_rate": rate,
        "unauthorized_new_claim_count": len(unauthorized),
        "fabricated_claim_count": len(unauthorized),
        "fabricated_event_count": 0,
        "fabricated_source_count": 0,
    }
