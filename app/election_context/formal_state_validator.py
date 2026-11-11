"""Formal state validator: seed governance, reproducibility and DB consistency."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .bootstrap_v2 import run_bootstrap_v2
from .formal_state_hash import (
    formal_state_business_hash_from_db,
    formal_state_business_hash_from_seed,
)


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def validate_formal_state(config) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    seed = config.path("events_seed").parent

    manifest_path = seed / "seed_manifest.json"
    manifest_valid = manifest_path.exists()
    if not manifest_valid:
        errors.append("seed_manifest_valid")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_valid else {}

    required = ["election.json", "actors.yaml", "sources.jsonl", "events.jsonl",
                "initial_snapshot.json", "snapshot_history.jsonl", "polls.jsonl",
                "poll_sources.jsonl", "poll_source_links.jsonl"]
    missing = [f for f in required if not (seed / f).exists()]
    if missing:
        errors.append("all_seed_files_exist")
    for ent, info in manifest.get("entities", {}).items():
        if info.get("sha256") and info["sha256"] != _sha(seed / info["path"]):
            errors.append(f"all_seed_hashes_valid:{ent}")

    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        def ids(table, col):
            return [r[0] for r in conn.execute(f"SELECT {col} FROM {table}")]

        for table, col in (("election_events", "event_id"), ("sources", "source_id"),
                           ("election_polls", "poll_id"),
                           ("election_state_snapshots", "snapshot_id")):
            vals = ids(table, col)
            if len(vals) != len(set(vals)):
                errors.append(f"{table}_ids_unique")

        orphan_es = conn.execute(
            "SELECT COUNT(*) FROM event_sources es LEFT JOIN election_events e ON e.event_id=es.event_id "
            "LEFT JOIN sources s ON s.source_id=es.source_id WHERE e.event_id IS NULL OR s.source_id IS NULL"
        ).fetchone()[0]
        if orphan_es:
            errors.append("all_event_sources_resolve")
        orphan_ps = conn.execute(
            "SELECT COUNT(*) FROM poll_source_links p LEFT JOIN election_polls p2 ON p2.poll_id=p.poll_id "
            "LEFT JOIN sources s ON s.source_id=p.source_id WHERE p2.poll_id IS NULL OR s.source_id IS NULL"
        ).fetchone()[0]
        if orphan_ps:
            errors.append("all_poll_sources_resolve")

        active = [r["snapshot_id"] for r in conn.execute(
            "SELECT snapshot_id FROM election_state_snapshots WHERE snapshot_status='active'"
        )]
        if len(active) != 1:
            errors.append("exactly_one_active_snapshot")
        hist = [r["snapshot_id"] for r in conn.execute(
            "SELECT snapshot_id FROM election_state_snapshots WHERE snapshot_status!='active'"
        )]
        all_snapshots = set(hist) | set(active)
        superseded = [r["superseded_by"] for r in conn.execute(
            "SELECT superseded_by FROM election_state_snapshots WHERE superseded_by IS NOT NULL"
        )]
        if any(s not in all_snapshots for s in superseded):
            errors.append("snapshot_history_consistent")
    finally:
        conn.close()

    seed_hash = formal_state_business_hash_from_seed(config)
    db_hash = formal_state_business_hash_from_db(config.path("formal_db"))
    if seed_hash != db_hash:
        errors.append("database_matches_seed")

    tmp = config.path("output_root").parent.parent / "tmp" / "rebuild" / "election_context_rebuilt.db"
    tmp.parent.mkdir(parents=True, exist_ok=True)
    ok, _ = run_bootstrap_v2(seed, tmp, reset=True)
    rebuilt_hash = formal_state_business_hash_from_db(tmp) if ok else ""
    if not ok or rebuilt_hash != seed_hash:
        errors.append("bootstrap_reproducible")
    if rebuilt_hash != db_hash:
        errors.append("formal_state_hash_valid")

    fts = _check_fts(conn_path=config.path("formal_db"))
    if not fts:
        errors.append("fts_consistent")

    pub_root = config.path("output_root") / "publication_batches"
    unfinished_pub = (
        any(
            (pub_root / d / "publication_commit_journal.json").exists()
            and _unfinished(pub_root / d / "publication_commit_journal.json")
            for d in pub_root.iterdir() if d.is_dir()
        )
        if pub_root.exists()
        else False
    )
    rec_root = config.path("output_root") / "publication_recovery_journals"
    unfinished_rec = any(
        (rec_root / f).exists() and _unfinished(rec_root / f)
        for f in rec_root.glob("*.json")
    ) if rec_root.exists() else False
    if unfinished_pub:
        errors.append("unfinished_publication_journal=false")
    if unfinished_rec:
        errors.append("unfinished_recovery_journal=false")

    return {
        "formal_state_ready": not errors,
        "errors": errors,
        "warnings": warnings,
        "seed_manifest_valid": manifest_valid,
        "bootstrap_reproducible": "bootstrap_reproducible" not in errors,
        "database_matches_seed": "database_matches_seed" not in errors,
        "formal_state_hash": seed_hash,
        "database_hash": db_hash,
        "unfinished_publication_journal": unfinished_pub,
        "unfinished_recovery_journal": unfinished_rec,
    }


def _unfinished(path: Path) -> bool:
    try:
        j = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return True
    steps = j.get("steps") or {}
    return bool(
        (steps.get("seed_commit_started") and not steps.get("seed_commit_complete"))
        or (steps.get("database_commit_started") and not steps.get("database_commit_complete"))
    )


def _check_fts(conn_path: Path) -> bool:
    conn = sqlite3.connect(f"file:{conn_path}?mode=ro", uri=True)
    try:
        events = conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0]
        fts = conn.execute("SELECT COUNT(*) FROM election_events_fts").fetchone()[0]
        return events == fts
    except sqlite3.OperationalError:
        return False
    finally:
        conn.close()


def main():
    import argparse
    import json as _json
    import sys
    from pathlib import Path as _P

    sys.path.insert(0, str(_P(__file__).resolve().parent.parent.parent))
    from app.election_candidates.config import load_config

    parser = argparse.ArgumentParser(description="Formal state validator")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--election-id", default=None)
    args = parser.parse_args()
    config = load_config(args.config)
    result = validate_formal_state(config)
    print(_json.dumps(result, ensure_ascii=False, indent=2))
    raise SystemExit(0 if result["formal_state_ready"] else 1)


if __name__ == "__main__":
    main()
