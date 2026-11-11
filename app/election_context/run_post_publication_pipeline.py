"""Post-publication pipeline orchestrator.

Transaction boundaries (independent; later failures never roll back earlier
successful transactions):
    A. fact publication        (Phase 2, already committed)
    B. coverage rebuild        (this module)
    C. coverage activation     (coverage_activation: validated coverage enters
                               the selectable seed area, staging->atomic rename)
    D. snapshot activation     (snapshot_pipeline)
    E. assessment generation   (assessment_trigger + mock/assessment pipeline)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.time_utils import TAIPEI

from app.assessment.assessment_trigger import create_trigger, run_mock_assessment
from app.election_candidates.publication_recovery import recovery_gate
from app.election_context.coverage_activation import activate_coverage
from app.election_context.coverage_builder import build_coverage, write_coverage
from app.election_context.coverage_validator import validate_coverage
from app.election_context.downstream_refresh import (
    create_or_reuse_refresh_batch,
    validate_refresh_request,
)
from app.election_context.snapshot_candidate_builder import (
    build_snapshot_candidate,
    write_candidate,
)
from app.election_context.snapshot_pipeline import (
    commit_snapshot,
    detect_snapshot_recovery_required,
)
from app.election_context.snapshot_validator import validate_snapshot_candidate


def _atomic_write(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
    tmp.replace(path)


def _run_dir(config, refresh_batch_id: str) -> Path:
    d = config.path("post_publication_root") / refresh_batch_id
    d.mkdir(parents=True, exist_ok=True)
    return d


def _downstream_journal(config, refresh_batch_id: str) -> dict[str, Any]:
    p = _run_dir(config, refresh_batch_id) / "downstream_refresh_journal.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"steps": {}}


def _write_downstream_journal(config, refresh_batch_id: str, journal: dict[str, Any]):
    _atomic_write(
        _run_dir(config, refresh_batch_id) / "downstream_refresh_journal.json",
        json.dumps(journal, ensure_ascii=False, indent=2).encode("utf-8"),
    )


def phase3_recovery_gate(config) -> dict[str, Any]:
    """Recovery gate over publication + snapshot refresh + downstream journals."""
    gate = recovery_gate(config)
    errors: list[str] = []
    post_root = config.path("post_publication_root")
    if post_root.exists():
        for d in post_root.iterdir():
            if not d.is_dir():
                continue
            snap = detect_snapshot_recovery_required(config, d.name)
            if snap["recovery_required"]:
                errors.append(f"unfinished_snapshot_journal:{d.name}")
            dp = d / "downstream_refresh_journal.json"
            if dp.exists():
                j = json.loads(dp.read_text(encoding="utf-8"))
                steps = j.get("steps") or {}
                started = any(
                    k in steps
                    for k in (
                        "coverage_started",
                        "snapshot_candidate_started",
                        "snapshot_commit_started",
                        "assessment_trigger_created",
                    )
                )
                done = steps.get("completed") or steps.get("failed")
                if started and not done:
                    errors.append(f"unfinished_downstream_journal:{d.name}")
    return {
        "recovery_required": gate["recovery_required"] or bool(errors),
        "publication_recovery": gate,
        "phase3_errors": errors,
    }


def _mark_failed(repo, batch, error_summary: str):
    batch["status"] = "failed"
    batch["error_summary"] = error_summary
    batch["finished_at"] = datetime.now(TAIPEI).isoformat()
    repo.upsert_refresh_batch(batch)


def run_post_publication_pipeline(
    repo,
    config,
    *,
    publication_batch_id: str,
    request_path: str | Path,
    run_date=None,
    manual: bool = False,
    allow_real_snapshot: bool = False,
) -> dict[str, Any]:
    existing = repo.get_refresh_batch_by_publication(publication_batch_id)
    if existing and existing.get("status") == "completed":
        manifest_path = _run_dir(config, existing["refresh_batch_id"]) / "post_publication_pipeline_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["reused"] = True
            return manifest
    gate = phase3_recovery_gate(config)
    if gate["recovery_required"]:
        raise ValueError(f"pipeline blocked by recovery gate: {gate}")
    request_result = validate_refresh_request(repo, config, request_path)
    if not request_result["request_valid"]:
        raise ValueError(f"invalid refresh request: {request_result['errors']}")
    request = request_result["request"]
    batch = create_or_reuse_refresh_batch(
        repo,
        config,
        publication_batch_id,
        request_result["formal_state_hash"],
        previous_coverage_version=request.get("previous_coverage_version", ""),
        previous_snapshot_id=request.get("previous_snapshot_id", ""),
        requested_period_start=request.get("requested_period_start", ""),
        requested_period_end=request.get("requested_period_end", ""),
    )
    run_dir = _run_dir(config, batch["refresh_batch_id"])
    if batch.get("status") == "completed":
        manifest_path = run_dir / "post_publication_pipeline_manifest.json"
        if manifest_path.exists():
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            manifest["reused"] = True
            return manifest
    journal = _downstream_journal(config, batch["refresh_batch_id"])
    journal.update(
        {
            "publication_batch_id": publication_batch_id,
            "steps": {"request_validated": True},
        }
    )
    _write_downstream_journal(config, batch["refresh_batch_id"], journal)

    period_start = request.get(
        "requested_period_start",
        batch.get("requested_period_start") or "2025-08-01",
    )
    period_end = request.get(
        "requested_period_end",
        batch.get("requested_period_end") or "2026-07-27",
    )
    try:
        coverage_result = build_coverage(
            config,
            requested_start=period_start,
            requested_end=period_end,
            facts_cutoff=request.get("facts_cutoff"),
            blocking_gaps=request.get("blocking_gaps", []),
            known_gaps=request.get("known_gaps", []),
        )
        journal["steps"]["coverage_started"] = True
        _write_downstream_journal(config, batch["refresh_batch_id"], journal)
        coverage_manifest = coverage_result["manifest"]
        write_coverage(coverage_result, run_dir / "coverage")
        cov_validation = validate_coverage(
            config, coverage_result["coverage"], coverage_manifest
        )
        if not cov_validation["coverage_ready"]:
            raise RuntimeError(f"coverage invalid: {cov_validation['errors']}")
        # Activation gate: only a fully validated coverage may enter the
        # selectable seed area; staging->atomic rename, failures isolated to
        # <coverage_root>/staging/failed and never selectable by R2.
        cov_activation = activate_coverage(
            config,
            coverage_result,
            cov_validation,
            refresh_batch_id=batch["refresh_batch_id"],
            active_snapshot_id=request.get("previous_snapshot_id", ""),
            allow_real=allow_real_snapshot,
        )
        journal["steps"]["coverage_activated"] = bool(
            cov_activation.get("activated") or cov_activation.get("reused")
        )
        batch["coverage_result"] = json.dumps(
            {
                "version": coverage_result["coverage_version"],
                "business_hash": coverage_result["business_hash"],
                "activation_status": (
                    "activated"
                    if cov_activation.get("activated")
                    else ("reused" if cov_activation.get("reused") else "pending")
                ),
            },
            ensure_ascii=False,
        )
        batch["status"] = "coverage_prepared"
        repo.upsert_refresh_batch(batch)
        journal["steps"]["coverage_committed"] = True
        _write_downstream_journal(config, batch["refresh_batch_id"], journal)
    except Exception as exc:  # Coverage failure: facts + old coverage stay untouched
        _mark_failed(repo, batch, f"coverage_failed: {exc}")
        journal["steps"]["failed"] = True
        journal["error_summary"] = str(exc)
        _write_downstream_journal(config, batch["refresh_batch_id"], journal)
        raise

    coverage_for_snapshot = dict(coverage_result["coverage"])
    coverage_for_snapshot["coverage_version"] = coverage_result["coverage_version"]
    candidate = build_snapshot_candidate(
        config,
        refresh_batch_id=batch["refresh_batch_id"],
        new_event_ids=request.get("new_event_ids", []),
        coverage=coverage_for_snapshot,
    )
    journal["steps"]["snapshot_candidate_started"] = True
    _write_downstream_journal(config, batch["refresh_batch_id"], journal)
    candidate_dir = run_dir / "snapshot_candidates" / batch["refresh_batch_id"]
    write_candidate(candidate, candidate_dir)
    (candidate_dir / "snapshot_diff.json").write_text(
        json.dumps(
            {
                "refresh_batch_id": batch["refresh_batch_id"],
                "previous_snapshot_id": candidate.get("previous_snapshot_id"),
                "candidate_snapshot_id": candidate.get("candidate_snapshot_id"),
                "change_required": candidate.get("snapshot_change_required", False),
                "dimension_changes": candidate.get("dimension_changes", []),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    (candidate_dir / "evidence_mapping.json").write_text(
        json.dumps(
            {
                "supporting_event_ids": candidate.get("supporting_event_ids", []),
                "supporting_poll_ids": candidate.get("supporting_poll_ids", []),
                "rule_ids": sorted(
                    {
                        c.get("rule_id", "")
                        for c in candidate.get("dimension_changes", [])
                        if c.get("rule_id")
                    }
                ),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    snapshot_status = "no_change"
    active_snapshot_id = candidate.get("previous_snapshot_id", "")
    try:
        if candidate.get("snapshot_change_required"):
            snapshot_validation = validate_snapshot_candidate(
                config, candidate, coverage_result["coverage"], coverage_manifest
            )
            (candidate_dir / "validation.json").write_text(
                json.dumps(snapshot_validation, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )
            if not snapshot_validation["snapshot_valid"]:
                raise RuntimeError(
                    f"snapshot candidate invalid: {snapshot_validation['errors']}"
                )
            batch["snapshot_result"] = "validated"
            repo.upsert_refresh_batch(batch)
            journal["steps"]["snapshot_candidate_complete"] = True
            _write_downstream_journal(config, batch["refresh_batch_id"], journal)
            if candidate.get("review_required"):
                snapshot_status = "pending_review"
                batch["snapshot_result"] = "pending_review"
                batch["status"] = "snapshot_candidate_ready"
            else:
                journal["steps"]["snapshot_commit_started"] = True
                _write_downstream_journal(config, batch["refresh_batch_id"], journal)
                if allow_real_snapshot or config.test_mode:
                    commit_snapshot(
                        config,
                        batch["refresh_batch_id"],
                        candidate,
                        allow_real=allow_real_snapshot,
                    )
                    snapshot_status = "committed"
                    batch["snapshot_result"] = "committed"
                    batch["status"] = "snapshot_committed"
                else:
                    snapshot_status = "pending_review"
                    batch["snapshot_result"] = "pending_review"
                    batch["status"] = "snapshot_candidate_ready"
                journal["steps"]["snapshot_commit_complete"] = snapshot_status == "committed"
                _write_downstream_journal(config, batch["refresh_batch_id"], journal)
            active_snapshot_id = candidate.get("candidate_snapshot_id")
        else:
            journal["steps"]["snapshot_candidate_complete"] = True
            journal["steps"]["snapshot_commit_complete"] = False
            batch["snapshot_result"] = "no_change"
            _write_downstream_journal(config, batch["refresh_batch_id"], journal)
    except Exception as exc:
        batch["snapshot_result"] = "failed"
        _mark_failed(repo, batch, f"snapshot_failed: {exc}")
        journal["steps"]["failed"] = True
        journal["error_summary"] = str(exc)
        _write_downstream_journal(config, batch["refresh_batch_id"], journal)
        raise
    repo.upsert_refresh_batch(batch)

    trigger = create_trigger(
        repo,
        config,
        refresh_batch_id=batch["refresh_batch_id"],
        formal_state_hash=coverage_result["formal_state_hash"],
        coverage_manifest=coverage_manifest,
        snapshot_id=active_snapshot_id,
        run_date=run_date,
        manual=manual,
    )
    journal["steps"]["assessment_trigger_created"] = True
    _write_downstream_journal(config, batch["refresh_batch_id"], journal)

    assessment_status = trigger["status"]
    assessment_run_id = ""
    error_summary = ""
    if snapshot_status == "pending_review":
        assessment_status = "blocked"
        repo.update_trigger_status(
            trigger["trigger_id"],
            "blocked",
            "snapshot pending review blocks formal report",
        )
    elif trigger["status"] == "eligible":
        try:
            report = run_mock_assessment(config, trigger, run_dir / "assessment")
            assessment_run_id = report.get("assessment_run_id", "")
            assessment_status = "generated"
            repo.update_trigger_status(
                trigger["trigger_id"],
                "generated",
                assessment_run_id=assessment_run_id,
            )
        except Exception as exc:
            assessment_status = "failed"
            error_summary = f"assessment_failed: {exc}"
            repo.update_trigger_status(trigger["trigger_id"], "failed", error_summary)

    batch["assessment_trigger_result"] = json.dumps(
        {
            "trigger_id": trigger["trigger_id"],
            "status": assessment_status,
            "assessment_run_id": assessment_run_id,
        },
        ensure_ascii=False,
    )
    batch["status"] = "completed" if assessment_status != "failed" else "failed"
    batch["finished_at"] = datetime.now(TAIPEI).isoformat()
    if error_summary:
        batch["error_summary"] = error_summary
    repo.upsert_refresh_batch(batch)
    journal["steps"]["completed"] = assessment_status != "failed"
    journal["steps"]["failed"] = assessment_status == "failed"
    if error_summary:
        journal["error_summary"] = error_summary
    _write_downstream_journal(config, batch["refresh_batch_id"], journal)

    manifest = {
        "pipeline_version": "0.1.0",
        "refresh_batch_id": batch["refresh_batch_id"],
        "publication_batch_id": publication_batch_id,
        "formal_state_hash": coverage_result["formal_state_hash"],
        "facts_cutoff": coverage_result["coverage"].get("facts_cutoff", ""),
        "coverage": {
            "status": "committed",
            "version": coverage_result["coverage_version"],
            "business_hash": coverage_result["business_hash"],
            "activation_status": (
                "activated"
                if cov_activation.get("activated")
                else ("reused" if cov_activation.get("reused") else "pending")
            ),
        },
        "snapshot": {
            "status": snapshot_status,
            "previous_snapshot_id": candidate.get("previous_snapshot_id", ""),
            "candidate_snapshot_id": candidate.get("candidate_snapshot_id") or "",
            "active_snapshot_id": active_snapshot_id,
            "changed": candidate.get("snapshot_change_required", False),
        },
        "assessment": {
            "trigger_id": trigger["trigger_id"],
            "eligible": trigger["status"] == "eligible",
            "status": assessment_status,
            "assessment_run_id": assessment_run_id,
        },
        "network_calls": 0,
        "production_real_snapshot_activation_performed": False,
        "retry_required": assessment_status == "failed",
    }
    (run_dir / "post_publication_pipeline_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return manifest
