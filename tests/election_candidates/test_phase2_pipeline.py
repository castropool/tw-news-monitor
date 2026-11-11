from __future__ import annotations

import json

import pytest

from app.election_candidates.publication_pipeline import (
    PublicationFault,
    PublicationLock,
    PublicationLockError,
    batch_hash,
    commit_batch,
    create_backup,
    detect_recovery_required,
    prepare_batch,
    rollback_batch,
)
from app.election_candidates.publication_preview import (
    build_preview,
    formal_seed_business_hash,
)
from app.election_candidates.publication_validator import validate_batch

from .publication_helpers import (
    default_event_payload,
    default_sources,
    make_and_save_decision,
    make_publication_config,
    open_candidate_repo,
    seed_candidate,
)


def _approved_preview(tmp_path, config, repo, event=None, sources=None, decision_type="approve_new_event", target=None):
    decision = make_and_save_decision(
        repo, config, tmp_path, "cand_tnn_abc123", decision_type,
        event=event or default_event_payload(),
        sources=sources if sources is not None else default_sources(),
        target=target,
    )
    return build_preview(
        repo, config, "TW-2026-TNN-MAYOR", "local_reviewer",
        [decision["review_decision_id"]],
    )


def _prepare(tmp_path, config, repo, preview, faults=None):
    return prepare_batch(
        repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview,
        "local_reviewer", faults=faults,
    )


