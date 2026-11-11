from __future__ import annotations

import json
import sqlite3

import pytest

from app.election_context.coverage_builder import build_coverage
from app.election_context.formal_state_hash import formal_state_business_hash_from_db
from app.election_context.snapshot_candidate_builder import (
    build_snapshot_candidate,
    compute_snapshot_changes,
)
from app.election_context.snapshot_pipeline import (
    commit_snapshot,
    detect_snapshot_recovery_required,
    prepare_snapshot_staging,
    recover_snapshot,
    rollback_snapshot,
)
from app.election_context.snapshot_validator import validate_snapshot_candidate

from .phase3_helpers import load_golden, make_phase3_env


GOLDEN = load_golden("snapshot")


def _snapshot_args(case: dict):
    return dict(
        previous_state=case["previous_state"],
        previous_supporting=case.get("previous_supporting", []),
        previous_snapshot_id=case.get("previous_snapshot_id", ""),
        new_event_ids=case.get("new_event_ids", []),
        events_by_id=case.get("events_by_id", {}),
        coverage=case.get("coverage", {}),
        as_of=case["as_of"],
        refresh_batch_id=case["refresh_batch_id"],
    )


@pytest.mark.parametrize("case", GOLDEN, ids=[c["case_id"] for c in GOLDEN])
def test_snapshot_golden_cases(case):
    if case.get("expected_error"):
        with pytest.raises(ValueError):
            compute_snapshot_changes(**_snapshot_args(case))
        return
    result = compute_snapshot_changes(**_snapshot_args(case))
    assert result["snapshot_change_required"] == case["expected_change_required"]
    if "expected_review" in case:
        assert result["review_required"] == case["expected_review"]
    if "expected_auto" in case:
        assert result["auto_activatable"] == case["expected_auto"]
    if "expected_candidate_id" in case:
        assert result["candidate_snapshot_id"] == case["expected_candidate_id"]
    if "expected_change_type" in case:
        assert any(
            c["change_type"] == case["expected_change_type"]
            for c in result["dimension_changes"]
        )
    if "expected_milestones" in case:
        assert result["dimensions"]["milestone_events"] == case["expected_milestones"]
    if "expected_supporting" in case:
        assert result["supporting_event_ids"] == case["expected_supporting"]
    if "expected_new_value" in case:
        assert any(
            c["new_value"] == case["expected_new_value"]
            for c in result["dimension_changes"]
        )


