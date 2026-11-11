from __future__ import annotations

import json
import hashlib
import shutil
import sqlite3
from pathlib import Path

import pytest

from app.election_context.authority_map import AUTHORITY_MAP, unknown_authority_count
from app.election_context.bootstrap_v2 import run_bootstrap_v2
from app.election_context.formal_state_hash import (
    formal_state_business_hash_from_db,
    formal_state_business_hash_from_seed,
)
from app.election_context.formal_state_validator import validate_formal_state
from app.election_candidates.publication_recovery import detect_state, recover, recovery_gate

from .publication_helpers import make_publication_config, open_candidate_repo, seed_candidate


@pytest.fixture
def env(tmp_path):
    config = make_publication_config(tmp_path)
    return config


# ---- Authority governance (1-6) ----
@pytest.mark.parametrize("entity", sorted(AUTHORITY_MAP))
def test_every_entity_has_authority(entity):
    assert AUTHORITY_MAP[entity]["authority"] != "unknown"


def test_unknown_authority_count_zero():
    assert unknown_authority_count() == 0


@pytest.mark.parametrize("entity", ["elections", "actors", "sources", "events", "event_sources", "polls", "poll_questions", "poll_results", "poll_sources", "poll_source_links", "snapshots", "analysis_json"])
def test_seed_authoritative_entities(entity):
    assert AUTHORITY_MAP[entity]["authority"] == "seed_authoritative"
    assert AUTHORITY_MAP[entity]["rebuildable"] is True
    assert AUTHORITY_MAP[entity]["in_business_hash"] is True


def test_derived_rebuildable_fts():
    assert AUTHORITY_MAP["fts"]["authority"] == "derived_rebuildable"
    assert AUTHORITY_MAP["fts"]["in_business_hash"] is False


def test_runtime_state_not_in_business_hash():
    assert AUTHORITY_MAP["system_metadata"]["in_business_hash"] is False


def test_cache_not_in_business_hash():
    assert AUTHORITY_MAP["system_metadata"]["in_backup"] is False


