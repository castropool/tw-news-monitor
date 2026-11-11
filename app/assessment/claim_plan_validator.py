"""Fail-closed validation for Stage 1 Claim Plans."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy

from .claim_evidence_semantics import validate_claim_semantics
from .claim_evidence_validator import build_evidence_context
from .claim_plan_schema import validate_claim_plan_schema


REQUIRED_SECTIONS = {"S01", "S02", "S03", "S04", "S07", "S08"}
OPTIONAL_DISCLOSURE_SECTIONS = {"S05", "S06"}


def claim_business_hash(claim: dict) -> str:
    return hashlib.sha256(
        json.dumps(claim, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def adapt_plan_claim(claim: dict) -> dict:
    return {
        "claim_id": claim.get("claim_id"),
        "claim_type": claim.get("claim_type"),
        "claim_text": claim.get("claim_text"),
        "confidence": claim.get("confidence"),
        "material_for_report": claim.get("material_for_report"),
        "supporting_event_ids": list(claim.get("event_ids") or []),
        "supporting_poll_ids": list(claim.get("poll_ids") or []),
        "supporting_source_ids": list(claim.get("source_ids") or []),
        "supporting_snapshot_dimensions": list(claim.get("snapshot_dimensions") or []),
        "supporting_gap_ids": list(claim.get("gap_ids") or []),
        "inference_basis": str(claim.get("evidence_reasoning_summary") or ""),
        "limitations": list(claim.get("limitations") or []),
        "applies_to_period": claim.get("applies_to_period"),
    }


def _reference_errors(claim: dict, envelope: dict) -> list[str]:
    errors: list[str] = []
    events = {item.get("event_id"): item for item in envelope.get("events") or []}
    polls = {item.get("poll_id"): item for item in envelope.get("polls") or []}
    sources = {item.get("source_id") for item in envelope.get("sources") or []}
    event_ids = list(claim.get("event_ids") or [])
    poll_ids = list(claim.get("poll_ids") or [])
    source_ids = set(claim.get("source_ids") or [])
    if not set(event_ids) <= set(events):
        errors.append("invalid_event_reference")
    if not set(poll_ids) <= set(polls):
        errors.append("invalid_poll_reference")
    if not source_ids <= sources:
        errors.append("invalid_source_reference")
    cited = [events[eid] for eid in event_ids if eid in events]
    cited_polls = [polls[pid] for pid in poll_ids if pid in polls]
    if cited or cited_polls:
        if not source_ids:
            errors.append("missing_source_reference")
        allowed_union = {
            sid for item in cited for sid in item.get("allowed_source_ids") or []
        } | {
            sid for item in cited_polls for sid in item.get("allowed_poll_source_ids") or []
        }
        if not source_ids <= allowed_union:
            errors.append("source_not_allowed_for_evidence")
        if any(not (source_ids & set(item.get("allowed_source_ids") or [])) for item in cited):
            errors.append("event_without_allowed_source")
        if any(not (source_ids & set(item.get("allowed_poll_source_ids") or [])) for item in cited_polls):
            errors.append("poll_without_allowed_source")
    return errors


def _type_strength_errors(claim: dict) -> list[str]:
    errors: list[str] = []
    ctype = claim.get("claim_type")
    strength = claim.get("claim_strength")
    events = claim.get("event_ids") or []
    polls = claim.get("poll_ids") or []
    dims = claim.get("snapshot_dimensions") or []
    if strength == "unsupported":
        errors.append("unsupported_claim_strength")
    if ctype == "factual_synthesis" and not (events or polls):
        errors.append("factual_synthesis_missing_evidence")
    if ctype == "current_assessment" and not (len(events) >= 2 or (events and dims)):
        errors.append("current_assessment_insufficient_evidence")
    if ctype == "comparative_assessment" and not dims:
        errors.append("comparative_assessment_missing_dimension")
    if ctype == "forward_outlook":
        if len(events) + len(polls) < 2:
            errors.append("forward_outlook_insufficient_evidence")
        if strength != "bounded_inference":
            errors.append("forward_outlook_strength_not_bounded")
    if strength in {"bounded_inference", "strong_inference"} and not claim.get("evidence_reasoning_summary"):
        errors.append("missing_evidence_reasoning_summary")
    if strength == "strong_inference" and len(events) + len(polls) < 2:
        errors.append("strong_inference_insufficient_evidence")
    return errors


def validate_claim_plan(
    plan: dict, *, contract: dict, planner_envelope: dict, config: dict
) -> dict:
    raw = deepcopy(plan)
    schema_errors = validate_claim_plan_schema(plan)
    integrity_errors: list[str] = []
    if plan.get("election_id") != contract.get("election_id"):
        integrity_errors.append("election_id_mismatch")
    plan_period = plan.get("reporting_period") or {}
    contract_period = contract.get("report_period") or {}
    if any(
        plan_period.get(key) != contract_period.get(key)
        for key in ("period_start", "period_end")
    ):
        integrity_errors.append("reporting_period_mismatch")
    if plan.get("formal_state_hash") != planner_envelope.get("formal_state_hash"):
        integrity_errors.append("formal_state_hash_mismatch")
    if plan.get("evidence_pack_hash") != planner_envelope.get("evidence_pack_hash"):
        integrity_errors.append("evidence_pack_hash_mismatch")
    context = build_evidence_context(contract, evidence_pack=None, config=config)
    duplicate_ids = {
        cid for cid in [item.get("claim_id") for item in plan.get("claims") or []]
        if [item.get("claim_id") for item in plan.get("claims") or []].count(cid) > 1
    }
    results: list[dict] = []
    accepted: list[dict] = []
    rejected: list[dict] = []
    for claim in plan.get("claims") or []:
        reasons: list[str] = []
        if claim.get("claim_id") in duplicate_ids:
            reasons.append("duplicate_claim_id")
        reasons.extend(_reference_errors(claim, planner_envelope))
        reasons.extend(_type_strength_errors(claim))
        semantic = validate_claim_semantics(adapt_plan_claim(claim), context)
        reasons.extend(semantic.get("failures") or [])
        reasons = list(dict.fromkeys(reasons))
        record = {
            "claim_id": claim.get("claim_id"),
            "accepted": not reasons and not schema_errors and not integrity_errors,
            "validation_reasons": reasons or (
                (["claim_plan_schema_invalid"] if schema_errors else [])
                + (["claim_plan_integrity_invalid"] if integrity_errors else [])
            ),
            "claim_business_hash": claim_business_hash(claim),
        }
        results.append(record)
        if record["accepted"]:
            accepted.append(deepcopy(claim))
        else:
            rejected.append({**deepcopy(claim), "validation_reasons": record["validation_reasons"]})

    section_coverage = {}
    for sid in [f"S{i:02d}" for i in range(1, 9)]:
        ids = [item["claim_id"] for item in accepted if item.get("target_section_id") == sid]
        section_coverage[sid] = {
            "accepted_claim_ids": ids,
            "covered": bool(ids),
            "deterministic_disclosure_required": sid in OPTIONAL_DISCLOSURE_SECTIONS and not ids,
        }
    missing_required = sorted(sid for sid in REQUIRED_SECTIONS if not section_coverage[sid]["covered"])
    bounded = sum(item.get("claim_strength") == "bounded_inference" for item in accepted)
    forward = sum(item.get("claim_type") == "forward_outlook" for item in accepted)
    coverage_errors = []
    if missing_required:
        coverage_errors.append(f"missing_required_sections:{','.join(missing_required)}")
    if bounded < 1:
        coverage_errors.append("missing_bounded_inference")
    if forward < 1:
        coverage_errors.append("missing_forward_outlook")
    if schema_errors or integrity_errors or coverage_errors:
        status = "rejected"
        validation_status = "failed"
    elif rejected:
        status = "accepted_with_rejections"
        validation_status = "passed_with_rejections"
    else:
        status = "accepted"
        validation_status = "passed"
    return {
        "claim_plan_schema_valid": not schema_errors,
        "schema_errors": schema_errors,
        "integrity_errors": integrity_errors,
        "claim_results": results,
        "accepted_claims": accepted,
        "rejected_claims": rejected,
        "section_coverage": section_coverage,
        "coverage_errors": coverage_errors,
        "bounded_inference_count": bounded,
        "forward_outlook_count": forward,
        "claim_plan_status": status,
        "claim_validation_status": validation_status,
        "report_generation_not_started": status == "rejected",
        "raw_plan_unchanged": raw == plan,
    }