def test_candidate_structure_required_keys(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    coverage = build_coverage(env["config"], requested_start="2026-07-01", requested_end="2026-07-31")
    cov = dict(coverage["coverage"])
    cov["coverage_version"] = coverage["coverage_version"]
    event_id = env["preview"]["new_events"][0]["event_id"]
    candidate = build_snapshot_candidate(
        env["config"], refresh_batch_id="dr_x", new_event_ids=[event_id], coverage=cov
    )
    for key in (
        "candidate_snapshot_id", "previous_snapshot_id", "effective_date",
        "dimensions", "dimension_changes", "new_event_ids", "supporting_event_ids",
        "supporting_poll_ids", "formal_state_hash", "coverage_version",
        "auto_activatable", "review_required", "review_reasons",
    ):
        assert key in candidate
    env["repo"].close()


def test_candidate_id_stable_across_runs(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    coverage = build_coverage(env["config"], requested_start="2026-07-01", requested_end="2026-07-31")
    cov = dict(coverage["coverage"])
    cov["coverage_version"] = coverage["coverage_version"]
    event_id = env["preview"]["new_events"][0]["event_id"]
    a = build_snapshot_candidate(
        env["config"], refresh_batch_id="dr_x", new_event_ids=[event_id], coverage=cov
    )
    b = build_snapshot_candidate(
        env["config"], refresh_batch_id="dr_x", new_event_ids=[event_id], coverage=cov
    )
    assert a["candidate_snapshot_id"] == b["candidate_snapshot_id"]
    env["repo"].close()


def test_no_change_keeps_active_snapshot(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    coverage = build_coverage(env["config"], requested_start="2026-07-01", requested_end="2026-07-31")
    cov = dict(coverage["coverage"])
    cov["coverage_version"] = coverage["coverage_version"]
    event_id = env["preview"]["new_events"][0]["event_id"]
    candidate = build_snapshot_candidate(
        env["config"], refresh_batch_id="dr_x", new_event_ids=[event_id], coverage=cov
    )
    assert candidate["snapshot_change_required"] is False
    conn = sqlite3.connect(f"file:{env['config'].path('formal_db')}?mode=ro", uri=True)
    active = conn.execute(
        "SELECT snapshot_id FROM election_state_snapshots WHERE snapshot_status='active'"
    ).fetchall()
    conn.close()
    assert len(active) == 1
    assert active[0][0] == candidate["previous_snapshot_id"]
    env["repo"].close()


def test_analytical_change_requires_review_not_auto(tmp_path):
    env = make_phase3_env(tmp_path, event_type="campaign_event")
    coverage = build_coverage(env["config"], requested_start="2026-07-01", requested_end="2026-07-31")
    cov = dict(coverage["coverage"])
    cov["coverage_version"] = coverage["coverage_version"]
    event_id = env["preview"]["new_events"][0]["event_id"]
    candidate = build_snapshot_candidate(
        env["config"], refresh_batch_id="dr_x", new_event_ids=[event_id], coverage=cov
    )
    assert candidate["review_required"] is True
    assert candidate["auto_activatable"] is False
    env["repo"].close()


def test_every_change_has_supporting_evidence(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    coverage = build_coverage(env["config"], requested_start="2026-07-01", requested_end="2026-07-31")
    cov = dict(coverage["coverage"])
    cov["coverage_version"] = coverage["coverage_version"]
    event_id = env["preview"]["new_events"][0]["event_id"]
    candidate = build_snapshot_candidate(
        env["config"], refresh_batch_id="dr_x", new_event_ids=[event_id], coverage=cov
    )
    for change in candidate["dimension_changes"]:
        assert change["supporting_event_ids"] or change["supporting_poll_ids"]
    env["repo"].close()


def test_evidence_mapping_only_formal_ids(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    coverage = build_coverage(env["config"], requested_start="2026-07-01", requested_end="2026-07-31")
    cov = dict(coverage["coverage"])
    cov["coverage_version"] = coverage["coverage_version"]
    event_id = env["preview"]["new_events"][0]["event_id"]
    candidate = build_snapshot_candidate(
        env["config"], refresh_batch_id="dr_x", new_event_ids=[event_id], coverage=cov
    )
    conn = sqlite3.connect(f"file:{env['config'].path('formal_db')}?mode=ro", uri=True)
    known = {r[0] for r in conn.execute("SELECT event_id FROM election_events")}
    conn.close()
    assert set(candidate["supporting_event_ids"]) <= known
    env["repo"].close()


def _candidate_for_env(env, event_type="primary_result", event_date="2026-07-27T12:00:00+08:00"):
    coverage = build_coverage(env["config"], requested_start="2026-07-01", requested_end="2026-07-31")
    cov = dict(coverage["coverage"])
    cov["coverage_version"] = coverage["coverage_version"]
    event_id = env["preview"]["new_events"][0]["event_id"]
    return build_snapshot_candidate(
        env["config"], refresh_batch_id="dr_v", new_event_ids=[event_id], coverage=cov
    ), coverage


def test_validator_passes_auto_candidate(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, coverage = _candidate_for_env(env)
    validation = validate_snapshot_candidate(
        env["config"], candidate, coverage["coverage"], coverage["manifest"]
    )
    assert validation["snapshot_valid"] is True
    assert validation["errors"] == []
    env["repo"].close()


def test_validator_rejects_unknown_previous(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, coverage = _candidate_for_env(env)
    candidate["previous_snapshot_id"] = "tn_state_ghost_v1"
    validation = validate_snapshot_candidate(
        env["config"], candidate, coverage["coverage"], coverage["manifest"]
    )
    assert "previous_snapshot_exists" in validation["errors"]
    assert "previous_snapshot_is_active" in validation["errors"]
    env["repo"].close()


def test_validator_rejects_stale_previous_after_commit(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, coverage = _candidate_for_env(env)
    commit_snapshot(env["config"], "dr_v", candidate, allow_real=False)
    validation = validate_snapshot_candidate(
        env["config"], candidate, coverage["coverage"], coverage["manifest"]
    )
    assert "previous_snapshot_is_active" in validation["errors"]
    env["repo"].close()


def test_validator_rejects_missing_supporting_event(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, coverage = _candidate_for_env(env)
    candidate["supporting_event_ids"].append("evt_ghost")
    validation = validate_snapshot_candidate(
        env["config"], candidate, coverage["coverage"], coverage["manifest"]
    )
    assert "all_supporting_events_exist" in validation["errors"]
    env["repo"].close()


def test_validator_rejects_unpublished_reference(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, coverage = _candidate_for_env(env)
    candidate["new_event_ids"] = ["evt_not_published"]
    validation = validate_snapshot_candidate(
        env["config"], candidate, coverage["coverage"], coverage["manifest"]
    )
    assert "no_unpublished_candidate_reference" in validation["errors"]
    env["repo"].close()


def test_validator_rejects_unsupported_inference(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, coverage = _candidate_for_env(env)
    candidate["review_required"] = True
    candidate["auto_activatable"] = True
    validation = validate_snapshot_candidate(
        env["config"], candidate, coverage["coverage"], coverage["manifest"]
    )
    assert "no_unsupported_political_inference" in validation["errors"]
    env["repo"].close()


def test_validator_rejects_unexplained_change(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, coverage = _candidate_for_env(env)
    candidate["dimension_changes"][0]["supporting_event_ids"] = []
    candidate["dimension_changes"][0]["supporting_poll_ids"] = []
    validation = validate_snapshot_candidate(
        env["config"], candidate, coverage["coverage"], coverage["manifest"]
    )
    assert "dimension_changes_explained" in validation["errors"]
    env["repo"].close()


def test_validator_rejects_double_active(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, coverage = _candidate_for_env(env)
    conn = sqlite3.connect(env["config"].path("formal_db"))
    conn.execute(
        "INSERT INTO election_state_snapshots "
        "(snapshot_id,election_id,as_of,state_json,supporting_event_ids_json,created_at,snapshot_status) "
        "VALUES ('tn_state_dup_v1','TW-2026-TNN-MAYOR','2026-08-01','{}','[]','2026-08-01','active')"
    )
    conn.commit()
    conn.close()
    validation = validate_snapshot_candidate(
        env["config"], candidate, coverage["coverage"], coverage["manifest"]
    )
    assert "exactly_one_active_snapshot" in validation["errors"]
    env["repo"].close()


def test_validator_rejects_formal_hash_mismatch(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, coverage = _candidate_for_env(env)
    candidate["formal_state_hash"] = "deadbeef"
    validation = validate_snapshot_candidate(
        env["config"], candidate, coverage["coverage"], coverage["manifest"]
    )
    assert "formal_state_hash_matches" in validation["errors"]
    env["repo"].close()


def test_validator_rejects_analytical_without_review(tmp_path):
    env = make_phase3_env(tmp_path, event_type="campaign_event")
    candidate, coverage = _candidate_for_env(env)
    candidate["review_required"] = False
    validation = validate_snapshot_candidate(
        env["config"], candidate, coverage["coverage"], coverage["manifest"]
    )
    assert "analytical_change_requires_review" in validation["errors"]
    env["repo"].close()


def test_prepare_staging_creates_seed_and_db(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, coverage = _candidate_for_env(env)
    staging = prepare_snapshot_staging(env["config"], "dr_s1", candidate)
    assert (staging / "initial_snapshot.json").exists()
    assert (staging / "snapshot_history.jsonl").exists()
    assert (staging / "election_context.db").exists()
    assert (staging / "staging_validation.json").exists()
    staging_db = staging / "election_context.db"
    from app.election_context.formal_state_hash import formal_state_business_hash_from_seed_dir
    assert formal_state_business_hash_from_seed_dir(staging) == formal_state_business_hash_from_db(staging_db)
    conn = sqlite3.connect(f"file:{staging_db}?mode=ro", uri=True)
    active = conn.execute(
        "SELECT COUNT(*) FROM election_state_snapshots WHERE snapshot_status='active'"
    ).fetchone()[0]
    conn.close()
    assert active == 1
    env["repo"].close()


def test_staging_does_not_touch_live_seed(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, _ = _candidate_for_env(env)
    seed = env["config"].path("events_seed").parent
    before = (seed / "initial_snapshot.json").read_bytes()
    prepare_snapshot_staging(env["config"], "dr_s2", candidate)
    assert (seed / "initial_snapshot.json").read_bytes() == before
    env["repo"].close()


def test_commit_snapshot_updates_seed_and_db(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, _ = _candidate_for_env(env)
    before_hash = formal_state_business_hash_from_db(env["config"].path("formal_db"))
    result = commit_snapshot(env["config"], "dr_c1", candidate, allow_real=False)
    assert result["snapshot_committed"] is True
    seed = env["config"].path("events_seed").parent
    active = json.loads((seed / "initial_snapshot.json").read_text(encoding="utf-8"))
    assert active["snapshot_id"] == candidate["candidate_snapshot_id"]
    assert active["snapshot_status"] == "active"
    conn = sqlite3.connect(f"file:{env['config'].path('formal_db')}?mode=ro", uri=True)
    rows = conn.execute(
        "SELECT snapshot_id, snapshot_status FROM election_state_snapshots ORDER BY snapshot_id"
    ).fetchall()
    conn.close()
    assert sum(1 for _, s in rows if s == "active") == 1
    assert any(sid == candidate["candidate_snapshot_id"] for sid, _ in rows)
    assert formal_state_business_hash_from_db(env["config"].path("formal_db")) != before_hash
    env["repo"].close()


def test_commit_requires_test_mode_or_allow_real(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    env["config"].raw["test_mode"] = False
    candidate, _ = _candidate_for_env(env)
    with pytest.raises(PermissionError):
        commit_snapshot(env["config"], "dr_c2", candidate, allow_real=False)
    env["repo"].close()


def test_snapshot_journal_steps_written(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, _ = _candidate_for_env(env)
    commit_snapshot(env["config"], "dr_c3", candidate, allow_real=False)
    journal_path = (
        env["config"].path("post_publication_root")
        / "dr_c3" / "snapshot_refresh_journal.json"
    )
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    steps = journal["steps"]
    for key in (
        "prepared", "backup_complete", "seed_commit_started", "seed_commit_complete",
        "database_rebuild_started", "database_rebuild_complete",
        "post_validation_complete", "committed",
    ):
        assert steps.get(key) is True
    env["repo"].close()


def test_rollback_restores_before_refresh_hash(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, _ = _candidate_for_env(env)
    before = formal_state_business_hash_from_db(env["config"].path("formal_db"))
    commit_snapshot(env["config"], "dr_r1", candidate, allow_real=False)
    result = rollback_snapshot(env["config"], "dr_r1", "local_reviewer")
    assert result["snapshot_rolled_back"] is True
    after = formal_state_business_hash_from_db(env["config"].path("formal_db"))
    assert after == before
    assert result["formal_state_hash_after"] == before
    env["repo"].close()


def test_rollback_twice_blocked(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, _ = _candidate_for_env(env)
    commit_snapshot(env["config"], "dr_r2", candidate, allow_real=False)
    rollback_snapshot(env["config"], "dr_r2", "local_reviewer")
    with pytest.raises(ValueError):
        rollback_snapshot(env["config"], "dr_r2", "local_reviewer")
    env["repo"].close()


def test_recovery_detects_partial_journal(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, _ = _candidate_for_env(env)
    prepare_snapshot_staging(env["config"], "dr_f1", candidate)
    journal_path = (
        env["config"].path("post_publication_root")
        / "dr_f1" / "snapshot_refresh_journal.json"
    )
    journal_path.write_text(
        json.dumps(
            {
                "refresh_batch_id": "dr_f1",
                "steps": {"prepared": True, "backup_complete": True,
                          "seed_commit_started": True, "seed_commit_complete": False},
            }
        ),
        encoding="utf-8",
    )
    gate = detect_snapshot_recovery_required(env["config"], "dr_f1")
    assert gate["recovery_required"] is True
    env["repo"].close()


def test_recovery_restores_backup(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, _ = _candidate_for_env(env)
    before = formal_state_business_hash_from_db(env["config"].path("formal_db"))
    prepare_snapshot_staging(env["config"], "dr_f2", candidate)
    journal_path = (
        env["config"].path("post_publication_root")
        / "dr_f2" / "snapshot_refresh_journal.json"
    )
    journal_path.write_text(
        json.dumps(
            {
                "steps": {"prepared": True, "backup_complete": True,
                          "database_rebuild_started": True, "database_rebuild_complete": False},
            }
        ),
        encoding="utf-8",
    )
    result = recover_snapshot(env["config"], "dr_f2", "local_reviewer")
    assert result["automatic_action"] == "rollback"
    assert formal_state_business_hash_from_db(env["config"].path("formal_db")) == before
    env["repo"].close()


def test_exactly_one_active_after_commit(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    candidate, _ = _candidate_for_env(env)
    commit_snapshot(env["config"], "dr_o1", candidate, allow_real=False)
    conn = sqlite3.connect(f"file:{env['config'].path('formal_db')}?mode=ro", uri=True)
    active = conn.execute(
        "SELECT COUNT(*) FROM election_state_snapshots WHERE snapshot_status='active'"
    ).fetchone()[0]
    conn.close()
    assert active == 1
    env["repo"].close()


def test_snapshot_rollback_does_not_rollback_published_facts(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_result")
    event_id = env["preview"]["new_events"][0]["event_id"]
    candidate, _ = _candidate_for_env(env)
    commit_snapshot(env["config"], "dr_f3", candidate, allow_real=False)
    rollback_snapshot(env["config"], "dr_f3", "local_reviewer")
    conn = sqlite3.connect(f"file:{env['config'].path('formal_db')}?mode=ro", uri=True)
    assert conn.execute(
        "SELECT COUNT(*) FROM election_events WHERE event_id=?", (event_id,)
    ).fetchone()[0] == 1
    conn.close()
    env["repo"].close()


def test_no_change_reports_keep_active(tmp_path):
    env = make_phase3_env(tmp_path, event_type="primary_registration")
    candidate, _ = _candidate_for_env(env)
    assert candidate["snapshot_change_required"] is False
    conn = sqlite3.connect(f"file:{env['config'].path('formal_db')}?mode=ro", uri=True)
    active = conn.execute(
        "SELECT snapshot_id FROM election_state_snapshots WHERE snapshot_status='active'"
    ).fetchone()
    conn.close()
    assert active[0] == candidate["previous_snapshot_id"]
    env["repo"].close()