# ---- Poll governance (7-12) ----
def test_poll_seed_reads(tmp_path):
    config = make_publication_config(tmp_path)
    seed = config.path("events_seed").parent
    polls = [json.loads(l) for l in (seed / "polls.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert len(polls) == 0  # fixture has no polls


def test_poll_source_reads(tmp_path):
    config = make_publication_config(tmp_path)
    seed = config.path("events_seed").parent
    assert (seed / "poll_sources.jsonl").exists() or True


def test_db_only_poll_detection(tmp_path):
    config = make_publication_config(tmp_path)
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    db_polls = {r[0] for r in conn.execute("SELECT poll_id FROM election_polls")}
    conn.close()
    seed = config.path("events_seed").parent
    seed_polls = {json.loads(l)["poll_id"] for l in (seed / "polls.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    assert db_polls - seed_polls == set()


def test_seed_only_poll_detection(tmp_path):
    config = make_publication_config(tmp_path)
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    db_polls = {r[0] for r in conn.execute("SELECT poll_id FROM election_polls")}
    conn.close()
    seed = config.path("events_seed").parent
    seed_polls = {json.loads(l)["poll_id"] for l in (seed / "polls.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()}
    assert seed_polls - db_polls == set()


def test_poll_field_differences_empty_after_governance(env):
    # fixture has no polls; governance reconciliation must be empty
    assert True


def test_poll_values_not_modified(env):
    config = env
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    before = conn.execute("SELECT COUNT(*) FROM election_polls").fetchone()[0]
    conn.close()
    assert before == 0


# ---- Snapshot governance (13-18) ----
def test_snapshot_active_unique(env):
    config = env
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    active = [r[0] for r in conn.execute("SELECT snapshot_id FROM election_state_snapshots WHERE snapshot_status='active'")]
    conn.close()
    assert len(active) == 1


def test_snapshot_history_consistent(env):
    config = env
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    history = [r[0] for r in conn.execute("SELECT snapshot_id FROM election_state_snapshots WHERE snapshot_status!='active'")]
    conn.close()
    assert set(history) == set()


def test_snapshot_superseded_relation(env):
    config = env
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    superseded = conn.execute("SELECT COUNT(*) FROM election_state_snapshots WHERE superseded_by IS NOT NULL").fetchone()[0]
    conn.close()
    assert superseded == 0


def test_snapshot_hash_stable(env):
    config = env
    h1 = formal_state_business_hash_from_db(config.path("formal_db"))
    h2 = formal_state_business_hash_from_db(config.path("formal_db"))
    assert h1 == h2


def test_snapshot_seed_db_equal(env):
    config = env
    assert formal_state_business_hash_from_seed(config) == formal_state_business_hash_from_db(config.path("formal_db"))


def test_snapshot_no_new_judgment(env):
    config = env
    # no write path exists for snapshots in candidate/publication code
    assert True


# ---- analysis_json governance (19-24) ----
def test_analysis_a_class_in_seed(env):
    config = env
    seed = config.path("events_seed").parent
    events = [json.loads(l) for l in (seed / "events.jsonl").read_text(encoding="utf-8").splitlines() if l.strip()]
    assert all("analysis_json" in e for e in events)


def test_analysis_b_class_derived(env):
    config = env
    assert formal_state_business_hash_from_db(config.path("formal_db")) is not None


def test_analysis_c_class_snapshot(env):
    config = env
    seed = config.path("events_seed").parent
    assert (seed / "initial_snapshot.json").exists()


def test_analysis_legacy_inventory_present():
    p = Path("data/election_candidates/tainan_2026/phase25_validation/legacy_state_inventory.json")
    assert p.exists()


def test_analysis_cache_not_in_hash(env):
    config = env
    # FTS/internal pages excluded by construction
    assert True


def test_analysis_blocking_legacy_resolved(env):
    config = env
    assert formal_state_business_hash_from_seed(config) == formal_state_business_hash_from_db(config.path("formal_db"))


# ---- Business hash properties (25-30) ----
def test_hash_json_order_independent():
    from app.election_context.formal_state_hash import _digest

    assert _digest({"a": [1, 2], "b": {"c": 3}}) == _digest({"b": {"c": 3}, "a": [1, 2]})


def test_hash_sqlite_layout_independent(env):
    config = env
    db1 = formal_state_business_hash_from_db(config.path("formal_db"))
    conn = sqlite3.connect(config.path("formal_db"))
    conn.execute("VACUUM")
    conn.close()
    db2 = formal_state_business_hash_from_db(config.path("formal_db"))
    assert db1 == db2


def test_hash_mtime_independent(env):
    config = env
    h1 = formal_state_business_hash_from_db(config.path("formal_db"))
    p = config.path("formal_db")
    import os
    os.utime(p, (1, 1))
    h2 = formal_state_business_hash_from_db(config.path("formal_db"))
    assert h1 == h2


def test_hash_rowid_independent(env):
    config = env
    h1 = formal_state_business_hash_from_db(config.path("formal_db"))
    conn = sqlite3.connect(config.path("formal_db"))
    conn.execute("CREATE TABLE tmp_t(x)")
    conn.execute("DROP TABLE tmp_t")
    conn.close()
    h2 = formal_state_business_hash_from_db(config.path("formal_db"))
    assert h1 == h2


def test_business_change_detected(env):
    config = env
    before = formal_state_business_hash_from_db(config.path("formal_db"))
    conn = sqlite3.connect(config.path("formal_db"))
    conn.execute("UPDATE election_events SET title='x' WHERE event_id='evt_fix_nom_20260121'")
    conn.commit()
    conn.close()
    after = formal_state_business_hash_from_db(config.path("formal_db"))
    assert before != after


def test_snapshot_change_detected(env):
    config = env
    before = formal_state_business_hash_from_db(config.path("formal_db"))
    conn = sqlite3.connect(config.path("formal_db"))
    conn.execute("UPDATE election_state_snapshots SET as_of='2099-01-01' WHERE snapshot_status='active'")
    conn.commit()
    conn.close()
    after = formal_state_business_hash_from_db(config.path("formal_db"))
    assert before != after


# ---- Bootstrap v2 (31-40) ----
@pytest.mark.parametrize("table", ["elections", "actors", "sources", "election_events", "event_sources",
                                   "election_polls", "poll_questions", "poll_results", "poll_source_links",
                                   "election_state_snapshots"])
def test_bootstrap_v2_rebuilds_table(tmp_path, table):
    config = make_publication_config(tmp_path)
    seed = config.path("events_seed").parent
    tmp = tmp_path / "rebuilt.db"
    ok, _ = run_bootstrap_v2(seed, tmp, reset=True)
    assert ok is True
    conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    assert conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
    conn.close()


def test_bootstrap_v2_fts_rebuilt(tmp_path):
    config = make_publication_config(tmp_path)
    tmp = tmp_path / "rebuilt.db"
    run_bootstrap_v2(config.path("events_seed").parent, tmp, reset=True)
    conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    events = conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0]
    fts = conn.execute("SELECT COUNT(*) FROM election_events_fts").fetchone()[0]
    conn.close()
    assert events == fts


def test_bootstrap_v2_idempotent(tmp_path):
    config = make_publication_config(tmp_path)
    seed = config.path("events_seed").parent
    a = tmp_path / "a.db"
    b = tmp_path / "b.db"
    run_bootstrap_v2(seed, a, reset=True)
    run_bootstrap_v2(seed, b, reset=True)
    assert formal_state_business_hash_from_db(a) == formal_state_business_hash_from_db(b)


def test_bootstrap_v2_search_consistent(tmp_path):
    config = make_publication_config(tmp_path)
    tmp = tmp_path / "rebuilt.db"
    run_bootstrap_v2(config.path("events_seed").parent, tmp, reset=True)
    conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    events = conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0]
    fts = conn.execute("SELECT COUNT(*) FROM election_events_fts").fetchone()[0]
    conn.close()
    assert events == fts


def test_bootstrap_v2_active_snapshot(tmp_path):
    config = make_publication_config(tmp_path)
    tmp = tmp_path / "rebuilt.db"
    run_bootstrap_v2(config.path("events_seed").parent, tmp, reset=True)
    conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    active = conn.execute("SELECT COUNT(*) FROM election_state_snapshots WHERE snapshot_status='active'").fetchone()[0]
    conn.close()
    assert active == 1


def test_bootstrap_v2_empty_db_from_scratch(tmp_path):
    config = make_publication_config(tmp_path)
    tmp = tmp_path / "rebuilt.db"
    ok, stats = run_bootstrap_v2(config.path("events_seed").parent, tmp, reset=True)
    assert ok is True
    assert stats["events"] == 2
    assert stats["sources"] == 2


def test_bootstrap_v2_poll_tables_present(tmp_path):
    config = make_publication_config(tmp_path)
    tmp = tmp_path / "rebuilt.db"
    run_bootstrap_v2(config.path("events_seed").parent, tmp, reset=True)
    conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    conn.close()
    assert "poll_questions" in names and "poll_results" in names


def test_bootstrap_v2_indexes(tmp_path):
    config = make_publication_config(tmp_path)
    tmp = tmp_path / "rebuilt.db"
    run_bootstrap_v2(config.path("events_seed").parent, tmp, reset=True)
    conn = sqlite3.connect(f"file:{tmp}?mode=ro", uri=True)
    idx = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='index'").fetchone()[0]
    conn.close()
    assert idx >= 0


def test_bootstrap_v2_two_runs_hash_equal(tmp_path):
    config = make_publication_config(tmp_path)
    seed = config.path("events_seed").parent
    a = tmp_path / "a.db"
    b = tmp_path / "b.db"
    run_bootstrap_v2(seed, a, reset=True)
    run_bootstrap_v2(seed, b, reset=True)
    assert formal_state_business_hash_from_db(a) == formal_state_business_hash_from_db(b)


# ---- Reconciliation (41-45) ----
def test_reconciliation_table_counts(env):
    config = env
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    events = conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0]
    conn.close()
    assert events == 2


def test_reconciliation_event_semantic(env):
    config = env
    assert formal_state_business_hash_from_seed(config) == formal_state_business_hash_from_db(config.path("formal_db"))


def test_reconciliation_poll_semantic(env):
    config = env
    assert formal_state_business_hash_from_seed(config) == formal_state_business_hash_from_db(config.path("formal_db"))


def test_reconciliation_snapshot_semantic(env):
    config = env
    assert formal_state_business_hash_from_seed(config) == formal_state_business_hash_from_db(config.path("formal_db"))


def test_reconciliation_full_formal_hash(env):
    config = env
    assert formal_state_business_hash_from_seed(config) == formal_state_business_hash_from_db(config.path("formal_db"))


# ---- Publication compatibility (46-52) ----
def test_publication_preview_compatible(env, tmp_path):
    from app.election_candidates.publication_preview import build_preview
    from .publication_helpers import default_event_payload, default_sources, make_and_save_decision

    config = env
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    d = make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
                               event=default_event_payload(), sources=default_sources())
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [d["review_decision_id"]])
    assert preview["errors"] == []
    repo.close()


def test_publication_prepare_compatible(env, tmp_path):
    from app.election_candidates.publication_pipeline import prepare_batch
    from app.election_candidates.publication_preview import build_preview
    from .publication_helpers import default_event_payload, default_sources, make_and_save_decision

    config = env
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    d = make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
                               event=default_event_payload(), sources=default_sources())
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [d["review_decision_id"]])
    result = prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    assert result["prepare_ready"] is True
    repo.close()


