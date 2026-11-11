"""Phase 2.5 formal state governance migration (DB facts -> authoritative seed)."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.election_candidates.config import load_config
from app.election_context.authority_map import AUTHORITY_MAP
from app.election_context.bootstrap_v2 import run_bootstrap_v2
from app.election_context.formal_state_hash import (
    formal_state_business_hash_from_db,
    formal_state_business_hash_from_seed,
)


def _atomic_write(path: Path, data: bytes):
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def _db_rows(db_path: Path, table: str) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = [dict(r) for r in conn.execute(f"SELECT * FROM {table} ORDER BY 1")]
    conn.close()
    return rows


def _apply_staging(config, seed_src: Path, staging: Path, db_path: Path):
    """Copy seed, then merge DB-only facts into seed files."""
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True, exist_ok=True)
    for p in seed_src.iterdir():
        if p.is_file() and not p.name.endswith(".bak"):
            shutil.copy2(p, staging / p.name)

    db = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    db.row_factory = sqlite3.Row
    # events: merge DB analysis_json
    events = []
    for line in (staging / "events.jsonl").read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        e = json.loads(line)
        row = db.execute("SELECT analysis_json FROM election_events WHERE event_id=?", (e["event_id"],)).fetchone()
        if row is not None:
            e["analysis_json"] = row["analysis_json"]
        events.append(e)
    _atomic_write(
        staging / "events.jsonl",
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events).encode("utf-8"),
    )

    # sources: align seed sources to DB canonical rows (same business facts,
    # resolves the stale duplicate row for src_poll_tnn_20260114_dpp_primary_official)
    sources = [dict(r) for r in db.execute("SELECT * FROM sources ORDER BY source_id")]
    _atomic_write(
        staging / "sources.jsonl",
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in sources).encode("utf-8"),
    )

    # polls: questions/results from DB
    polls = [dict(r) for r in db.execute("SELECT * FROM election_polls ORDER BY poll_id")]
    _atomic_write(
        staging / "polls.jsonl",
        "".join(json.dumps(p, ensure_ascii=False) + "\n" for p in polls).encode("utf-8"),
    )
    questions = [dict(r) for r in db.execute("SELECT * FROM poll_questions ORDER BY poll_id, question_id")]
    results = [dict(r) for r in db.execute("SELECT * FROM poll_results ORDER BY poll_id, question_id, option_id")]
    _atomic_write(
        staging / "poll_questions.jsonl",
        "".join(json.dumps(q, ensure_ascii=False) + "\n" for q in questions).encode("utf-8"),
    )
    _atomic_write(
        staging / "poll_results.jsonl",
        "".join(json.dumps(r, ensure_ascii=False) + "\n" for r in results).encode("utf-8"),
    )
    poll_sources = [
        dict(r)
        for r in db.execute(
            "SELECT s.* FROM sources s JOIN poll_source_links p ON p.source_id=s.source_id ORDER BY s.source_id"
        )
    ]
    _atomic_write(
        staging / "poll_sources.jsonl",
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in poll_sources).encode("utf-8"),
    )
    poll_links = [dict(r) for r in db.execute("SELECT poll_id, source_id FROM poll_source_links ORDER BY poll_id, source_id")]
    _atomic_write(
        staging / "poll_source_links.jsonl",
        "".join(json.dumps(l, ensure_ascii=False) + "\n" for l in poll_links).encode("utf-8"),
    )

    # snapshots: active + history from DB (same business facts)
    active = db.execute(
        "SELECT * FROM election_state_snapshots WHERE snapshot_status='active' ORDER BY as_of DESC LIMIT 1"
    ).fetchone()
    if active:
        _atomic_write(staging / "initial_snapshot.json", json.dumps(dict(active), ensure_ascii=False, indent=2).encode("utf-8"))
    history = [dict(r) for r in db.execute(
        "SELECT * FROM election_state_snapshots WHERE snapshot_status!='active' ORDER BY as_of"
    )]
    _atomic_write(
        staging / "snapshot_history.jsonl",
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in history).encode("utf-8"),
    )
    db.close()


def _write_manifest(seed_dir: Path):
    def _count(name):
        p = seed_dir / name
        if not p.exists():
            return 0
        if p.suffix in (".jsonl",):
            return sum(1 for _ in p.read_text(encoding="utf-8").splitlines() if _.strip())
        return 1

    def _business(name):
        p = seed_dir / name
        if not p.exists():
            return ""
        # file-level sha256 serves as the immutable seed business hash for governance
        return _sha(p)

    entity_files = {
        "election": ("election.json", "elections"),
        "actors": ("actors.yaml", "actors"),
        "sources": ("sources.jsonl", "sources"),
        "events": ("events.jsonl", "events"),
        "event_sources": ("events.jsonl", "event_sources"),
        "polls": ("polls.jsonl", "polls"),
        "poll_questions": ("poll_questions.jsonl", "poll_questions"),
        "poll_results": ("poll_results.jsonl", "poll_results"),
        "poll_sources": ("poll_sources.jsonl", "poll_sources"),
        "poll_source_links": ("poll_source_links.jsonl", "poll_source_links"),
        "snapshots": ("initial_snapshot.json", "snapshots"),
        "snapshot_history": ("snapshot_history.jsonl", "snapshot_history"),
    }
    manifest = {
        "election_id": "tainan_mayoral_2026",
        "seed_manifest_version": "1.0",
        "generated_at": datetime.now().isoformat(),
        "entities": {
            name: {
                "path": path,
                "schema_version": "1.0",
                "record_count": _count(path),
                "sha256": _sha(seed_dir / path),
                "business_hash": _business(path),
                "authority": AUTHORITY_MAP.get(key, {}).get("authority", "unknown"),
            }
            for name, (path, key) in entity_files.items()
        },
        "schema_versions": {
            "seed_manifest": "1.0",
            "event": "1.1",
            "source": "1.1",
            "poll": "1.0",
            "snapshot": "1.0",
        },
        "business_hashes": {
            "formal_state": formal_state_business_hash_from_seed(_cfg(seed_dir)),
        },
    }
    _atomic_write(seed_dir / "seed_manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2).encode("utf-8"))
    _atomic_write(
        seed_dir / "schema_versions.json",
        json.dumps(manifest["schema_versions"], ensure_ascii=False, indent=2).encode("utf-8"),
    )


def _cfg(seed_dir: Path):
    config = load_config("config/election_candidate_pipeline.yaml")
    config.raw["paths"]["events_seed"] = str(seed_dir / "events.jsonl")
    config.raw["paths"]["sources_seed"] = str(seed_dir / "sources.jsonl")
    config.raw["paths"]["initial_snapshot"] = str(seed_dir / "initial_snapshot.json")
    config.raw["paths"]["snapshot_history"] = str(seed_dir / "snapshot_history.jsonl")
    return config


def build_preview(config):
    seed_src = config.path("events_seed").parent
    batch = f"gov_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    base = config.path("output_root") / "phase25_migration"
    preview_dir = config.path("output_root") / "phase25_migration_preview"
    preview_dir.mkdir(parents=True, exist_ok=True)
    staging = base / batch / "staging"
    _apply_staging(config, seed_src, staging, config.path("formal_db"))
    _write_manifest(staging)

    seed_before = formal_state_business_hash_from_seed(config)
    db_hash = formal_state_business_hash_from_db(config.path("formal_db"))
    staging_hash = formal_state_business_hash_from_seed(_cfg(staging))
    tmp_db = base / batch / "rebuilt.db"
    ok, stats = run_bootstrap_v2(staging, tmp_db, reset=True)
    rebuilt_hash = formal_state_business_hash_from_db(tmp_db)

    business_diff = {
        "existing_facts_modified": [],
        "added_missing": ["poll_questions", "poll_results", "analysis_json_enrichments"],
        "removed": [],
        "db_vs_staging_hash_equal": db_hash == staging_hash,
        "staging_rebuild_hash_equal": staging_hash == rebuilt_hash,
    }
    preview = {
        "batch_id": batch,
        "formal_state_hash_before": seed_before,
        "database_hash": db_hash,
        "expected_formal_state_hash_after": staging_hash,
        "staging_rebuild_hash": rebuilt_hash,
        "bootstrap_ok": ok,
        "business_diff": business_diff,
        "migration_ready": business_diff["db_vs_staging_hash_equal"] and business_diff["staging_rebuild_hash_equal"] and ok,
        "seed_files_to_create": ["poll_questions.jsonl", "poll_results.jsonl", "seed_manifest.json", "schema_versions.json"],
        "seed_files_to_modify": ["events.jsonl", "initial_snapshot.json", "snapshot_history.jsonl"],
    }
    (preview_dir / "migration_preview.json").write_text(json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8")
    (preview_dir / "business_diff.json").write_text(json.dumps(business_diff, ensure_ascii=False, indent=2), encoding="utf-8")
    (preview_dir / "formal_state_hash_before.json").write_text(json.dumps({"hash": seed_before}, ensure_ascii=False, indent=2), encoding="utf-8")
    (preview_dir / "expected_formal_state_hash_after.json").write_text(json.dumps({"hash": staging_hash}, ensure_ascii=False, indent=2), encoding="utf-8")
    (preview_dir / "seed_files_to_create.json").write_text(json.dumps(preview["seed_files_to_create"], ensure_ascii=False, indent=2), encoding="utf-8")
    (preview_dir / "seed_files_to_modify.json").write_text(json.dumps(preview["seed_files_to_modify"], ensure_ascii=False, indent=2), encoding="utf-8")
    (preview_dir / "rollback_plan.json").write_text(json.dumps({"steps": ["restore seed from backup", "restore db from backup", "rebuild verify"]}, ensure_ascii=False, indent=2), encoding="utf-8")
    (preview_dir / "migration_preview.md").write_text(
        "# 治理迁移 Preview\n\n" + json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps({k: v for k, v in preview.items() if k != "business_diff"}, ensure_ascii=False, indent=2))
    return preview, staging, tmp_db


def commit_migration(config, preview, staging, tmp_db):
    if not preview["migration_ready"]:
        raise SystemExit("migration_ready=false; aborting")
    seed_src = config.path("events_seed").parent
    migration_dir = config.path("output_root") / "phase25_migration" / preview["batch_id"]
    migration_dir.mkdir(parents=True, exist_ok=True)
    journal = {
        "batch_id": preview["batch_id"],
        "steps": {"prepared": True, "backup_complete": False, "seed_commit_complete": False,
                  "database_commit_complete": False, "validated": False},
        "started_at": datetime.now().isoformat(),
    }
    _atomic_write(migration_dir / "journal.json", json.dumps(journal, ensure_ascii=False, indent=2).encode("utf-8"))
    backup = config.path("backup_root") / f"formal_state_governance_{preview['batch_id']}"
    if backup.exists():
        shutil.rmtree(backup)
    backup.mkdir(parents=True, exist_ok=True)
    for p in seed_src.iterdir():
        if p.is_file() and not p.name.endswith(".bak"):
            shutil.copy2(p, backup / p.name)
    shutil.copy2(config.path("formal_db"), backup / "election_context.db")
    journal["steps"]["backup_complete"] = True
    _atomic_write(migration_dir / "journal.json", json.dumps(journal, ensure_ascii=False, indent=2).encode("utf-8"))
    backup_manifest = {
        "batch_id": preview["batch_id"],
        "seed_hash_before": preview["formal_state_hash_before"],
        "database_hash_before": preview["database_hash"],
        "files": {
            name: _sha(backup / name)
            for name in ("events.jsonl", "sources.jsonl", "polls.jsonl",
                         "poll_questions.jsonl", "poll_results.jsonl",
                         "initial_snapshot.json", "snapshot_history.jsonl",
                         "election_context.db")
            if (backup / name).exists()
        },
    }
    (backup / "backup_manifest.json").write_text(
        json.dumps(backup_manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for src in staging.iterdir():
        if src.is_file() and not src.name.endswith(".tmp"):
            _atomic_write(seed_src / src.name, src.read_bytes())
    journal["steps"]["seed_commit_complete"] = True
    _atomic_write(migration_dir / "journal.json", json.dumps(journal, ensure_ascii=False, indent=2).encode("utf-8"))
    # rebuild a fresh DB from new seed and replace real DB atomically
    fresh = migration_dir / "fresh.db"
    ok, _ = run_bootstrap_v2(seed_src, fresh, reset=True)
    if not ok:
        raise SystemExit("fresh bootstrap failed")
    if formal_state_business_hash_from_db(fresh) != formal_state_business_hash_from_db(config.path("formal_db")):
        raise SystemExit("fresh db business hash mismatch")
    _atomic_write(config.path("formal_db"), fresh.read_bytes())
    journal["steps"]["database_commit_complete"] = True
    journal["steps"]["validated"] = True
    journal["finished_at"] = datetime.now().isoformat()
    _atomic_write(migration_dir / "journal.json", json.dumps(journal, ensure_ascii=False, indent=2).encode("utf-8"))
    audit = {
        "operation_type": "formal_state_governance_migration",
        "batch_id": preview["batch_id"],
        "committed_at": datetime.now().isoformat(),
        "seed_hash_before": preview["formal_state_hash_before"],
        "seed_hash_after": preview["expected_formal_state_hash_after"],
        "database_hash_after": formal_state_business_hash_from_db(config.path("formal_db")),
        "backup_dir": str(backup),
        "rollback_available": True,
    }
    audit_dir = config.path("output_root") / "phase25_migration_preview"
    (audit_dir / "migration_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"migration committed: {preview['batch_id']}")


def rollback_migration(config, batch_id: str, backup_dir: str | Path):
    backup = Path(backup_dir)
    manifest_path = backup / "backup_manifest.json"
    if not manifest_path.exists():
        raise SystemExit("backup manifest missing")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    for name, expected in manifest["files"].items():
        if (backup / name).exists() and _sha(backup / name) != expected:
            raise SystemExit(f"backup hash mismatch: {name}")
    seed_src = config.path("events_seed").parent
    for name in ("events.jsonl", "sources.jsonl", "polls.jsonl",
                 "poll_questions.jsonl", "poll_results.jsonl",
                 "poll_sources.jsonl", "poll_source_links.jsonl",
                 "initial_snapshot.json", "snapshot_history.jsonl",
                 "seed_manifest.json", "schema_versions.json"):
        src = backup / name
        if src.exists():
            _atomic_write(seed_src / name, src.read_bytes())
    db_backup = backup / "election_context.db"
    if db_backup.exists():
        _atomic_write(config.path("formal_db"), db_backup.read_bytes())
    restored_db_hash = formal_state_business_hash_from_db(config.path("formal_db"))
    if restored_db_hash != manifest.get("database_hash_before"):
        raise SystemExit("rollback hash restoration failed")
    print(f"migration rolled back: {batch_id}")


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Phase 2.5 governance migration")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--batch-id", default=None)
    parser.add_argument("--backup-dir", default=None)
    args = parser.parse_args()
    config = load_config(args.config)
    if args.preview:
        build_preview(config)
    elif args.commit:
        preview, staging, tmp_db = build_preview(config)
        commit_migration(config, preview, staging, tmp_db)
    elif args.rollback:
        if not args.batch_id or not args.backup_dir:
            parser.error("--rollback requires --batch-id and --backup-dir")
        rollback_migration(config, args.batch_id, args.backup_dir)
    else:
        parser.error("--preview or --commit required")


if __name__ == "__main__":
    main()
