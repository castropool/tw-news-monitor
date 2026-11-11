from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from app.election_candidates.config import load_config
from app.election_context.coverage_builder import build_coverage
from app.election_context.formal_state_hash import formal_state_business_hash_from_db
from app.election_context.snapshot_pipeline import commit_snapshot

from .phase3_helpers import sha256_file


ROOT = Path(__file__).resolve().parent.parent.parent


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def _active_snapshot_hash():
    db = ROOT / "data" / "election_context.db"
    if not db.exists():
        return "missing"
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = conn.execute(
        "SELECT state_json FROM election_state_snapshots WHERE snapshot_status='active'"
    ).fetchone()
    conn.close()
    state = json.loads(row[0]) if isinstance(row[0], str) else row[0]
    return hashlib.sha256(
        json.dumps(state, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


REAL_FILES = {
    "formal_db": ROOT / "data" / "election_context.db",
    "events_seed": ROOT / "data" / "election_seed" / "tainan_2026" / "events.jsonl",
    "sources_seed": ROOT / "data" / "election_seed" / "tainan_2026" / "sources.jsonl",
    "polls_seed": ROOT / "data" / "election_seed" / "tainan_2026" / "polls.jsonl",
    "poll_questions_seed": ROOT / "data" / "election_seed" / "tainan_2026" / "poll_questions.jsonl",
    "poll_results_seed": ROOT / "data" / "election_seed" / "tainan_2026" / "poll_results.jsonl",
    "initial_snapshot_seed": ROOT / "data" / "election_seed" / "tainan_2026" / "initial_snapshot.json",
    "snapshot_history_seed": ROOT / "data" / "election_seed" / "tainan_2026" / "snapshot_history.jsonl",
    "frozen_rc1": ROOT / "dist" / "releases" / "tainan-assessment-offline-rc1.zip",
}
BASELINE = {k: _sha(v) for k, v in REAL_FILES.items()}
BASELINE["active_snapshot_state"] = _active_snapshot_hash()


@pytest.mark.parametrize("key", sorted(REAL_FILES), ids=sorted(REAL_FILES))
def test_real_data_files_unchanged(key):
    assert _sha(REAL_FILES[key]) == BASELINE[key]


@pytest.mark.parametrize("name", ["news.db", "election_watch.db"])
def test_live_runtime_db_integrity(name):
    """news.db / election_watch.db are live runtime data after consolidation;
    protect integrity instead of byte-identical hash."""
    p = ROOT / "data" / name
    assert p.exists()
    # immutable=1 for election_watch.db (WAL): a plain mode=ro open updates
    # the -shm mtime and would dirty the production fingerprint.  The live
    # -wal is empty, so no uncheckpointed frames are bypassed.
    uri = f"file:{p}?mode=ro&immutable=1"
    conn = sqlite3.connect(uri, uri=True)
    try:
        assert conn.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    finally:
        conn.close()


def test_real_active_snapshot_state_unchanged():
    assert _active_snapshot_hash() == BASELINE["active_snapshot_state"]


def test_real_coverage_build_is_read_only():
    config = load_config("config/election_candidate_pipeline.yaml")
    before = formal_state_business_hash_from_db(config.path("formal_db"))
    build_coverage(config, requested_start="2025-08-01", requested_end="2026-07-27")
    after = formal_state_business_hash_from_db(config.path("formal_db"))
    assert before == after


def test_real_snapshot_commit_blocked_without_test_mode():
    config = load_config("config/election_candidate_pipeline.yaml")
    assert config.test_mode is False
    with pytest.raises(PermissionError):
        commit_snapshot(config, "dr_real_probe", {}, allow_real=False)


def test_production_real_flags_remain_false():
    config = load_config("config/election_candidate_pipeline.yaml")
    assert config.test_mode is False
    gates = ROOT / "data" / "election_candidates" / "tainan_2026" / "phase3_validation"
    if (gates / "phase3_production_preflight.json").exists():
        preflight = json.loads((gates / "phase3_production_preflight.json").read_text(encoding="utf-8"))
        assert preflight["production_end_to_end_ready"] is False
        assert preflight["production_llm_ready"] is False
        assert preflight["production_delivery_ready"] is False