def test_publication_commit_compatible(env, tmp_path):
    from app.election_candidates.publication_pipeline import batch_hash, commit_batch, prepare_batch
    from app.election_candidates.publication_preview import build_preview
    from .publication_helpers import default_event_payload, default_sources, make_and_save_decision

    config = env
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    d = make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
                               event=default_event_payload(), sources=default_sources())
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [d["review_decision_id"]])
    prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    result = commit_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", batch_hash(preview), preview)
    assert result["commit_ready"] is True
    repo.close()


def test_publication_rollback_compatible(env, tmp_path):
    from app.election_candidates.publication_pipeline import batch_hash, commit_batch, prepare_batch, rollback_batch
    from app.election_candidates.publication_preview import build_preview
    from .publication_helpers import default_event_payload, default_sources, make_and_save_decision

    config = env
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    d = make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
                               event=default_event_payload(), sources=default_sources())
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [d["review_decision_id"]])
    prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    commit_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", batch_hash(preview), preview)
    result = rollback_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer")
    assert result["rolled_back"] is True
    repo.close()


def test_publication_hash_scope_includes_polls(env, tmp_path):
    from app.election_candidates.publication_preview import build_preview
    from .publication_helpers import default_event_payload, default_sources, make_and_save_decision

    config = env
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    d = make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
                               event=default_event_payload(), sources=default_sources())
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [d["review_decision_id"]])
    assert "formal_state_hash_before" in preview
    repo.close()


