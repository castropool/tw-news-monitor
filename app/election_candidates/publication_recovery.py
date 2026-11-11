"""Automatic publication recovery executor (deterministic, idempotent)."""

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

from .candidate_repository import CandidateRepository
from .publication_pipeline import PublicationLock, _atomic_write


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _load_journal(batch_dir: Path) -> dict[str, Any]:
    p = batch_dir / "publication_commit_journal.json"
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"corrupt": True}


def recovery_gate(config) -> dict[str, Any]:
    pub_root = config.path("output_root") / "publication_batches"
    unfinished = []
    if pub_root.exists():
        for d in pub_root.iterdir():
            j = _load_journal(d)
            steps = j.get("steps") or {}
            if bool(steps.get("seed_commit_started")) and not bool(steps.get("seed_commit_complete")):
                unfinished.append(d.name)
            elif bool(steps.get("database_commit_started")) and not bool(steps.get("database_commit_complete")):
                unfinished.append(d.name)
    rec_root = config.path("output_root") / "publication_recovery_journals"
    unfinished_rec = []
    if rec_root.exists():
        for f in rec_root.glob("*.json"):
            try:
                j = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                unfinished_rec.append(f.stem)
                continue
            if j.get("result") != "success":
                unfinished_rec.append(f.stem)
    return {
        "recovery_required": bool(unfinished) or bool(unfinished_rec),
        "unfinished_publication_batches": unfinished,
        "unfinished_recovery_journals": unfinished_rec,
    }


def assert_recovery_gate(config):
    gate = recovery_gate(config)
    if gate["recovery_required"]:
        raise ValueError(
            "RECOVERY_REQUIRED batch_id="
            + ",".join(gate["unfinished_publication_batches"] + gate["unfinished_recovery_journals"])
        )


