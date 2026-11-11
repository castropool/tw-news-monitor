"""Downstream refresh request validation and refresh batch management."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.time_utils import TAIPEI

from app.election_context.formal_state_hash import formal_state_business_hash_from_db


def validate_refresh_request(
    repo,
    config,
    request_path: str | Path,
) -> dict[str, Any]:
    """Validate a downstream_refresh_request produced by a committed publication batch."""
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    errors: list[str] = []
    warnings: list[str] = []
    publication_batch_id = request.get("publication_batch_id", "")
    batch = repo.get_publication_batch(publication_batch_id)
    if not batch:
        errors.append("publication_batch_id_exists")
    elif batch.get("status") != "committed":
        errors.append("batch_status_committed")

    if "new_event_ids" not in request or "new_source_ids" not in request:
        errors.append("new_ids_fields_present")
    elif not request.get("new_event_ids") and not request.get("new_source_ids"):
        errors.append("new_ids_present")
    if not request.get("election_id"):
        errors.append("election_id_present")

    current_hash = formal_state_business_hash_from_db(config.path("formal_db"))
    if request.get("formal_state_hash"):
        if request["formal_state_hash"] != current_hash:
            errors.append("formal_state_hash_matches")
    else:
        warnings.append("formal_state_hash_missing_in_request")

    if batch and batch.get("commit_completed"):
        audits = repo.list_publication_audit(publication_batch_id)
        if not any(a.get("action") == "commit" and a.get("result") == "success" for a in audits):
            warnings.append("publication_audit_incomplete")
    else:
        warnings.append("publication_audit_incomplete")

    return {
        "request_valid": not errors,
        "errors": errors,
        "warnings": warnings,
        "request": request,
        "publication_batch_id": publication_batch_id,
        "formal_state_hash": current_hash,
    }


def create_or_reuse_refresh_batch(
    repo,
    config,
    publication_batch_id: str,
    formal_state_hash: str,
    previous_coverage_version: str = "",
    previous_snapshot_id: str = "",
    requested_period_start: str = "",
    requested_period_end: str = "",
) -> dict[str, Any]:
    """Idempotent: one publication batch maps to exactly one refresh batch."""
    existing = repo.get_refresh_batch_by_publication(publication_batch_id)
    if existing:
        return existing
    now = datetime.now(TAIPEI).isoformat()
    refresh_batch_id = "dr_" + hashlib.sha256(publication_batch_id.encode("utf-8")).hexdigest()[:16]
    batch = {
        "refresh_batch_id": refresh_batch_id,
        "publication_batch_id": publication_batch_id,
        "election_id": config.canonical_election_id,
        "formal_state_hash": formal_state_hash,
        "previous_coverage_version": previous_coverage_version,
        "previous_snapshot_id": previous_snapshot_id,
        "requested_period_start": requested_period_start,
        "requested_period_end": requested_period_end,
        "status": "pending",
        "coverage_refresh_required": 1,
        "snapshot_refresh_required": 1,
        "assessment_refresh_required": 1,
        "coverage_result": "",
        "snapshot_result": "",
        "assessment_trigger_result": "",
        "created_at": now,
        "started_at": "",
        "finished_at": "",
        "error_summary": "",
    }
    repo.upsert_refresh_batch(batch)
    return batch