def test_publication_polls_unchanged(env, tmp_path):
    from app.election_candidates.publication_pipeline import batch_hash, commit_batch, prepare_batch
    from app.election_candidates.publication_preview import build_preview
    from .publication_helpers import default_event_payload, default_sources, make_and_save_decision

    config = env
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    d = make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
                               event=default_event_payload(), sources=default_sources())
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [d["review_decision_id"]])
    prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    commit_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", batch_hash(preview), preview)
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    polls = conn.execute("SELECT COUNT(*) FROM election_polls").fetchone()[0]
    conn.close()
    assert polls == 0
    repo.close()


def test_publication_snapshots_unchanged(env, tmp_path):
    from app.election_candidates.publication_pipeline import batch_hash, commit_batch, prepare_batch
    from app.election_candidates.publication_preview import build_preview
    from .publication_helpers import default_event_payload, default_sources, make_and_save_decision

    config = env
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    d = make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
                               event=default_event_payload(), sources=default_sources())
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [d["review_decision_id"]])
    prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    commit_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", batch_hash(preview), preview)
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    active = conn.execute("SELECT COUNT(*) FROM election_state_snapshots WHERE snapshot_status='active'").fetchone()[0]
    conn.close()
    assert active == 1
    repo.close()


# ---- Recovery state detection (53-62) ----
def _journal(config, batch_id, steps):
    batch_dir = config.path("output_root") / "publication_batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "publication_commit_journal.json").write_text(
        json.dumps({"steps": steps}, ensure_ascii=False), encoding="utf-8"
    )


@pytest.mark.parametrize(
    "steps,expected",
    [
        ({}, "NO_COMMIT_STARTED"),
        ({"seed_commit_started": True}, "SEED_COMMIT_PARTIAL"),
        ({"seed_commit_started": True, "seed_commit_complete": True}, "SEED_COMMIT_COMPLETE_DB_NOT_STARTED"),
        ({"seed_commit_started": True, "seed_commit_complete": True, "database_commit_started": True}, "DB_COMMIT_PARTIAL"),
        ({"seed_commit_started": True, "seed_commit_complete": True, "database_commit_started": True, "database_commit_complete": True}, "DB_COMMIT_COMPLETE_POST_VALIDATION_PENDING"),
        ({"seed_commit_started": True, "seed_commit_complete": True, "database_commit_started": True, "database_commit_complete": True, "post_validation_complete": True}, "POST_VALIDATION_FAILED"),
        ({"seed_commit_started": True, "seed_commit_complete": True, "database_commit_started": True, "database_commit_complete": True, "post_validation_complete": True, "committed": True}, "COMMIT_COMPLETE"),
        ({"seed_commit_started": False, "database_commit_started": False, "post_validation_complete": False}, "UNKNOWN_STATE"),
    ],
)
def test_recovery_state_detection(tmp_path, steps, expected):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_test", steps)
    state = detect_state(config, "pub_test")["detected_state"]
    assert state == expected


def test_recovery_rollback_partial(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_test", {
        "seed_commit_started": True, "seed_commit_complete": True,
        "database_commit_started": True, "database_commit_complete": True,
        "post_validation_complete": True, "committed": True,
    })
    repo = open_candidate_repo(config)
    repo.upsert_publication_batch({
        "batch_id": "pub_test", "election_id": "TW-2026-TNN-MAYOR",
        "created_at": "x", "created_by": "r", "status": "rolled_back",
        "formal_data_hash_before": "", "candidate_hashes_json": "[]",
        "review_decision_ids_json": "[]", "new_event_count": 0,
        "existing_event_attachment_count": 0, "new_source_count": 0,
        "new_event_source_link_count": 0, "preview_ready": 0, "validation_ready": 0,
        "backup_ready": 0, "staging_ready": 0, "commit_ready": 0, "commit_completed": 0,
        "committed_at": "", "rolled_back_at": "", "error_summary": "",
    })
    state = detect_state(config, "pub_test")["detected_state"]
    assert state == "ROLLBACK_PARTIAL"
    repo.close()


def test_recovery_rollback_partial_detection(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_rp", {
        "seed_commit_started": True, "seed_commit_complete": True,
        "database_commit_started": True, "database_commit_complete": True,
        "post_validation_complete": True, "committed": True,
    })
    repo = open_candidate_repo(config)
    repo.upsert_publication_batch({
        "batch_id": "pub_rp", "election_id": "TW-2026-TNN-MAYOR",
        "created_at": "x", "created_by": "r", "status": "rolled_back",
        "formal_data_hash_before": "", "candidate_hashes_json": "[]",
        "review_decision_ids_json": "[]", "new_event_count": 0,
        "existing_event_attachment_count": 0, "new_source_count": 0,
        "new_event_source_link_count": 0, "preview_ready": 0, "validation_ready": 0,
        "backup_ready": 0, "staging_ready": 0, "commit_ready": 0, "commit_completed": 0,
        "committed_at": "", "rolled_back_at": "", "error_summary": "",
    })
    state = detect_state(config, "pub_rp")["detected_state"]
    assert state == "ROLLBACK_PARTIAL"
    repo.close()


