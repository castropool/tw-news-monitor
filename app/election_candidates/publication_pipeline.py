"""Publication prepare/commit/rollback with lock, journal and atomic replace."""

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
from .formal_diff import diff_links, diff_rows, write_formal_diff
from .publication_audit import write_publication_audit_md
from .publication_preview import (
    formal_seed_business_hash,
    read_seed_events,
    read_seed_sources,
)
from .publication_validator import validate_batch
from app.election_context.formal_state_hash import (
    formal_state_business_hash_from_db,
    formal_state_business_hash_from_seed_dir,
)


JOURNAL_STEPS = [
    "prepared",
    "backup_complete",
    "seed_commit_started",
    "seed_commit_complete",
    "database_commit_started",
    "database_commit_complete",
    "post_validation_complete",
    "committed",
]


class PublicationFault(RuntimeError):
    pass


class PublicationLockError(RuntimeError):
    pass


def _atomic_write(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _hash_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _db_business_hash(path: Path) -> str:
    if not path.exists():
        return ""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    h = hashlib.sha256()
    try:
        tables = ["elections", "actors", "sources", "election_events", "event_sources",
                  "election_polls", "poll_questions", "poll_results", "poll_source_links",
                  "election_state_snapshots"]
        for t in tables:
            try:
                rows = conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
            except sqlite3.OperationalError:
                continue
            h.update(t.encode("utf-8"))
            h.update(json.dumps([list(r) for r in rows], ensure_ascii=False, sort_keys=True).encode("utf-8"))
    finally:
        conn.close()
    return h.hexdigest()


def batch_hash(preview: dict[str, Any]) -> str:
    raw = json.dumps(
        {
            "batch_id": preview["batch_id"],
            "review_decision_ids": preview["review_decision_ids"],
            "new_events": preview["new_events"],
            "new_sources": preview["new_sources"],
            "new_links": preview["new_links"],
            "attachments": preview.get("attachments", []),
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _lock_path(config, election_id: str) -> Path:
    return config.path("output_root").parent.parent / "data" / "locks" / f"formal_publication_{election_id}.lock"


class PublicationLock:
    def __init__(self, config, election_id: str, reviewer: str, batch_id: str, operation: str):
        self.path = _lock_path(config, election_id)
        self.meta = {
            "pid": os.getpid(),
            "batch_id": batch_id,
            "reviewer": reviewer,
            "started_at": datetime.now(TAIPEI).isoformat(),
            "operation": operation,
        }
        self.acquired = False

    def acquire(self):
        if self.path.exists():
            raise PublicationLockError(f"publication lock exists: {self.path}")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.meta, ensure_ascii=False, indent=2), encoding="utf-8")
        self.acquired = True

    def release(self):
        if self.acquired and self.path.exists():
            self.path.unlink()
            self.acquired = False

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *args):
        self.release()


def _load_journal(batch_dir: Path) -> dict[str, Any]:
    path = batch_dir / "publication_commit_journal.json"
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"batch_id": batch_dir.name, "steps": {}, "recovery_required": False}


def _write_journal(batch_dir: Path, journal: dict[str, Any], faults: dict[str, bool] | None = None):
    if faults and faults.get("fail_journal_write"):
        raise PublicationFault("injected: journal write failure")
    _atomic_write(batch_dir / "publication_commit_journal.json", json.dumps(journal, ensure_ascii=False, indent=2).encode("utf-8"))


