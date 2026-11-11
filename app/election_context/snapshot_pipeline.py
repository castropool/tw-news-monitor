"""Snapshot staging/commit/rollback with journal and atomic replace."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.time_utils import TAIPEI

from app.election_candidates.publication_pipeline import PublicationLock
from app.election_context.bootstrap_v2 import run_bootstrap_v2
from app.election_context.formal_state_hash import (
    formal_state_business_hash_from_db,
    formal_state_business_hash_from_seed_dir,
)


def _atomic_write(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _journal_path(config, refresh_batch_id: str) -> Path:
    d = config.path("post_publication_root") / refresh_batch_id
    d.mkdir(parents=True, exist_ok=True)
    return d / "snapshot_refresh_journal.json"


def _load_journal(config, refresh_batch_id: str) -> dict[str, Any]:
    p = _journal_path(config, refresh_batch_id)
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {"steps": {}}


def _write_journal(config, refresh_batch_id: str, journal: dict[str, Any]):
    _atomic_write(_journal_path(config, refresh_batch_id), json.dumps(journal, ensure_ascii=False, indent=2).encode("utf-8"))


def _backup_dir(config, refresh_batch_id: str) -> Path:
    return config.path("backup_root") / f"formal_snapshot_{refresh_batch_id}"


def prepare_snapshot_staging(config, refresh_batch_id: str, candidate: dict[str, Any]) -> Path:
    seed_src = config.path("events_seed").parent
    backup = _backup_dir(config, refresh_batch_id)
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True, exist_ok=True)
    for p in seed_src.iterdir():
        if p.is_file() and not p.name.endswith(".bak"):
            shutil.copy2(p, backup / p.name)
    shutil.copy2(config.path("formal_db"), backup / "election_context.db")
    before_hash = formal_state_business_hash_from_db(config.path("formal_db"))
    backup_manifest = {
        "refresh_batch_id": refresh_batch_id,
        "before_refresh_hash": before_hash,
        "created_at": datetime.now(TAIPEI).isoformat(),
    }
    (backup / "backup_manifest.json").write_text(json.dumps(backup_manifest, ensure_ascii=False, indent=2), encoding="utf-8")

    staging = config.path("post_publication_root") / refresh_batch_id / "snapshot_staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    for p in seed_src.iterdir():
        if p.is_file() and not p.name.endswith(".bak"):
            shutil.copy2(p, staging / p.name)

    history = []
    hist_path = seed_src / "snapshot_history.jsonl"
    if hist_path.exists():
        history = [json.loads(l) for l in hist_path.read_text(encoding="utf-8").splitlines() if l.strip()]
    prev = _active_row(config.path("formal_db"))
    if prev:
        prev["snapshot_status"] = "superseded"
        prev["superseded_by"] = candidate["candidate_snapshot_id"]
        prev["superseded_at"] = candidate["effective_date"]
        history.append(prev)
    history.sort(key=lambda s: s.get("as_of", ""))
    _atomic_write(staging / "snapshot_history.jsonl",
                  "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in history).encode("utf-8"))

    active = {
        "snapshot_id": candidate["candidate_snapshot_id"],
        "election_id": config.resolve_election_id(config.canonical_election_id),
        "as_of": candidate["effective_date"],
        "state_json": candidate["dimensions"],
        "supporting_event_ids": candidate.get("supporting_event_ids", []),
        "snapshot_status": "active",
        "superseded_by": None,
        "superseded_at": None,
    }
    _atomic_write(staging / "initial_snapshot.json", json.dumps(active, ensure_ascii=False, indent=2).encode("utf-8"))

    from scripts.phase25_migration import _write_manifest
    _write_manifest(staging)
    staging_db = staging / "election_context.db"
    ok, _ = run_bootstrap_v2(staging, staging_db, reset=True)
    if not ok:
        raise RuntimeError("snapshot staging bootstrap failed")
    if formal_state_business_hash_from_seed_dir(staging) != formal_state_business_hash_from_db(staging_db):
        raise RuntimeError("snapshot staging hash mismatch")
    (staging / "staging_validation.json").write_text(
        json.dumps(
            {
                "staging_ready": True,
                "bootstrap_reproducible": True,
                "hash_matches": True,
                "candidate_snapshot_id": candidate.get("candidate_snapshot_id"),
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return staging


def _active_row(db_path: Path) -> dict[str, Any] | None:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute("SELECT * FROM election_state_snapshots WHERE snapshot_status='active' ORDER BY as_of DESC LIMIT 1").fetchone()
    conn.close()
    if not row:
        return None
    d = dict(row)
    if isinstance(d.get("state_json"), str):
        d["state_json"] = json.loads(d["state_json"])
    if isinstance(d.get("supporting_event_ids_json"), str):
        d["supporting_event_ids"] = json.loads(d["supporting_event_ids_json"])
    return d


def commit_snapshot(
    config,
    refresh_batch_id: str,
    candidate: dict[str, Any],
    reviewer: str = "snapshot_operator",
    allow_real: bool = False,
) -> dict[str, Any]:
    if not allow_real and not config.test_mode:
        raise PermissionError("real snapshot activation blocked; fixture/test mode required")
    staging = config.path("post_publication_root") / refresh_batch_id / "snapshot_staging"
    if not (staging / "initial_snapshot.json").exists():
        staging = prepare_snapshot_staging(config, refresh_batch_id, candidate)
    seed_src = config.path("events_seed").parent
    journal = _load_journal(config, refresh_batch_id)
    journal.update({
        "refresh_batch_id": refresh_batch_id,
        "candidate_snapshot_id": candidate["candidate_snapshot_id"],
        "steps": {"prepared": True, "backup_complete": True},
    })
    _write_journal(config, refresh_batch_id, journal)

    with PublicationLock(config, config.resolve_election_id(config.canonical_election_id), reviewer, refresh_batch_id, "snapshot_commit"):
        journal["steps"]["seed_commit_started"] = True
        _write_journal(config, refresh_batch_id, journal)
        for name in ("initial_snapshot.json", "snapshot_history.jsonl", "seed_manifest.json",
                     "schema_versions.json"):
            src = staging / name
            if src.exists():
                _atomic_write(seed_src / name, src.read_bytes())
        journal["steps"]["seed_commit_complete"] = True
        _write_journal(config, refresh_batch_id, journal)

        journal["steps"]["database_rebuild_started"] = True
        _write_journal(config, refresh_batch_id, journal)
        fresh = staging.parent / "fresh.db"
        ok, _ = run_bootstrap_v2(seed_src, fresh, reset=True)
        if not ok:
            raise RuntimeError("snapshot commit bootstrap failed")
        _atomic_write(config.path("formal_db"), fresh.read_bytes())
        journal["steps"]["database_rebuild_complete"] = True
        _write_journal(config, refresh_batch_id, journal)

        from app.election_context.formal_state_validator import validate_formal_state
        result = validate_formal_state(config)
        if not result["formal_state_ready"]:
            raise RuntimeError(f"snapshot post validation failed: {result['errors']}")
        journal["steps"]["post_validation_complete"] = True
        journal["steps"]["committed"] = True
        journal["finished_at"] = datetime.now(TAIPEI).isoformat()
        _write_journal(config, refresh_batch_id, journal)
        return {"snapshot_committed": True, "candidate_snapshot_id": candidate["candidate_snapshot_id"],
                "formal_state_hash": formal_state_business_hash_from_db(config.path("formal_db"))}


def rollback_snapshot(config, refresh_batch_id: str, reviewer: str = "snapshot_operator") -> dict[str, Any]:
    backup = _backup_dir(config, refresh_batch_id)
    manifest_path = backup / "backup_manifest.json"
    if not manifest_path.exists():
        raise ValueError("snapshot backup missing")
    journal = _load_journal(config, refresh_batch_id)
    if (journal.get("steps") or {}).get("rolled_back"):
        raise ValueError("snapshot rollback already completed")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    seed_src = config.path("events_seed").parent
    with PublicationLock(config, config.resolve_election_id(config.canonical_election_id), reviewer, refresh_batch_id, "snapshot_rollback"):
        for p in backup.iterdir():
            if p.is_file() and p.name != "election_context.db":
                _atomic_write(seed_src / p.name, p.read_bytes())
        db_backup = backup / "election_context.db"
        if db_backup.exists():
            _atomic_write(config.path("formal_db"), db_backup.read_bytes())
        after = formal_state_business_hash_from_db(config.path("formal_db"))
        if after != manifest.get("before_refresh_hash"):
            raise RuntimeError("snapshot rollback hash restoration failed")
        journal["steps"]["rolled_back"] = True
        _write_journal(config, refresh_batch_id, journal)
        return {"snapshot_rolled_back": True, "formal_state_hash_after": after}


def detect_snapshot_recovery_required(config, refresh_batch_id: str) -> dict[str, Any]:
    journal = _load_journal(config, refresh_batch_id)
    steps = journal.get("steps") or {}
    partial = bool(
        (bool(steps.get("seed_commit_started")) and not bool(steps.get("seed_commit_complete")))
        or (
            bool(steps.get("database_rebuild_started"))
            and not bool(steps.get("database_rebuild_complete"))
        )
    )
    return {
        "recovery_required": partial,
        "refresh_batch_id": refresh_batch_id,
        "steps": steps,
    }


def recover_snapshot(config, refresh_batch_id: str, reviewer: str = "snapshot_operator") -> dict[str, Any]:
    """Deterministic recovery for a partial snapshot refresh: restore the pre-refresh
    backup (facts published by the publication transaction are preserved because the
    backup was taken after that transaction completed)."""
    gate = detect_snapshot_recovery_required(config, refresh_batch_id)
    if not gate["recovery_required"]:
        return {"automatic_action": "noop", "reason": "no partial snapshot refresh"}
    result = rollback_snapshot(config, refresh_batch_id, reviewer)
    result["automatic_action"] = "rollback"
    result["detected_steps"] = gate["steps"]
    return result