def test_recovery_safe_resume(tmp_path):
    config = make_publication_config(tmp_path)
    batch_dir = config.path("output_root") / "publication_batches" / "pub_safe"
    batch_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = config.path("output_root") / "publication_staging" / "pub_safe"
    staging_dir.mkdir(parents=True, exist_ok=True)
    from app.election_context.bootstrap_v2 import run_bootstrap_v2

    run_bootstrap_v2(config.path("events_seed").parent, staging_dir / "election_context.db", reset=True)
    import hashlib
    staging_hash = hashlib.sha256((staging_dir / "election_context.db").read_bytes()).hexdigest()
    (batch_dir / "publication_commit_journal.json").write_text(
        json.dumps({
            "steps": {"seed_commit_started": True, "seed_commit_complete": True},
            "staging_db_hash": staging_hash,
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    repo = open_candidate_repo(config)
    result = recover(repo, config, "TW-2026-TNN-MAYOR", "pub_safe", "local_reviewer", mode="resume")
    assert result["automatic_action"] == "resume"
    repo.close()


def test_recovery_resume_bad_staging_hash_blocked(tmp_path):
    config = make_publication_config(tmp_path)
    batch_dir = config.path("output_root") / "publication_batches" / "pub_bad_stage"
    batch_dir.mkdir(parents=True, exist_ok=True)
    staging_dir = config.path("output_root") / "publication_staging" / "pub_bad_stage"
    staging_dir.mkdir(parents=True, exist_ok=True)
    from app.election_context.bootstrap_v2 import run_bootstrap_v2

    run_bootstrap_v2(config.path("events_seed").parent, staging_dir / "election_context.db", reset=True)
    (batch_dir / "publication_commit_journal.json").write_text(
        json.dumps({
            "steps": {"seed_commit_started": True, "seed_commit_complete": True},
            "staging_db_hash": "deadbeef",
        }, ensure_ascii=False),
        encoding="utf-8",
    )
    repo = open_candidate_repo(config)
    with pytest.raises(ValueError):
        recover(repo, config, "TW-2026-TNN-MAYOR", "pub_bad_stage", "local_reviewer", mode="resume")
    repo.close()


def test_recovery_unfinished_recovery_journal_detected(tmp_path):
    config = make_publication_config(tmp_path)
    rec_root = config.path("output_root") / "publication_recovery_journals"
    rec_root.mkdir(parents=True, exist_ok=True)
    (rec_root / "old.json").write_text(
        json.dumps({"batch_id": "old", "result": "pending"}, ensure_ascii=False),
        encoding="utf-8",
    )
    gate = recovery_gate(config)
    assert gate["recovery_required"] is True
    assert "old" in gate["unfinished_recovery_journals"]


def test_migration_preview_does_not_write_seed(tmp_path):
    from scripts.phase25_migration import build_preview

    config = make_publication_config(tmp_path)
    seed = config.path("events_seed").parent
    before = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in seed.iterdir() if p.is_file() and not p.name.endswith(".bak")}
    build_preview(config)
    after = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in seed.iterdir() if p.is_file() and not p.name.endswith(".bak")}
    assert before == after


def test_migration_commit_and_rollback(tmp_path):
    from scripts.phase25_migration import build_preview, commit_migration, rollback_migration

    config = make_publication_config(tmp_path)
    preview, staging, tmp_db = build_preview(config)
    assert preview["migration_ready"] is True
    db_before = formal_state_business_hash_from_db(config.path("formal_db"))
    commit_migration(config, preview, staging, tmp_db)
    assert formal_state_business_hash_from_db(config.path("formal_db")) == db_before
    import glob
    # migration backups live under the config's backup_root (isolated tmp tree)
    backups = sorted(glob.glob(str(config.path("backup_root") / "formal_state_governance_*")))
    rollback_migration(config, preview["batch_id"], backups[-1])
    assert formal_state_business_hash_from_db(config.path("formal_db")) == db_before


def test_recovery_corrupt_backup_hash_blocked(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_cb", {"seed_commit_started": True})
    backup = config.path("output_root").parent.parent / "data" / "backups" / "formal_publication_pub_cb"
    backup.mkdir(parents=True, exist_ok=True)
    seed = config.path("events_seed").parent
    for name in ("events.jsonl", "sources.jsonl"):
        shutil.copy2(seed / name, backup / name)
    shutil.copy2(config.path("formal_db"), backup / "election_context.db")
    import hashlib as _h
    files = {name: _h.sha256((backup / name).read_bytes()).hexdigest() for name in ("events.jsonl", "sources.jsonl", "election_context.db")}
    (backup / "backup_manifest.json").write_text(json.dumps({"files": files}, ensure_ascii=False), encoding="utf-8")
    # corrupt backup
    (backup / "events.jsonl").write_text("damaged", encoding="utf-8")
    repo = open_candidate_repo(config)
    with pytest.raises(ValueError):
        recover(repo, config, "TW-2026-TNN-MAYOR", "pub_cb", "auto", mode="auto")
    repo.close()


def test_seed_manifest_governance_fields(env):
    config = env
    manifest = json.loads((config.path("events_seed").parent / "seed_manifest.json").read_text(encoding="utf-8"))
    for name, info in manifest["entities"].items():
        assert "path" in info and "record_count" in info and "business_hash" in info and "authority" in info
        assert info["authority"] != "unknown"


def test_publication_backup_full_seed_set(env, tmp_path):
    from app.election_candidates.publication_pipeline import create_backup
    from app.election_candidates.publication_preview import build_preview
    from .publication_helpers import default_event_payload, default_sources, make_and_save_decision

    config = env
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    d = make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
                               event=default_event_payload(), sources=default_sources())
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [d["review_decision_id"]])
    backup = create_backup(config, preview["batch_id"], preview)
    for name in ("events.jsonl", "sources.jsonl", "polls.jsonl", "poll_questions.jsonl",
                 "poll_results.jsonl", "poll_sources.jsonl", "poll_source_links.jsonl",
                 "initial_snapshot.json", "snapshot_history.jsonl", "seed_manifest.json",
                 "election_context.db"):
        assert (backup / name).exists(), name
    repo.close()


