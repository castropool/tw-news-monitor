from __future__ import annotations

import json
import sqlite3

import pytest

from app.assessment.assessment_trigger import run_mock_assessment
from app.election_candidates.publication_pipeline import rollback_batch
from app.election_context.formal_state_hash import formal_state_business_hash_from_db
from app.election_context.run_post_publication_pipeline import (
    phase3_recovery_gate,
    run_post_publication_pipeline,
)
from app.election_context.snapshot_pipeline import (
    detect_snapshot_recovery_required,
    recover_snapshot,
)

from .phase3_helpers import make_phase3_env


def _run(env, run_date=None, **over):
    return run_post_publication_pipeline(
        env["repo"], env["config"],
        publication_batch_id=env["batch_id"],
        request_path=env["request_path"],
        run_date=run_date,
        **over,
    )


def _active_snapshot_id(config):
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    row = conn.execute(
        "SELECT snapshot_id FROM election_state_snapshots WHERE snapshot_status='active'"
    ).fetchone()
    conn.close()
    return row[0]


def test_case_a_no_change_pending(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    before_active = _active_snapshot_id(env["config"])
    manifest = _run(env, run_date="2026-08-08")
    assert manifest["coverage"]["status"] == "committed"
    assert manifest["snapshot"]["status"] == "no_change"
    assert manifest["snapshot"]["changed"] is False
    assert _active_snapshot_id(env["config"]) == before_active
    assert manifest["assessment"]["status"] == "pending"
    assert manifest["assessment"]["eligible"] is False
    assert manifest["network_calls"] == 0
    env["repo"].close()


def test_case_b_deterministic_auto_activate(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    manifest = _run(env, run_date="2026-08-08")
    assert manifest["snapshot"]["status"] == "committed"
    assert manifest["snapshot"]["changed"] is True
    assert manifest["snapshot"]["active_snapshot_id"] == manifest["snapshot"]["candidate_snapshot_id"]
    assert _active_snapshot_id(env["config"]) == manifest["snapshot"]["candidate_snapshot_id"]
    env["repo"].close()


def test_case_c_analytical_pending_review_blocks_assessment(tmp_path):
    env = make_phase3_env(tmp_path, event_type="campaign_event")
    manifest = _run(env, run_date="2026-08-09")
    assert manifest["snapshot"]["status"] == "pending_review"
    assert manifest["assessment"]["status"] == "blocked"
    assert _active_snapshot_id(env["config"]) == manifest["snapshot"]["previous_snapshot_id"]
    trigger = env["repo"].get_trigger(manifest["assessment"]["trigger_id"])
    assert trigger["status"] == "blocked"
    env["repo"].close()


def test_case_d_coverage_failure_keeps_facts_and_old_coverage(tmp_path, monkeypatch):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    before_hash = formal_state_business_hash_from_db(env["config"].path("formal_db"))
    old_coverage = _active_coverage(env["config"])
    import app.election_context.run_post_publication_pipeline as rpp

    def boom(*args, **kwargs):
        raise RuntimeError("injected coverage failure")

    monkeypatch.setattr(rpp, "build_coverage", boom)
    with pytest.raises(RuntimeError, match="coverage failure"):
        _run(env, run_date="2026-08-08")
    assert formal_state_business_hash_from_db(env["config"].path("formal_db")) == before_hash
    assert _active_coverage(env["config"]) == old_coverage
    batch = env["repo"].get_refresh_batch_by_publication(env["batch_id"])
    assert batch["status"] == "failed"
    assert "coverage_failed" in batch["error_summary"]
    env["repo"].close()


def test_case_e_snapshot_failure_keeps_coverage_and_active(tmp_path, monkeypatch):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    before_active = _active_snapshot_id(env["config"])
    import app.election_context.run_post_publication_pipeline as rpp

    def boom(*args, **kwargs):
        raise RuntimeError("injected snapshot staging failure")

    monkeypatch.setattr(rpp, "commit_snapshot", boom)
    with pytest.raises(RuntimeError, match="snapshot staging failure"):
        _run(env, run_date="2026-08-08")
    assert _active_snapshot_id(env["config"]) == before_active
    batch = env["repo"].get_refresh_batch_by_publication(env["batch_id"])
    assert batch["status"] == "failed"
    env["repo"].close()


def test_case_f_snapshot_journal_recovery(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    before_hash = formal_state_business_hash_from_db(env["config"].path("formal_db"))
    from app.election_context.snapshot_pipeline import prepare_snapshot_staging
    from app.election_context.coverage_builder import build_coverage

    coverage = build_coverage(env["config"], requested_start="2026-07-01", requested_end="2026-07-31")
    cov = dict(coverage["coverage"])
    cov["coverage_version"] = coverage["coverage_version"]
    from app.election_context.snapshot_candidate_builder import build_snapshot_candidate
    event_id = env["preview"]["new_events"][0]["event_id"]
    candidate = build_snapshot_candidate(
        env["config"], refresh_batch_id="dr_f", new_event_ids=[event_id], coverage=cov
    )
    prepare_snapshot_staging(env["config"], "dr_f", candidate)
    journal_path = (
        env["config"].path("post_publication_root")
        / "dr_f" / "snapshot_refresh_journal.json"
    )
    journal_path.write_text(
        json.dumps(
            {
                "refresh_batch_id": "dr_f",
                "steps": {"prepared": True, "backup_complete": True,
                          "seed_commit_started": True, "seed_commit_complete": False},
            }
        ),
        encoding="utf-8",
    )
    assert detect_snapshot_recovery_required(env["config"], "dr_f")["recovery_required"] is True
    result = recover_snapshot(env["config"], "dr_f", "local_reviewer")
    assert result["automatic_action"] == "rollback"
    assert formal_state_business_hash_from_db(env["config"].path("formal_db")) == before_hash
    conn = sqlite3.connect(f"file:{env['config'].path('formal_db')}?mode=ro", uri=True)
    active = conn.execute(
        "SELECT COUNT(*) FROM election_state_snapshots WHERE snapshot_status='active'"
    ).fetchone()[0]
    conn.close()
    assert active == 1
    env["repo"].close()


def test_case_g_assessment_failure_does_not_rollback_snapshot(tmp_path, monkeypatch):
    env = make_phase3_env(
        tmp_path, event_type="primary_result", event_date="2026-07-31T12:00:00+08:00"
    )
    import app.election_context.run_post_publication_pipeline as rpp

    def boom(*args, **kwargs):
        raise RuntimeError("injected mock assessment failure")

    monkeypatch.setattr(rpp, "run_mock_assessment", boom)
    manifest = _run(env, run_date="2026-08-09")
    assert manifest["snapshot"]["status"] == "committed"
    assert manifest["assessment"]["status"] == "failed"
    assert manifest["retry_required"] is True
    assert _active_snapshot_id(env["config"]) == manifest["snapshot"]["candidate_snapshot_id"]
    trigger = env["repo"].get_trigger(manifest["assessment"]["trigger_id"])
    assert trigger["status"] == "failed"
    env["repo"].update_trigger_status(trigger["trigger_id"], "eligible")
    retried = run_mock_assessment(
        env["config"], {**trigger, "status": "eligible"},
        env["config"].path("post_publication_root") / "retry" / "assessment",
    )
    assert retried["assessment_run_id"]
    env["repo"].close()


def test_case_h_report_day_full_coverage_final(tmp_path):
    env = make_phase3_env(
        tmp_path, event_type="primary_registration", event_date="2026-07-31T12:00:00+08:00"
    )
    manifest = _run(env, run_date="2026-08-09")
    assert manifest["assessment"]["eligible"] is True
    assert manifest["assessment"]["status"] == "generated"
    assert manifest["network_calls"] == 0
    assert manifest["production_real_snapshot_activation_performed"] is False
    env["repo"].close()


def test_case_i_report_day_incomplete_draft(tmp_path):
    env = make_phase3_env(
        tmp_path, event_type="primary_registration", event_date="2026-07-27T12:00:00+08:00"
    )
    manifest = _run(env, run_date="2026-08-09")
    assert manifest["assessment"]["eligible"] is False
    assert manifest["assessment"]["status"] == "pending"
    trigger = env["repo"].get_trigger(manifest["assessment"]["trigger_id"])
    report = run_mock_assessment(
        env["config"], trigger, env["config"].path("post_publication_root") / "draft" / "assessment"
    )
    assert report["generation_mode"] == "draft_with_data_gap"
    assert report["final_report_allowed"] is False
    env["repo"].close()


def test_case_j_duplicate_consumption_idempotent(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    first = _run(env, run_date="2026-08-08")
    second = _run(env, run_date="2026-08-08")
    assert second["reused"] is True
    assert second["refresh_batch_id"] == first["refresh_batch_id"]
    assert second["snapshot"]["active_snapshot_id"] == first["snapshot"]["active_snapshot_id"]
    conn = sqlite3.connect(f"file:{env['config'].path('formal_db')}?mode=ro", uri=True)
    active = conn.execute(
        "SELECT COUNT(*) FROM election_state_snapshots WHERE snapshot_status='active'"
    ).fetchone()[0]
    conn.close()
    assert active == 1
    env["repo"].close()


def test_case_k_same_period_new_facts_supersede(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    manifest = _run(env, run_date="2026-08-08")
    from app.assessment.assessment_trigger import create_trigger
    newer = create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_newer",
        formal_state_hash="hash_newer",
        coverage_manifest={"facts_cutoff": "2026-07-31", "coverage_version": "v2"},
        snapshot_id="tn_state_v1",
        run_date="2026-08-08",
    )
    rows = env["repo"].get_triggers_for_period(
        env["config"].canonical_election_id,
        newer["period_start"], newer["period_end"],
    )
    old = [r for r in rows if r["formal_state_hash"] != "hash_newer"]
    assert old and old[0]["status"] == "superseded"
    assert manifest["assessment"]["trigger_id"] in [r["trigger_id"] for r in rows]
    env["repo"].close()


def test_case_l_rollback_publication_rejects_refresh(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    rollback_batch(
        env["repo"], env["config"], "TW-2026-TNN-MAYOR", env["batch_id"],
        "local_reviewer",
    )
    with pytest.raises(ValueError, match="invalid refresh request"):
        _run(env, run_date="2026-08-08")
    env["repo"].close()


def test_manifest_required_keys(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    manifest = _run(env, run_date="2026-08-08")
    for key in (
        "pipeline_version", "refresh_batch_id", "publication_batch_id",
        "formal_state_hash", "coverage", "snapshot", "assessment",
        "network_calls", "production_real_snapshot_activation_performed",
    ):
        assert key in manifest
    assert manifest["pipeline_version"] == "0.1.0"
    env["repo"].close()


def test_manifest_file_written(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    manifest = _run(env, run_date="2026-08-08")
    run_dir = (
        env["config"].path("post_publication_root")
        / manifest["refresh_batch_id"]
    )
    assert (run_dir / "post_publication_pipeline_manifest.json").exists()
    assert (run_dir / "downstream_refresh_journal.json").exists()
    assert (run_dir / "coverage" / "coverage_manifest.json").exists()
    env["repo"].close()


def test_snapshot_artifacts_written(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    manifest = _run(env, run_date="2026-08-08")
    run_dir = (
        env["config"].path("post_publication_root")
        / manifest["refresh_batch_id"]
    )
    candidate_dir = run_dir / "snapshot_candidates" / manifest["refresh_batch_id"]
    for name in ("snapshot_candidate.json", "snapshot_diff.json", "evidence_mapping.json", "validation.json"):
        assert (candidate_dir / name).exists()
    env["repo"].close()


def test_downstream_journal_steps(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    manifest = _run(env, run_date="2026-08-08")
    run_dir = (
        env["config"].path("post_publication_root")
        / manifest["refresh_batch_id"]
    )
    journal = json.loads((run_dir / "downstream_refresh_journal.json").read_text(encoding="utf-8"))
    steps = journal["steps"]
    for key in (
        "request_validated", "coverage_started", "coverage_committed",
        "snapshot_candidate_started", "snapshot_candidate_complete",
        "assessment_trigger_created", "completed",
    ):
        assert steps.get(key) is True
    env["repo"].close()


def test_recovery_gate_blocks_unfinished_downstream(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    batch = env["repo"].get_refresh_batch_by_publication(env["batch_id"])
    if not batch:
        from app.election_context.downstream_refresh import create_or_reuse_refresh_batch
        batch = create_or_reuse_refresh_batch(
            env["repo"], env["config"], env["batch_id"], "h"
        )
    run_dir = (
        env["config"].path("post_publication_root")
        / batch["refresh_batch_id"]
    )
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "downstream_refresh_journal.json").write_text(
        json.dumps({"steps": {"coverage_started": True}}), encoding="utf-8"
    )
    gate = phase3_recovery_gate(env["config"])
    assert gate["recovery_required"] is True
    with pytest.raises(ValueError, match="recovery gate"):
        _run(env, run_date="2026-08-08")
    env["repo"].close()


def test_refresh_batch_status_completed(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    manifest = _run(env, run_date="2026-08-08")
    batch = env["repo"].get_refresh_batch(manifest["refresh_batch_id"])
    assert batch["status"] == "completed"
    assert batch["coverage_result"]
    assert batch["snapshot_result"] == "no_change"
    assert batch["assessment_trigger_result"]
    assert batch["finished_at"]
    env["repo"].close()


def test_failure_isolation_coverage_does_not_rollback_facts(tmp_path, monkeypatch):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    before = formal_state_business_hash_from_db(env["config"].path("formal_db"))
    import app.election_context.run_post_publication_pipeline as rpp

    def boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(rpp, "build_coverage", boom)
    with pytest.raises(RuntimeError):
        _run(env, run_date="2026-08-08")
    assert formal_state_business_hash_from_db(env["config"].path("formal_db")) == before
    env["repo"].close()


def test_real_production_flags_false(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    manifest = _run(env, run_date="2026-08-08")
    assert manifest["production_real_snapshot_activation_performed"] is False
    assert manifest["network_calls"] == 0
    env["repo"].close()


def _active_coverage(config):
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    row = conn.execute(
        "SELECT state_json FROM election_state_snapshots WHERE snapshot_status='active'"
    ).fetchone()
    conn.close()
    state = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    return state.get("coverage", {})
