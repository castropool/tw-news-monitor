"""Snapshot candidate validator."""

from __future__ import annotations

import sqlite3
from typing import Any

from app.election_context.coverage_validator import validate_coverage
from app.election_context.formal_state_hash import formal_state_business_hash_from_db


SUPPORTED_CHANGE_TYPES = {
    "deterministic_append",
    "deterministic_update",
    "analytical_impact_pending_review",
}


def validate_snapshot_candidate(
    config,
    candidate: dict[str, Any],
    coverage: dict[str, Any],
    coverage_manifest: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    if candidate.get("snapshot_change_required") is False:
        return {
            "snapshot_valid": True,
            "errors": [],
            "warnings": [],
            "change_required": False,
        }
    if not candidate.get("candidate_snapshot_id"):
        errors.append("candidate_snapshot_id_valid")
    if candidate.get("candidate_snapshot_id") and not str(
        candidate["candidate_snapshot_id"]
    ).startswith("tn_state_"):
        errors.append("candidate_snapshot_id_valid")

    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    try:
        active_rows = conn.execute(
            "SELECT snapshot_id, snapshot_status FROM election_state_snapshots "
            "WHERE snapshot_status='active'"
        ).fetchall()
        if len(active_rows) != 1:
            errors.append("exactly_one_active_snapshot")
        prev = conn.execute(
            "SELECT snapshot_id, snapshot_status FROM election_state_snapshots "
            "WHERE snapshot_id=? AND snapshot_status='active'",
            (candidate.get("previous_snapshot_id"),),
        ).fetchone()
        if not prev:
            errors.append("previous_snapshot_exists")
            errors.append("previous_snapshot_is_active")
        event_ids = {r[0] for r in conn.execute("SELECT event_id FROM election_events")}
        poll_ids = {r[0] for r in conn.execute("SELECT poll_id FROM election_polls")}
    finally:
        conn.close()

    for eid in candidate.get("supporting_event_ids", []):
        if eid not in event_ids:
            errors.append("all_supporting_events_exist")
    for eid in candidate.get("new_event_ids", []):
        if eid not in event_ids:
            errors.append("no_unpublished_candidate_reference")
    for pid in candidate.get("supporting_poll_ids", []):
        if pid not in poll_ids:
            errors.append("all_supporting_polls_exist")

    if candidate.get("formal_state_hash") != formal_state_business_hash_from_db(
        config.path("formal_db")
    ):
        errors.append("formal_state_hash_matches")
    cov = validate_coverage(config, coverage, coverage_manifest)
    if not cov["coverage_ready"]:
        errors.append("coverage_valid")
    if candidate.get("coverage_version") != coverage.get("coverage_version"):
        errors.append("coverage_version_matches")

    for change in candidate.get("dimension_changes", []):
        if change.get("change_type") not in SUPPORTED_CHANGE_TYPES:
            errors.append("dimension_change_type_supported")
        if not change.get("supporting_event_ids") and not change.get("supporting_poll_ids"):
            errors.append("dimension_changes_explained")
        if change.get("change_type") == "unsupported_evidence":
            errors.append("unsupported_evidence")
        if change.get("change_type") == "analytical_impact_pending_review":
            if change.get("new_value") not in (None, "unchanged_pending_review"):
                errors.append("analytical_fields_unchanged")
    if candidate.get("review_required") and candidate.get("auto_activatable"):
        errors.append("no_unsupported_political_inference")
    if not candidate.get("review_required") and any(
        c.get("change_type") == "analytical_impact_pending_review"
        for c in candidate.get("dimension_changes", [])
    ):
        errors.append("analytical_change_requires_review")

    return {
        "snapshot_valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "change_required": True,
    }