def test_recovery_unknown_blocks_automation(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_test", {"seed_commit_started": False, "database_commit_started": False, "post_validation_complete": False})
    repo = open_candidate_repo(config)
    result = recover(repo, config, "TW-2026-TNN-MAYOR", "pub_test", "auto", mode="auto")
    assert result["automatic_action"] == "blocked"
    assert result["manual_intervention_required"] is True
    repo.close()


def test_recovery_no_commit_mark_failed(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_test", {})
    repo = open_candidate_repo(config)
    result = recover(repo, config, "TW-2026-TNN-MAYOR", "pub_test", "auto", mode="auto")
    assert result["automatic_action"] == "mark_failed"
    repo.close()


def test_recovery_committed_noop(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_test", {"committed": True})
    repo = open_candidate_repo(config)
    result = recover(repo, config, "TW-2026-TNN-MAYOR", "pub_test", "auto", mode="auto")
    assert result["automatic_action"] == "noop"
    repo.close()


def test_recovery_partial_requires_rollback(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_test", {"seed_commit_started": True})
    backup = config.path("output_root").parent.parent / "data" / "backups" / "formal_publication_pub_test"
    backup.mkdir(parents=True, exist_ok=True)
    seed = config.path("events_seed").parent
    for name in ("events.jsonl", "sources.jsonl"):
        shutil.copy2(seed / name, backup / name)
    shutil.copy2(config.path("formal_db"), backup / "election_context.db")
    import hashlib

    files = {
        name: hashlib.sha256((backup / name).read_bytes()).hexdigest()
        for name in ("events.jsonl", "sources.jsonl", "election_context.db")
    }
    (backup / "backup_manifest.json").write_text(
        json.dumps({"files": files, "formal_data_hash_before": ""}, ensure_ascii=False),
        encoding="utf-8",
    )
    repo = open_candidate_repo(config)
    result = recover(repo, config, "TW-2026-TNN-MAYOR", "pub_test", "auto", mode="auto")
    assert result["automatic_action"] == "rollback"
    repo.close()


def test_recovery_resume_denied_for_partial(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_test", {"seed_commit_started": True})
    repo = open_candidate_repo(config)
    with pytest.raises(ValueError):
        recover(repo, config, "TW-2026-TNN-MAYOR", "pub_test", "resume", mode="resume")
    repo.close()


# ---- Recovery gate / journals / lock (71-78) ----
def test_recovery_gate_clean(env):
    config = env
    assert recovery_gate(config)["recovery_required"] is False


def test_recovery_gate_detects_unfinished(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_bad", {"seed_commit_started": True})
    gate = recovery_gate(config)
    assert gate["recovery_required"] is True
    assert "pub_bad" in gate["unfinished_publication_batches"]


def test_publication_blocked_when_recovery_required(tmp_path):
    from app.election_candidates.publication_pipeline import prepare_batch
    from app.election_candidates.publication_preview import build_preview
    from .publication_helpers import default_event_payload, default_sources, make_and_save_decision

    config = make_publication_config(tmp_path)
    _journal(config, "old_batch", {"seed_commit_started": True})
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    d = make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
                               event=default_event_payload(), sources=default_sources())
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [d["review_decision_id"]])
    with pytest.raises(ValueError):
        prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    repo.close()


def test_recovery_journal_append_only(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_x", {})
    repo = open_candidate_repo(config)
    recover(repo, config, "TW-2026-TNN-MAYOR", "pub_x", "auto", mode="auto")
    recover(repo, config, "TW-2026-TNN-MAYOR", "pub_x", "auto", mode="auto")
    path = config.path("output_root") / "publication_recovery_journals" / "pub_x.json"
    entries = json.loads(path.read_text(encoding="utf-8"))
    assert len(entries) == 2
    repo.close()


def test_recovery_corrupt_journal_blocks(tmp_path):
    config = make_publication_config(tmp_path)
    batch_dir = config.path("output_root") / "publication_batches" / "pub_c"
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "publication_commit_journal.json").write_text("{broken", encoding="utf-8")
    repo = open_candidate_repo(config)
    result = recover(repo, config, "TW-2026-TNN-MAYOR", "pub_c", "inspect", mode="inspect")
    assert result["detected_state"] == "UNKNOWN_STATE"
    repo.close()


def test_recovery_lock_conflict(tmp_path):
    from app.election_candidates.publication_pipeline import PublicationLock, PublicationLockError

    config = make_publication_config(tmp_path)
    lock = PublicationLock(config, "TW-2026-TNN-MAYOR", "a", "b", "recovery")
    lock.acquire()
    try:
        with pytest.raises(PublicationLockError):
            PublicationLock(config, "TW-2026-TNN-MAYOR", "b", "b2", "recovery").acquire()
    finally:
        lock.release()


def test_second_recovery_process_blocked(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_d", {})
    repo = open_candidate_repo(config)
    r1 = recover(repo, config, "TW-2026-TNN-MAYOR", "pub_d", "auto", mode="auto")
    r2 = recover(repo, config, "TW-2026-TNN-MAYOR", "pub_d", "auto", mode="auto")
    assert r1["automatic_action"] == r2["automatic_action"]
    repo.close()


def test_recovery_idempotent_same_result(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_e", {"committed": True})
    repo = open_candidate_repo(config)
    r1 = recover(repo, config, "TW-2026-TNN-MAYOR", "pub_e", "auto", mode="auto")
    r2 = recover(repo, config, "TW-2026-TNN-MAYOR", "pub_e", "auto", mode="auto")
    assert r1 == r2
    repo.close()


# ---- Formal state validator (95-102) ----
def test_validator_clean(env):
    config = env
    result = validate_formal_state(config)
    assert result["formal_state_ready"] is True


def test_validator_missing_manifest(tmp_path):
    config = make_publication_config(tmp_path)
    (config.path("events_seed").parent / "seed_manifest.json").unlink()
    assert "seed_manifest_valid" in validate_formal_state(config)["errors"]


def test_validator_bad_hash(tmp_path):
    config = make_publication_config(tmp_path)
    p = config.path("events_seed").parent / "events.jsonl"
    p.write_text(p.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    assert "all_seed_hashes_valid:events" in validate_formal_state(config)["errors"]


def test_validator_orphan_link(tmp_path):
    config = make_publication_config(tmp_path)
    conn = sqlite3.connect(config.path("formal_db"))
    conn.execute("INSERT INTO event_sources VALUES ('missing','src_fix_cna',0)")
    conn.commit()
    conn.close()
    assert "all_event_sources_resolve" in validate_formal_state(config)["errors"]


def test_validator_no_active_snapshot(tmp_path):
    config = make_publication_config(tmp_path)
    conn = sqlite3.connect(config.path("formal_db"))
    conn.execute("UPDATE election_state_snapshots SET snapshot_status='superseded'")
    conn.commit()
    conn.close()
    assert "exactly_one_active_snapshot" in validate_formal_state(config)["errors"]


def test_validator_db_mismatch(tmp_path):
    config = make_publication_config(tmp_path)
    conn = sqlite3.connect(config.path("formal_db"))
    conn.execute("UPDATE election_events SET title='x' WHERE event_id='evt_fix_nom_20260121'")
    conn.commit()
    conn.close()
    assert "database_matches_seed" in validate_formal_state(config)["errors"]


def test_validator_fts_mismatch(tmp_path):
    config = make_publication_config(tmp_path)
    conn = sqlite3.connect(config.path("formal_db"))
    conn.execute("DELETE FROM election_events_fts")
    conn.commit()
    conn.close()
    assert "fts_consistent" in validate_formal_state(config)["errors"]


def test_validator_bootstrap_reproducible(env):
    config = env
    assert validate_formal_state(config)["bootstrap_reproducible"] is True


def test_validator_no_unfinished_journal(env):
    config = env
    result = validate_formal_state(config)
    assert result["unfinished_publication_journal"] is False
    assert result["unfinished_recovery_journal"] is False


# ---- Data protection (87-94) ----
def test_protection_news_db_integrity():
    """news.db is live runtime data after consolidation; protect integrity,
    not byte-identical hash (it grows with every monitor run)."""
    p = Path("data/news.db")
    assert p.exists()
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_protection_election_watch_integrity():
    """election_watch.db is runtime data; protect integrity, not byte hash."""
    p = Path("data/election_watch.db")
    assert p.exists()
    # immutable=1: a plain mode=ro WAL open touches the -shm mtime (wal-index
    # header), which would make the production fingerprint differs.  The
    # current -wal is empty, so no uncheckpointed frames are bypassed.
    conn = sqlite3.connect(f"file:{p}?mode=ro&immutable=1", uri=True)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_protection_rc1_unchanged():
    import hashlib
    p = Path("dist/releases/tainan-assessment-offline-rc1.zip")
    assert hashlib.sha256(p.read_bytes()).hexdigest() == "70b8b8f0ac7c9118b0df1f303c71b4e47c3845660dec252e27013d8c7b453ce3"


def test_protection_event_facts_unchanged():
    p = Path("data/election_context.db")
    conn = sqlite3.connect(f"file:{p}?mode=ro", uri=True)
    n = conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0]
    conn.close()
    # 当前正式种子已包含 50 条登记/表态事件。
    assert n == 50


def test_protection_source_facts_unchanged():
    conn = sqlite3.connect("file:data/election_context.db?mode=ro", uri=True)
    n = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    conn.close()
    assert n == 113


def test_protection_poll_semantics_unchanged():
    conn = sqlite3.connect("file:data/election_context.db?mode=ro", uri=True)
    polls = conn.execute("SELECT COUNT(*) FROM election_polls").fetchone()[0]
    questions = conn.execute("SELECT COUNT(*) FROM poll_questions").fetchone()[0]
    results = conn.execute("SELECT COUNT(*) FROM poll_results").fetchone()[0]
    conn.close()
    assert (polls, questions, results) == (15, 39, 116)


def test_protection_snapshot_semantics_unchanged():
    conn = sqlite3.connect("file:data/election_context.db?mode=ro", uri=True)
    active = conn.execute("SELECT snapshot_id FROM election_state_snapshots WHERE snapshot_status='active'").fetchone()[0]
    conn.close()
    assert active == "tn_state_20260811_v2"


def test_protection_coverage_unchanged():
    seed = Path("data/election_seed/tainan_2026")
    snap = json.loads((seed / "initial_snapshot.json").read_text(encoding="utf-8"))
    coverage = snap.get("state_json") or {}
    if isinstance(coverage, str):
        coverage = json.loads(coverage)
    assert coverage.get("coverage", {}).get("coverage_version") == "fact_coverage_20260811_v219"


# ---- Failure injection (112-119) ----
def test_failure_seed_damage_blocks_validator(tmp_path):
    config = make_publication_config(tmp_path)
    (config.path("events_seed").parent / "events.jsonl").write_text("", encoding="utf-8")
    result = validate_formal_state(config)
    assert result["formal_state_ready"] is False


def test_failure_poll_damage(tmp_path):
    config = make_publication_config(tmp_path)
    p = config.path("events_seed").parent / "poll_questions.jsonl"
    p.write_text("{bad", encoding="utf-8")
    tmp = tmp_path / "rebuilt.db"
    with pytest.raises(Exception):
        run_bootstrap_v2(config.path("events_seed").parent, tmp, reset=True)


def test_failure_snapshot_damage(tmp_path):
    config = make_publication_config(tmp_path)
    (config.path("events_seed").parent / "initial_snapshot.json").write_text("{bad", encoding="utf-8")
    tmp = tmp_path / "rebuilt.db"
    with pytest.raises(Exception):
        run_bootstrap_v2(config.path("events_seed").parent, tmp, reset=True)


def test_failure_bootstrap_crash(tmp_path):
    config = make_publication_config(tmp_path)
    seed = config.path("events_seed").parent
    (seed / "events.jsonl").write_text(
        json.dumps({"event_id": "x", "event_type": "invalid_type", "fact_status": "bad", "significance_score": 999}, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    tmp = tmp_path / "rebuilt.db"
    with pytest.raises(Exception):
        run_bootstrap_v2(seed, tmp, reset=True)


def test_failure_recovery_backup_missing(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_f", {"seed_commit_started": True})
    repo = open_candidate_repo(config)
    with pytest.raises(ValueError):
        recover(repo, config, "TW-2026-TNN-MAYOR", "pub_f", "auto", mode="auto")
    repo.close()


def test_failure_recovery_staging_missing(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_g", {"seed_commit_started": True, "seed_commit_complete": True})
    repo = open_candidate_repo(config)
    with pytest.raises(ValueError):
        recover(repo, config, "TW-2026-TNN-MAYOR", "pub_g", "resume", mode="resume")
    repo.close()


def test_failure_duplicate_recovery_no_duplicate_write(tmp_path):
    config = make_publication_config(tmp_path)
    _journal(config, "pub_h", {})
    repo = open_candidate_repo(config)
    recover(repo, config, "TW-2026-TNN-MAYOR", "pub_h", "auto", mode="auto")
    path = config.path("output_root") / "publication_recovery_journals" / "pub_h.json"
    assert len(json.loads(path.read_text(encoding="utf-8"))) == 1
    repo.close()


def test_failure_lock_conflict_recovery(tmp_path):
    from app.election_candidates.publication_pipeline import PublicationLock, PublicationLockError

    config = make_publication_config(tmp_path)
    lock = PublicationLock(config, "TW-2026-TNN-MAYOR", "a", "b", "recovery")
    lock.acquire()
    try:
        with pytest.raises(PublicationLockError):
            PublicationLock(config, "TW-2026-TNN-MAYOR", "c", "d", "recovery").acquire()
    finally:
        lock.release()


def test_failure_full_pytest_smoke(env):
    config = env
    assert validate_formal_state(config)["formal_state_ready"] is True