def create_backup(config, batch_id: str, preview: dict[str, Any]) -> Path:
    backup_dir = config.path("output_root").parent.parent / "data" / "backups" / f"formal_publication_{batch_id}"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    backup_dir.mkdir(parents=True, exist_ok=True)
    files = [
        config.path("events_seed"),
        config.path("sources_seed"),
        config.path("initial_snapshot"),
        config.path("snapshot_history"),
        config.path("formal_db"),
    ]
    seed_dir = config.path("events_seed").parent
    for p in seed_dir.iterdir():
        if p.is_file() and not p.name.endswith(".bak") and p.name not in {
            f.name for f in files if f.exists()
        }:
            files.append(p)
    sums = {}
    for src in files:
        if src.exists():
            dst = backup_dir / src.name
            shutil.copy2(src, dst)
            sums[src.name] = _hash_file(dst)
    manifest = {
        "batch_id": batch_id,
        "created_at": datetime.now(TAIPEI).isoformat(),
        "formal_data_hash_before": preview.get("formal_data_hash_before"),
        "files": sums,
        "excluded": [".env", "API keys", "Webhook"],
    }
    (backup_dir / "backup_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    sums["backup_manifest.json"] = _hash_file(backup_dir / "backup_manifest.json")
    (backup_dir / "SHA256SUMS").write_text(
        "\n".join(f"{v}  {k}" for k, v in sorted(sums.items())) + "\n",
        encoding="utf-8",
    )
    return backup_dir


def prepare_batch(
    repo: CandidateRepository,
    config,
    election_id: str,
    batch_id: str,
    preview: dict[str, Any],
    reviewer: str,
    faults: dict[str, bool] | None = None,
) -> dict[str, Any]:
    from .publication_recovery import assert_recovery_gate
    assert_recovery_gate(config)
    validation = validate_batch(repo, config, election_id, batch_id, preview)
    if not validation["publication_ready"]:
        raise ValueError(f"publication validation failed: {validation['errors']}")
    batch_dir = config.path("output_root") / "publication_batches" / batch_id
    batch_dir.mkdir(parents=True, exist_ok=True)
    (batch_dir / "validation.json").write_text(
        json.dumps(validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if faults and faults.get("fail_backup"):
        raise PublicationFault("injected: backup failure")
    backup_dir = create_backup(config, batch_id, preview)
    (batch_dir / "rollback_plan.json").write_text(
        json.dumps(
            {
                "backup_dir": str(backup_dir),
                "restore_files": ["events.jsonl", "sources.jsonl", "election_context.db"],
                "steps": ["acquire lock", "verify backup hash", "restore seeds", "restore db", "post-validate", "audit"],
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    staging_dir = config.path("output_root") / "publication_staging" / batch_id
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)
    for name in (
        "events.jsonl", "sources.jsonl", "initial_snapshot.json", "snapshot_history.jsonl",
        "election.json", "actors.yaml", "taxonomy.yaml",
        "polls.jsonl", "poll_questions.jsonl", "poll_results.jsonl",
        "poll_sources.jsonl", "poll_source_links.jsonl",
    ):
        src = config.path("events_seed").parent / name
        if src.exists():
            shutil.copy2(src, staging_dir / name)

    staging_events = read_seed_events(config) + preview.get("new_events", [])
    staging_sources = read_seed_sources(config) + preview.get("new_sources", [])
    for att in preview.get("attachments", []):
        event_id = att.get("event_id", "")
        evt = next((e for e in staging_events if e.get("event_id") == event_id), None)
        if evt is None:
            raise PublicationFault(f"attach target event missing in staging: {event_id}")
        source_id = att.get("source_id", "")
        if not any(s.get("source_id") == source_id for s in evt.get("sources", [])):
            evt.setdefault("sources", []).append(att.get("source") or {"source_id": source_id})
    _atomic_write(
        staging_dir / "events.jsonl",
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in staging_events).encode("utf-8"),
    )
    _atomic_write(
        staging_dir / "sources.jsonl",
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in staging_sources).encode("utf-8"),
    )
    if faults and faults.get("fail_staging_bootstrap"):
        raise PublicationFault("injected: staging bootstrap failure")
    staging_db = staging_dir / "election_context.db"
    from app.election_context.bootstrap_v2 import run_bootstrap_v2

    ok, stats = run_bootstrap_v2(staging_dir, staging_db, reset=True)
    if not ok:
        raise PublicationFault(f"staging bootstrap failed: {stats}")
    staging_hash = _db_business_hash(staging_db)
    staging_formal_hash = formal_state_business_hash_from_seed_dir(staging_dir)
    rebuilt_formal_hash = formal_state_business_hash_from_db(staging_db)
    if staging_formal_hash != rebuilt_formal_hash:
        raise PublicationFault("staging formal state hash mismatch")
    expected_count = len(staging_events)
    conn = sqlite3.connect(f"file:{staging_db}?mode=ro", uri=True)
    actual_count = conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0]
    conn.close()
    if actual_count != expected_count:
        raise PublicationFault(f"staging event count mismatch: {actual_count} != {expected_count}")

    journal = _load_journal(batch_dir)
    journal.update(
        {
            "steps": {
                "prepared": True,
                "backup_complete": True,
                "staging_ready": True,
            },
            "staging_dir": str(staging_dir),
            "staging_db_hash": staging_hash,
            "recovery_required": False,
        }
    )
    _write_journal(batch_dir, journal, faults)

    batch = repo.get_publication_batch(batch_id)
    batch["status"] = "staged"
    batch["backup_ready"] = 1
    batch["staging_ready"] = 1
    batch["validation_ready"] = 1
    repo.upsert_publication_batch(batch)
    return {"prepare_ready": True, "staging_dir": str(staging_dir), "validation": validation}


def commit_batch(
    repo: CandidateRepository,
    config,
    election_id: str,
    batch_id: str,
    reviewer: str,
    expected_batch_hash: str,
    preview: dict[str, Any],
    faults: dict[str, bool] | None = None,
) -> dict[str, Any]:
    from .publication_recovery import assert_recovery_gate
    assert_recovery_gate(config)
    actual_hash = batch_hash(preview)
    if actual_hash != expected_batch_hash:
        raise ValueError("expected batch hash mismatch; aborting commit")
    batch = repo.get_publication_batch(batch_id)
    if not batch or batch.get("status") not in ("staged", "publication_prepared"):
        raise ValueError(f"batch not prepared for commit: {batch_id}")
    if batch.get("commit_completed"):
        raise ValueError("duplicate commit rejected")
    batch_dir = config.path("output_root") / "publication_batches" / batch_id
    staging_dir = config.path("output_root") / "publication_staging" / batch_id
    journal = _load_journal(batch_dir)
    if journal.get("recovery_required"):
        raise ValueError("incomplete journal: recovery required")
    if journal["steps"].get("committed"):
        raise ValueError("duplicate commit rejected (journal)")

    with PublicationLock(config, election_id, reviewer, batch_id, "commit") as lock:
        batch["status"] = "committing"
        repo.upsert_publication_batch(batch)
        journal["steps"]["prepared"] = True
        _write_journal(batch_dir, journal, faults)

        journal["steps"]["seed_commit_started"] = True
        _write_journal(batch_dir, journal, faults)
        if faults and faults.get("fail_before_seed_replace"):
            raise PublicationFault("injected: fail before seed replace")
        for name in ("events.jsonl", "sources.jsonl"):
            src = staging_dir / name
            dst = config.path("events_seed").parent / name
            _atomic_write(dst, src.read_bytes())
        journal["steps"]["seed_commit_complete"] = True
        _write_journal(batch_dir, journal, faults)

        journal["steps"]["database_commit_started"] = True
        _write_journal(batch_dir, journal, faults)
        if faults and faults.get("fail_during_db_replace"):
            raise PublicationFault("injected: fail during db replace")
        staging_db = staging_dir / "election_context.db"
        _atomic_write(config.path("formal_db"), staging_db.read_bytes())
        journal["steps"]["database_commit_complete"] = True
        _write_journal(batch_dir, journal, faults)

        post = _post_commit_validation(config, election_id, preview)
        if faults and faults.get("fail_post_validation"):
            raise PublicationFault("injected: post validation failure")
        if not post["post_commit_ready"]:
            raise PublicationFault(f"post validation failed: {post['errors']}")
        journal["steps"]["post_validation_complete"] = True
        journal["steps"]["committed"] = True
        journal["recovery_required"] = False
        _write_journal(batch_dir, journal, faults)

        now = datetime.now(TAIPEI).isoformat()
        batch["status"] = "committed"
        batch["commit_ready"] = 1
        batch["commit_completed"] = 1
        batch["committed_at"] = now
        repo.upsert_publication_batch(batch)

        after_hash = formal_seed_business_hash(config)
        formal_after = formal_state_business_hash_from_db(config.path("formal_db"))
        from .review_completion import facts_cutoff_for_refresh

        facts_cutoff = facts_cutoff_for_refresh(repo, config, batch.get("election_id", election_id))
        _write_diff_and_audit(
            repo, config, batch, preview, batch_dir, after_hash, reviewer, now,
            facts_cutoff=facts_cutoff,
        )
        return {
            "commit_ready": True,
            "batch_id": batch_id,
            "formal_hash_after": after_hash,
            "formal_state_hash_after": formal_after,
            "post_commit_validation": post,
        }


def _post_commit_validation(config, election_id: str, preview: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    db = config.path("formal_db")
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    try:
        for evt in preview.get("new_events", []):
            if not conn.execute(
                "SELECT 1 FROM election_events WHERE event_id=?", (evt["event_id"],)
            ).fetchone():
                errors.append(f"event_missing:{evt['event_id']}")
        for src in preview.get("new_sources", []):
            if not conn.execute(
                "SELECT 1 FROM sources WHERE source_id=?", (src["source_id"],)
            ).fetchone():
                errors.append(f"source_missing:{src['source_id']}")
        for link in preview.get("new_links", []):
            if not conn.execute(
                "SELECT 1 FROM event_sources WHERE event_id=? AND source_id=?",
                (link["event_id"], link["source_id"]),
            ).fetchone():
                errors.append(f"link_missing:{link['event_id']}|{link['source_id']}")
        duplicate_events = conn.execute(
            "SELECT event_id FROM election_events GROUP BY event_id HAVING COUNT(*)>1"
        ).fetchall()
        if duplicate_events:
            errors.append("event_id_duplicate")
    finally:
        conn.close()
    return {"post_commit_ready": not errors, "errors": errors, "warnings": []}


def _write_diff_and_audit(
    repo, config, batch, preview, batch_dir: Path, after_hash: str, reviewer: str, now: str,
    facts_cutoff: str | None = None,
):
    before_events = read_seed_events(config)
    # Note: seeds already replaced; use preview to derive after
    after_events = before_events + preview.get("new_events", [])
    before_sources = read_seed_sources(config)
    after_sources = before_sources + preview.get("new_sources", [])
    before_links = [
        {"event_id": e["event_id"], "source_id": s["source_id"]}
        for e in before_events for s in e.get("sources", [])
    ]
    after_links = before_links + preview.get("new_links", [])
    diff = {
        "events_diff": diff_rows(before_events, after_events, "event_id"),
        "sources_diff": diff_rows(before_sources, after_sources, "source_id"),
        "links_diff": diff_links(before_links, after_links),
        "snapshot_changed": False,
        "coverage_changed": False,
        "poll_changed": False,
    }
    write_formal_diff(diff, diff, batch_dir / "formal_diff_before_after.json")

    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    active = conn.execute(
        "SELECT snapshot_id, state_json FROM election_state_snapshots "
        "WHERE snapshot_status='active' ORDER BY as_of DESC LIMIT 1"
    ).fetchone()
    conn.close()
    state = {}
    prev_coverage = {}
    prev_snapshot_id = ""
    if active:
        prev_snapshot_id = active["snapshot_id"]
        state = json.loads(active["state_json"]) if isinstance(active["state_json"], str) else active["state_json"]
        prev_coverage = state.get("coverage", {}) or {}
    downstream = {
        "election_id": config.canonical_election_id,
        "publication_batch_id": batch["batch_id"],
        "formal_state_hash": formal_state_business_hash_from_db(config.path("formal_db")),
        "facts_cutoff": facts_cutoff,
        "requested_period_start": prev_coverage.get("requested_period_start", "2025-08-01"),
        "requested_period_end": prev_coverage.get("requested_period_end", "2026-07-27"),
        "previous_coverage_version": prev_coverage.get("coverage_version", ""),
        "previous_snapshot_id": prev_snapshot_id,
        "known_gaps": prev_coverage.get("known_gaps", []),
        "new_event_ids": [e["event_id"] for e in preview.get("new_events", [])],
        "updated_event_ids": [],
        "new_source_ids": [s["source_id"] for s in preview.get("new_sources", [])],
        "snapshot_refresh_required": True,
        "coverage_refresh_required": True,
        "assessment_refresh_required": True,
        "requested_at": now,
    }
    (batch_dir / "downstream_refresh_request.json").write_text(
        json.dumps(downstream, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (batch_dir / "post_commit_validation.json").write_text(
        json.dumps({"post_commit_ready": True, "errors": []}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    decisions = [repo.get_review_decision(rid) for rid in preview.get("review_decision_ids", [])]
    write_publication_audit_md(
        batch,
        preview.get("items", []),
        [d for d in decisions if d],
        {
            "events_added": diff["events_diff"]["added"],
            "sources_added": diff["sources_diff"]["added"],
            "links_added": diff["links_diff"]["added"],
        },
        downstream,
        {"rollback_available": True, "backup_dir": str(config.path("output_root").parent.parent / "data" / "backups" / f"formal_publication_{batch['batch_id']}")},
        batch_dir / "publication_audit.md",
    )
    audit_raw = f"{batch['batch_id']}|{now}"
    repo.append_publication_audit(
        {
            "audit_id": "aud_" + hashlib.sha256(audit_raw.encode("utf-8")).hexdigest()[:16],
            "batch_id": batch["batch_id"],
            "candidate_id": "",
            "review_decision_id": "",
            "reviewer": reviewer,
            "action": "commit",
            "event_id": ",".join(e["event_id"] for e in preview.get("new_events", [])),
            "source_ids": [s["source_id"] for s in preview.get("new_sources", [])],
            "timestamp": now,
            "formal_hash_before": batch.get("formal_data_hash_before"),
            "formal_hash_after": after_hash,
            "result": "success",
            "reason": "committed",
        }
    )


def rollback_batch(
    repo: CandidateRepository,
    config,
    election_id: str,
    batch_id: str,
    reviewer: str,
    faults: dict[str, bool] | None = None,
) -> dict[str, Any]:
    from .publication_recovery import assert_recovery_gate
    assert_recovery_gate(config)
    batch = repo.get_publication_batch(batch_id)
    if not batch or batch.get("status") not in ("committed", "failed_commit"):
        raise ValueError(f"rollback only allowed for committed/failed_commit: {batch_id}")
    backup_dir = config.path("output_root").parent.parent / "data" / "backups" / f"formal_publication_{batch_id}"
    if not (backup_dir / "backup_manifest.json").exists():
        raise ValueError("backup missing")
    manifest = json.loads((backup_dir / "backup_manifest.json").read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if (backup_dir / name).exists() and _hash_file(backup_dir / name) != expected:
            raise ValueError(f"backup hash mismatch: {name}")

    with PublicationLock(config, election_id, reviewer, batch_id, "rollback"):
        if faults and faults.get("fail_rollback"):
            raise PublicationFault("injected: rollback failure")
        for name in ("events.jsonl", "sources.jsonl"):
            src = backup_dir / name
            if src.exists():
                _atomic_write(config.path("events_seed").parent / name, src.read_bytes())
        db_backup = backup_dir / "election_context.db"
        if db_backup.exists():
            _atomic_write(config.path("formal_db"), db_backup.read_bytes())
        after_hash = formal_seed_business_hash(config)
        formal_after = formal_state_business_hash_from_db(config.path("formal_db"))
        if after_hash != manifest["formal_data_hash_before"]:
            raise PublicationFault("rollback hash restoration failed")
        now = datetime.now(TAIPEI).isoformat()
        batch["status"] = "rolled_back"
        batch["rolled_back_at"] = now
        repo.upsert_publication_batch(batch)
        repo.append_publication_audit(
            {
                "audit_id": f"aud_{hashlib.sha256(f'{batch_id}|rollback|{now}'.encode()).hexdigest()[:16]}",
                "batch_id": batch_id,
                "candidate_id": "",
                "review_decision_id": "",
                "reviewer": reviewer,
                "action": "rollback",
                "event_id": "",
                "source_ids": [],
                "timestamp": now,
                "formal_hash_before": manifest["formal_data_hash_before"],
                "formal_hash_after": after_hash,
                "result": "success",
                "reason": "rollback completed",
            }
        )
        return {
            "rolled_back": True,
            "batch_id": batch_id,
            "formal_data_hash_after_rollback": after_hash,
            "formal_state_hash_after_rollback": formal_after,
            "formal_data_hash_before": manifest["formal_data_hash_before"],
        }


def detect_recovery_required(config, batch_id: str) -> dict[str, Any]:
    batch_dir = config.path("output_root") / "publication_batches" / batch_id
    journal = _load_journal(batch_dir)
    steps = journal.get("steps") or {}
    incomplete = bool(
        (bool(steps.get("seed_commit_started")) and not bool(steps.get("seed_commit_complete")))
        or (bool(steps.get("database_commit_started")) and not bool(steps.get("database_commit_complete")))
    )
    return {"recovery_required": incomplete or journal.get("recovery_required", False), "journal": journal}
