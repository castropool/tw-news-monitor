"""Sync production seed files from the authoritative formal DB (Option A).

The production deployment (2026-08-01 legacy) left seed files out of sync with
the formal election_context.db.  The publication pipeline fails closed on
"staging formal state hash mismatch" until seeds reproduce the DB.

This script exports the current formal DB into seed files (business state of
the DB is NOT modified), validates that:
  1. formal_state_business_hash_from_seed_dir(seed) == db hash, and
  2. bootstrap_v2(seed) rebuilds a DB with the same business hash.
Then, with --write, it backs up the old seed dir and writes the synced seeds.
"""

from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
import tempfile
from datetime import datetime
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.election_context.bootstrap_v2 import run_bootstrap_v2  # noqa: E402
from app.election_context.formal_state_hash import (  # noqa: E402
    formal_state_business_hash_from_db,
    formal_state_business_hash_from_seed_dir,
)


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    with open(path, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


def export_seeds(conn: sqlite3.Connection, seed_dir: Path) -> None:
    conn.row_factory = sqlite3.Row
    seed_dir.mkdir(parents=True, exist_ok=True)

    election = conn.execute("SELECT * FROM elections ORDER BY election_id").fetchone()
    (seed_dir / "election.json").write_text(
        json.dumps(dict(election), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    actors = []
    for r in conn.execute("SELECT * FROM actors ORDER BY actor_id").fetchall():
        d = dict(r)
        try:
            d["aliases"] = json.loads(d.get("aliases_json") or "[]")
        except json.JSONDecodeError:
            d["aliases"] = []
        d.pop("aliases_json", None)
        actors.append(d)
    (seed_dir / "actors.yaml").write_text(
        yaml.safe_dump({"actors": actors}, allow_unicode=True, sort_keys=False),
        encoding="utf-8",
    )

    sources = [dict(r) for r in conn.execute("SELECT * FROM sources ORDER BY source_id")]
    _write_jsonl(seed_dir / "sources.jsonl", sources)
    source_by_id = {s["source_id"]: s for s in sources}

    events = []
    for r in conn.execute("SELECT * FROM election_events ORDER BY event_id").fetchall():
        evt = dict(r)
        links = conn.execute(
            "SELECT source_id, is_primary FROM event_sources WHERE event_id=? ORDER BY source_id",
            (evt["event_id"],),
        ).fetchall()
        evt_sources = []
        for link in links:
            src = dict(source_by_id[link["source_id"]])
            src["is_primary"] = int(bool(link["is_primary"]))
            evt_sources.append(src)
        evt["sources"] = evt_sources
        events.append(evt)
    _write_jsonl(seed_dir / "events.jsonl", events)

    polls = [dict(r) for r in conn.execute("SELECT * FROM election_polls ORDER BY poll_id")]
    _write_jsonl(seed_dir / "polls.jsonl", polls)

    questions = [
        dict(r)
        for r in conn.execute("SELECT * FROM poll_questions ORDER BY poll_id, question_id")
    ]
    _write_jsonl(seed_dir / "poll_questions.jsonl", questions)

    results = [
        dict(r)
        for r in conn.execute(
            "SELECT * FROM poll_results ORDER BY poll_id, question_id, option_id"
        )
    ]
    _write_jsonl(seed_dir / "poll_results.jsonl", results)

    poll_sources = [
        dict(r)
        for r in conn.execute(
            "SELECT s.* FROM sources s JOIN poll_source_links p ON p.source_id=s.source_id "
            "ORDER BY s.source_id"
        )
    ]
    _write_jsonl(seed_dir / "poll_sources.jsonl", poll_sources)

    links = [
        dict(r)
        for r in conn.execute("SELECT poll_id, source_id FROM poll_source_links ORDER BY poll_id, source_id")
    ]
    _write_jsonl(seed_dir / "poll_source_links.jsonl", links)

    snapshots = [dict(r) for r in conn.execute("SELECT * FROM election_state_snapshots")]
    active = [s for s in snapshots if s.get("snapshot_status") == "active"]
    history = [s for s in snapshots if s.get("snapshot_status") != "active"]
    if active:
        (seed_dir / "initial_snapshot.json").write_text(
            json.dumps(active[0], ensure_ascii=False, indent=2), encoding="utf-8"
        )
    _write_jsonl(seed_dir / "snapshot_history.jsonl", history)


def validate_seeds(seed_dir: Path, db_path: Path) -> dict:
    seed_hash = formal_state_business_hash_from_seed_dir(seed_dir)
    db_hash = formal_state_business_hash_from_db(db_path)
    rebuilt = Path(tempfile.mkdtemp(prefix="seed_sync_rebuild_")) / "rebuilt.db"
    ok, stats = run_bootstrap_v2(seed_dir, rebuilt, reset=True)
    rebuilt_hash = formal_state_business_hash_from_db(rebuilt) if ok else ""
    return {
        "seed_hash": seed_hash,
        "db_hash": db_hash,
        "seed_matches_db": seed_hash == db_hash,
        "bootstrap_ok": ok,
        "bootstrap_stats": stats,
        "rebuilt_hash": rebuilt_hash,
        "rebuilt_matches_seed": ok and rebuilt_hash == seed_hash,
        "ready": seed_hash == db_hash and ok and rebuilt_hash == seed_hash,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Sync production seeds from formal DB")
    parser.add_argument("--db", default=str(ROOT / "data/election_context.db"))
    parser.add_argument("--seed-dir", default=str(ROOT / "data/election_seed/tainan_2026"))
    parser.add_argument("--backup-root", default=str(ROOT / "data/election_candidates/tainan_2026/phase_f1/candidate_deployment_backups"))
    parser.add_argument("--write", action="store_true", help="write to the real seed dir (after backup)")
    parser.add_argument("--no-backup", action="store_true", help="skip backup (backup done externally)")
    args = parser.parse_args()

    db_path = Path(args.db)
    seed_dir = Path(args.seed_dir)
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)

    if args.write:
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_dir = Path(args.backup_root) / f"{stamp}_seed_sync_before"
        if seed_dir.exists() and not args.no_backup:
            shutil.copytree(seed_dir, backup_dir)
            print(f"已备份旧种子目录：{backup_dir}")
        export_seeds(conn, seed_dir)
        result = validate_seeds(seed_dir, db_path)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        conn.close()
        if not result["ready"]:
            raise SystemExit("同步验证未通过，请检查导出逻辑")
        print("种子同步完成：种子哈希=正式库哈希，且可重新 bootstrap")
        return

    tmp = Path(tempfile.mkdtemp(prefix="seed_sync_preview_"))
    export_seeds(conn, tmp)
    result = validate_seeds(tmp, db_path)
    conn.close()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(f"预览种子目录：{tmp}")
    if not result["ready"]:
        raise SystemExit(1)
    print("预览验证通过（未写入生产）")


if __name__ == "__main__":
    main()
