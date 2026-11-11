"""Deterministic coverage validator (Phase 3.5 authoritative semantics)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import date
from typing import Any

from app.election_context.coverage_builder import ALLOWED_COVERAGE_STATUS, VERSION_PATTERN
from app.election_context.coverage_rules import (
    blocking_gap_kinds,
    full_requires_facts_cutoff_reaches_period_end,
    load_acceptance_rules,
)
from app.election_context.formal_state_hash import (
    formal_state_business_hash_from_db,
    formal_state_business_hash_from_seed_dir,
)


def _d(v: str | None) -> date | None:
    if not v:
        return None
    try:
        return date.fromisoformat(str(v)[:10])
    except ValueError:
        return None


def _recompute_business_hash(payload: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def validate_coverage(config, coverage: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    rules = load_acceptance_rules(config)
    current_hash = formal_state_business_hash_from_db(config.path("formal_db"))
    if manifest.get("built_from_formal_state_hash") != current_hash:
        errors.append("formal_state_hash_matches")
    if coverage.get("built_from_formal_state_hash") != current_hash:
        errors.append("formal_state_hash_matches:payload")
    seed_hash = formal_state_business_hash_from_seed_dir(config.path("events_seed").parent)
    if current_hash != seed_hash:
        errors.append("formal_state_ready")

    version = manifest.get("coverage_version", "")
    if not VERSION_PATTERN.match(str(version)):
        errors.append("coverage_version_format")
    if coverage.get("coverage_version") != version:
        errors.append("coverage_version_consistent")
    if coverage.get("coverage_status") not in ALLOWED_COVERAGE_STATUS:
        errors.append("coverage_status_allowed")

    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    try:
        event_ids = {r[0] for r in conn.execute("SELECT event_id FROM election_events")}
        source_ids = {r[0] for r in conn.execute("SELECT source_id FROM sources")}
        poll_ids = {r[0] for r in conn.execute("SELECT poll_id FROM election_polls")}
    finally:
        conn.close()
    for rid in coverage.get("covered_event_ids", []):
        if rid not in event_ids:
            errors.append("all_event_ids_resolve")
    for sid in coverage.get("covered_source_ids", []):
        if sid not in source_ids:
            errors.append("all_source_ids_resolve")
    for pid in coverage.get("covered_poll_ids", []):
        if pid not in poll_ids:
            errors.append("poll_ids_resolve")

    ps = coverage.get("requested_period_start")
    pe = coverage.get("requested_period_end")
    if not ps or not pe or ps >= pe:
        errors.append("date_ranges_valid")
    fc_raw = coverage.get("facts_cutoff")
    fc = _d(fc_raw)
    if coverage.get("facts_cutoff_provenance") == "derived_from_latest_event":
        errors.append("facts_cutoff_not_derived_from_latest_event")
    if "latest_event_date" not in coverage:
        errors.append("latest_event_date_present")
    if "facts_cutoff" not in coverage:
        errors.append("facts_cutoff_present")

    status = coverage.get("coverage_status")
    if status == "full":
        if fc is None:
            errors.append("full_status_requires_cutoff")
        elif pe and fc < _d(pe):
            errors.append("full_status_requires_cutoff")
            errors.append("assessment_final_gate_consistent")
    # partial is always an allowed, semantically valid status
    if status == "partial":
        pass  # partial_status_allowed

    blocking = coverage.get("blocking_gaps", [])
    if status == "full" and blocking:
        errors.append("coverage_status_consistent_with_gap_state")
    allowed_blocking = blocking_gap_kinds(rules)
    for g in blocking:
        if g.get("kind") not in allowed_blocking:
            errors.append("blocking_gap_kind_allowed")
        if g.get("start") and g.get("end") and g["start"] > g["end"]:
            errors.append("blocking_gap_range_valid")

    for g in coverage.get("uncovered_date_ranges", []):
        if g.get("start") and g.get("end") and g["start"] > g["end"]:
            errors.append("uncovered_ranges_valid")
    # The unreviewed period (facts_cutoff+1 .. period_end) must be disclosed when cutoff < end.
    if fc is not None and pe and fc < _d(pe):
        from datetime import timedelta

        expect_start = (fc + timedelta(days=1)).isoformat()
        covered_by_any = any(
            (g.get("start") or expect_start) <= expect_start <= (g.get("end") or _d(pe).isoformat())
            for g in coverage.get("uncovered_date_ranges", [])
        )
        if not covered_by_any:
            errors.append("unreviewed_period_disclosed")

    if not isinstance(coverage.get("dimension_gaps"), list):
        errors.append("dimension_gaps_valid")
    expected_hash = manifest.get("business_hash")
    recomputed = _recompute_business_hash(coverage)
    if not expected_hash:
        errors.append("business_hash_valid")
    elif expected_hash != recomputed:
        errors.append("business_hash_valid")
        errors.append("business_hash_recomputed")

    forbidden = {"who_leads", "win_rate", "advantage", "领先", "优势扩大", "胜算"}
    if forbidden & set(coverage.keys()):
        errors.append("no_political_inference")

    final_report_allowed = status == "full"
    assessment_consistent = not (status == "full" and fc is not None and pe and fc < _d(pe))
    if not assessment_consistent:
        errors.append("assessment_final_gate_consistent")

    return {
        "coverage_ready": not errors,
        "errors": errors,
        "warnings": warnings,
        "coverage_version": version,
        "business_hash": expected_hash or recomputed,
        "facts_cutoff": fc_raw or "",
        "poll_cutoff": coverage.get("poll_cutoff", ""),
        "coverage_status": status,
        "final_report_allowed": final_report_allowed,
        "assessment_gate_consistent": assessment_consistent,
    }