def test_prepare_success(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    result = _prepare(tmp_path, config, repo, preview)
    assert result["prepare_ready"] is True
    batch = repo.get_publication_batch(preview["batch_id"])
    assert batch["status"] == "staged"
    assert batch["backup_ready"] == 1 and batch["staging_ready"] == 1
    staging_db = config.path("output_root") / "publication_staging" / preview["batch_id"] / "election_context.db"
    assert staging_db.exists()
    repo.close()


def test_prepare_requires_validation_pass(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    event = default_event_payload(event_date="")
    preview = _approved_preview(tmp_path, config, repo, event=event)
    with pytest.raises(ValueError):
        _prepare(tmp_path, config, repo, preview)
    repo.close()


def test_prepare_unresolved_source_fails(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    sources = [
        {
            "source_name": "未知",
            "domain": "",
            "formal_source_id": "",
            "formal_match_status": "unresolved",
            "approve_new_source": False,
        }
    ]
    preview = _approved_preview(tmp_path, config, repo, sources=sources)
    with pytest.raises(ValueError):
        _prepare(tmp_path, config, repo, preview)
    repo.close()


def test_backup_created_with_manifest_and_sha(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    backup_dir = create_backup(config, preview["batch_id"], preview)
    assert (backup_dir / "backup_manifest.json").exists()
    assert (backup_dir / "SHA256SUMS").exists()
    assert (backup_dir / "events.jsonl").exists()
    assert (backup_dir / "election_context.db").exists()
    assert ".env" not in [p.name for p in backup_dir.iterdir()]
    repo.close()


def test_commit_success_and_post_validation(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    _prepare(tmp_path, config, repo, preview)
    before = formal_seed_business_hash(config)
    result = commit_batch(
        repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"],
        "local_reviewer", batch_hash(preview), preview,
    )
    assert result["commit_ready"] is True
    batch = repo.get_publication_batch(preview["batch_id"])
    assert batch["status"] == "committed"
    batch_dir = config.path("output_root") / "publication_batches" / preview["batch_id"]
    assert (batch_dir / "post_commit_validation.json").exists()
    assert (batch_dir / "downstream_refresh_request.json").exists()
    assert (batch_dir / "formal_diff_before_after.json").exists()
    assert (batch_dir / "publication_audit.md").exists()
    assert formal_seed_business_hash(config) != before
    repo.close()


def test_duplicate_commit_rejected(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    _prepare(tmp_path, config, repo, preview)
    commit_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", batch_hash(preview), preview)
    with pytest.raises(ValueError):
        commit_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", batch_hash(preview), preview)
    repo.close()


def test_commit_hash_mismatch_rejected(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    _prepare(tmp_path, config, repo, preview)
    with pytest.raises(ValueError):
        commit_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", "deadbeef", preview)
    repo.close()


def test_lock_conflict(tmp_path):
    config = make_publication_config(tmp_path)
    lock1 = PublicationLock(config, "TW-2026-TNN-MAYOR", "a", "b1", "commit")
    lock1.acquire()
    try:
        with pytest.raises(PublicationLockError):
            lock2 = PublicationLock(config, "TW-2026-TNN-MAYOR", "b", "b2", "commit")
            lock2.acquire()
    finally:
        lock1.release()


def test_crash_mid_commit_journal_detected(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    _prepare(tmp_path, config, repo, preview)
    with pytest.raises(PublicationFault):
        commit_batch(
            repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"],
            "local_reviewer", batch_hash(preview), preview,
            faults={"fail_before_seed_replace": True},
        )
    recovery = detect_recovery_required(config, preview["batch_id"])
    assert recovery["recovery_required"] is True
    repo.close()


def test_crash_after_seed_commit_detected(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    _prepare(tmp_path, config, repo, preview)
    with pytest.raises(PublicationFault):
        commit_batch(
            repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"],
            "local_reviewer", batch_hash(preview), preview,
            faults={"fail_during_db_replace": True},
        )
    recovery = detect_recovery_required(config, preview["batch_id"])
    assert recovery["recovery_required"] is True
    repo.close()


def test_rollback_restores_hash(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    before = formal_seed_business_hash(config)
    _prepare(tmp_path, config, repo, preview)
    commit_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", batch_hash(preview), preview)
    result = rollback_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer")
    assert result["rolled_back"] is True
    assert result["formal_data_hash_after_rollback"] == before
    assert formal_seed_business_hash(config) == before
    repo.close()


def test_rollback_second_time_blocked(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    _prepare(tmp_path, config, repo, preview)
    commit_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", batch_hash(preview), preview)
    rollback_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer")
    with pytest.raises(ValueError):
        rollback_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer")
    repo.close()


def test_fault_backup_failure(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    with pytest.raises(PublicationFault):
        _prepare(tmp_path, config, repo, preview, faults={"fail_backup": True})
    repo.close()


def test_fault_staging_bootstrap_failure(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    with pytest.raises(PublicationFault):
        _prepare(tmp_path, config, repo, preview, faults={"fail_staging_bootstrap": True})
    repo.close()


def test_fault_journal_write_failure(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    with pytest.raises(PublicationFault):
        _prepare(tmp_path, config, repo, preview, faults={"fail_journal_write": True})
    repo.close()


def test_fault_post_validation_failure(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    _prepare(tmp_path, config, repo, preview)
    with pytest.raises(PublicationFault):
        commit_batch(
            repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"],
            "local_reviewer", batch_hash(preview), preview,
            faults={"fail_post_validation": True},
        )
    repo.close()


def test_fault_rollback_failure(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    _prepare(tmp_path, config, repo, preview)
    commit_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", batch_hash(preview), preview)
    with pytest.raises(PublicationFault):
        rollback_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", faults={"fail_rollback": True})
    repo.close()


def test_staging_bootstrap_reproducible(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    _prepare(tmp_path, config, repo, preview)
    staging_dir = config.path("output_root") / "publication_staging" / preview["batch_id"]
    staging_db = staging_dir / "election_context.db"
    conn = __import__("sqlite3").connect(f"file:{staging_db}?mode=ro", uri=True)
    events = conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0]
    sources = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    links = conn.execute("SELECT COUNT(*) FROM event_sources").fetchone()[0]
    conn.close()
    assert events == 3  # 2 fixture + 1 new
    assert sources == 2  # reused fixture source
    assert links == 3
    repo.close()


def test_real_production_db_not_touched(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    preview = _approved_preview(tmp_path, config, repo)
    _prepare(tmp_path, config, repo, preview)
    commit_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", batch_hash(preview), preview)
    # production path is config.path("formal_db") which points to tmp in tests
    assert str(config.path("formal_db")).startswith(str(tmp_path))
    repo.close()