def detect_state(config, batch_id: str) -> dict[str, Any]:
    batch_dir = config.path("output_root") / "publication_batches" / batch_id
    journal = _load_journal(batch_dir)
    steps = journal.get("steps") or {}
    batch_status = ""
    try:
        conn = sqlite3.connect(f"file:{config.path('candidate_db')}?mode=ro", uri=True)
        row = conn.execute(
            "SELECT status FROM publication_batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        if row:
            batch_status = row[0]
        conn.close()
    except sqlite3.OperationalError:
        pass
    if not journal or journal.get("corrupt"):
        state = "UNKNOWN_STATE" if journal.get("corrupt") else "NO_COMMIT_STARTED"
    elif not journal.get("steps"):
        state = "NO_COMMIT_STARTED"
    elif steps.get("seed_commit_started") and not steps.get("seed_commit_complete"):
        state = "SEED_COMMIT_PARTIAL"
    elif steps.get("seed_commit_complete") and not steps.get("database_commit_started"):
        state = "SEED_COMMIT_COMPLETE_DB_NOT_STARTED"
    elif steps.get("database_commit_started") and not steps.get("database_commit_complete"):
        state = "DB_COMMIT_PARTIAL"
    elif steps.get("database_commit_complete") and not steps.get("post_validation_complete"):
        state = "DB_COMMIT_COMPLETE_POST_VALIDATION_PENDING"
    elif steps.get("post_validation_complete") and not steps.get("committed"):
        state = "POST_VALIDATION_FAILED"
    elif steps.get("committed"):
        state = "ROLLBACK_PARTIAL" if batch_status == "rolled_back" else "COMMIT_COMPLETE"
    else:
        state = "UNKNOWN_STATE"
    return {
        "batch_id": batch_id,
        "detected_state": state,
        "journal": journal,
        "backup_hash": _backup_manifest_hash(config, batch_id),
        "staging_db_hash": journal.get("staging_db_hash", ""),
        "batch_status": batch_status,
    }


def _backup_manifest_hash(config, batch_id: str) -> str:
    backup = config.path("output_root").parent.parent / "data" / "backups" / f"formal_publication_{batch_id}"
    manifest = backup / "backup_manifest.json"
    return _sha(manifest) if manifest.exists() else ""


def _restore_backup(config, batch_id: str):
    backup = config.path("output_root").parent.parent / "data" / "backups" / f"formal_publication_{batch_id}"
    manifest_path = backup / "backup_manifest.json"
    if not manifest_path.exists():
        raise ValueError("backup missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, expected in manifest.get("files", {}).items():
        if (backup / name).exists() and _sha(backup / name) != expected:
            raise ValueError(f"backup hash mismatch: {name}")
    seed_dir = config.path("events_seed").parent
    for name in ("events.jsonl", "sources.jsonl"):
        src = backup / name
        if src.exists():
            _atomic_write(seed_dir / name, src.read_bytes())
    db_backup = backup / "election_context.db"
    if db_backup.exists():
        _atomic_write(config.path("formal_db"), db_backup.read_bytes())


def _recovery_journal_path(config, batch_id: str) -> Path:
    root = config.path("output_root") / "publication_recovery_journals"
    root.mkdir(parents=True, exist_ok=True)
    return root / f"{batch_id}.json"


def _append_recovery_journal(config, batch_id: str, record: dict[str, Any]):
    path = _recovery_journal_path(config, batch_id)
    entries = []
    if path.exists():
        entries = json.loads(path.read_text(encoding="utf-8"))
    entries.append(record)
    _atomic_write(path, json.dumps(entries, ensure_ascii=False, indent=2).encode("utf-8"))


def recover(
    repo: CandidateRepository,
    config,
    election_id: str,
    batch_id: str,
    reviewer: str,
    mode: str = "auto",
    faults: dict[str, bool] | None = None,
) -> dict[str, Any]:
    if mode not in ("auto", "inspect", "rollback", "resume"):
        raise ValueError(f"invalid mode: {mode}")
    state_info = detect_state(config, batch_id)
    state = state_info["detected_state"]
    started = datetime.now(TAIPEI).isoformat()

    if mode == "inspect":
        return {**state_info, "automatic_action": "inspect_only"}

    if state == "COMMIT_COMPLETE":
        return {"automatic_action": "noop", "detected_state": state, "reason": "already committed"}
    if state == "NO_COMMIT_STARTED":
        batch = repo.get_publication_batch(batch_id)
        if batch:
            batch["status"] = "failed"
            repo.upsert_publication_batch(batch)
        _append_recovery_journal(
            config, batch_id,
            {"recovery_id": f"rec_{hashlib.sha256(f'{batch_id}|{started}'.encode()).hexdigest()[:12]}",
             "batch_id": batch_id, "detected_state": state, "decision": "mark_failed",
             "decision_reasons": ["no commit started"], "formal_hash_before": "",
             "backup_hash": state_info["backup_hash"], "staging_hash": state_info["staging_db_hash"],
             "actions": ["mark_failed"], "formal_hash_after": "", "result": "success",
             "started_at": started, "finished_at": datetime.now(TAIPEI).isoformat()},
        )
        return {"automatic_action": "mark_failed", "detected_state": state}

    if state in ("SEED_COMMIT_PARTIAL", "DB_COMMIT_PARTIAL"):
        if mode == "resume":
            raise ValueError("resume is not allowed for partial commit states")
        return _do_rollback(repo, config, election_id, batch_id, reviewer, state, started, faults)

    if state == "SEED_COMMIT_COMPLETE_DB_NOT_STARTED":
        if mode == "resume":
            return _do_resume(repo, config, election_id, batch_id, reviewer, state, started, faults)
        return _do_rollback(repo, config, election_id, batch_id, reviewer, state, started, faults)

    if state == "DB_COMMIT_COMPLETE_POST_VALIDATION_PENDING":
        if mode == "resume":
            return _do_resume(repo, config, election_id, batch_id, reviewer, state, started, faults)
        return _do_rollback(repo, config, election_id, batch_id, reviewer, state, started, faults)

    if state == "POST_VALIDATION_FAILED":
        return _do_rollback(repo, config, election_id, batch_id, reviewer, state, started, faults)

    # UNKNOWN_STATE or rollback partial
    if state == "UNKNOWN_STATE" and mode == "auto":
        _append_recovery_journal(
            config, batch_id,
            {"recovery_id": "rec_unknown", "batch_id": batch_id, "detected_state": state,
             "decision": "blocked", "decision_reasons": ["unknown state"],
             "formal_hash_before": "", "backup_hash": state_info["backup_hash"],
             "staging_hash": state_info["staging_db_hash"], "actions": [],
             "formal_hash_after": "", "result": "blocked",
             "started_at": started, "finished_at": datetime.now(TAIPEI).isoformat()},
        )
        return {"automatic_action": "blocked", "manual_intervention_required": True,
                "detected_state": state, "reason": "unknown state blocks automation"}
    if state == "ROLLBACK_PARTIAL":
        return _do_rollback(repo, config, election_id, batch_id, reviewer, state, started, faults)
    raise ValueError(f"no recovery path for state {state} mode {mode}")


def _do_rollback(repo, config, election_id, batch_id, reviewer, state, started, faults):
    if faults and faults.get("fail_recovery_rollback"):
        raise RuntimeError("injected: recovery rollback failure")
    with PublicationLock(config, election_id, reviewer, batch_id, "recovery_rollback"):
        _restore_backup(config, batch_id)
        batch = repo.get_publication_batch(batch_id)
        if batch:
            batch["status"] = "rolled_back"
            batch["rolled_back_at"] = datetime.now(TAIPEI).isoformat()
            repo.upsert_publication_batch(batch)
        record = {
            "recovery_id": f"rec_{hashlib.sha256(f'{batch_id}|rollback|{started}'.encode()).hexdigest()[:12]}",
            "batch_id": batch_id, "detected_state": state, "decision": "rollback",
            "decision_reasons": ["state requires rollback"],
            "formal_hash_before": "", "backup_hash": _backup_manifest_hash(config, batch_id),
            "staging_hash": "", "actions": ["restore_backup", "mark_rolled_back"],
            "formal_hash_after": "", "result": "success",
            "started_at": started, "finished_at": datetime.now(TAIPEI).isoformat(),
        }
        _append_recovery_journal(config, batch_id, record)
        return {"automatic_action": "rollback", "detected_state": state, "result": "success"}


def _do_resume(repo, config, election_id, batch_id, reviewer, state, started, faults):
    if faults and faults.get("fail_recovery_resume"):
        raise RuntimeError("injected: recovery resume failure")
    batch_dir = config.path("output_root") / "publication_batches" / batch_id
    staging_dir = config.path("output_root") / "publication_staging" / batch_id
    staging_db = staging_dir / "election_context.db"
    if not staging_db.exists():
        raise ValueError("staging db missing")
    journal = _load_journal(batch_dir)
    expected_staging_hash = journal.get("staging_db_hash", "")
    if expected_staging_hash:
        import hashlib
        actual = hashlib.sha256(staging_db.read_bytes()).hexdigest()
        if actual != expected_staging_hash:
            raise ValueError("staging hash mismatch; resume blocked")
    with PublicationLock(config, election_id, reviewer, batch_id, "recovery_resume"):
        _atomic_write(config.path("formal_db"), staging_db.read_bytes())
        batch = repo.get_publication_batch(batch_id)
        if batch:
            batch["status"] = "committed"
            batch["commit_completed"] = 1
            batch["committed_at"] = datetime.now(TAIPEI).isoformat()
            repo.upsert_publication_batch(batch)
        steps = journal.setdefault("steps", {})
        steps["database_commit_complete"] = True
        steps["post_validation_complete"] = True
        steps["committed"] = True
        _atomic_write(batch_dir / "publication_commit_journal.json",
                      json.dumps(journal, ensure_ascii=False, indent=2).encode("utf-8"))
        record = {
            "recovery_id": f"rec_{hashlib.sha256(f'{batch_id}|resume|{started}'.encode()).hexdigest()[:12]}",
            "batch_id": batch_id, "detected_state": state, "decision": "resume",
            "decision_reasons": ["safe resume verified"],
            "formal_hash_before": "", "backup_hash": _backup_manifest_hash(config, batch_id),
            "staging_hash": "", "actions": ["replace_db", "mark_committed"],
            "formal_hash_after": "", "result": "success",
            "started_at": started, "finished_at": datetime.now(TAIPEI).isoformat(),
        }
        _append_recovery_journal(config, batch_id, record)
        return {"automatic_action": "resume", "detected_state": state, "result": "success"}
