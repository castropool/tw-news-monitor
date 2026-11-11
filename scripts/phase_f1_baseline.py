"""Phase F1 baseline freezer (read-only).

Creates data/election_candidates/tainan_2026/phase_f1/*.json with the
pre-change production/candidate/formal state that Phase F1 must not disturb.
Only reads production data and writes inside the workspace phase_f1 dir.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import subprocess
from datetime import datetime, timezone
from pathlib import Path

import sys

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.election_context.formal_state_hash import (  # noqa: E402
    formal_state_business_hash_from_db,
    formal_state_business_hash_from_seed_dir,
)


F0_AUDIT_DIR = ROOT / "data/election_candidates/tainan_2026/fact_maintenance_audit"
PHASE_F1_DIR = ROOT / "data/election_candidates/tainan_2026/phase_f1"
PROD = ROOT


def sha256_file(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def db_table_rows(path: Path, table: str) -> tuple[int, str]:
    if not path.exists():
        return 0, ""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        rows = conn.execute(f"SELECT * FROM {table}").fetchall()
        h = hashlib.sha256(
            json.dumps([list(r) for r in rows], ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        return len(rows), h
    except sqlite3.OperationalError:
        return 0, ""
    finally:
        conn.close()


def sqlite_scalar(path: Path, sql: str, params: tuple = ()) -> object:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        return conn.execute(sql, params).fetchone()
    finally:
        conn.close()


def candidate_state(db: Path) -> dict:
    if not db.exists():
        return {"exists": False}
    status = {}
    for row in sqlite3.connect(f"file:{db}?mode=ro", uri=True).execute(
        "SELECT review_status, COUNT(*) FROM candidate_events GROUP BY review_status"
    ):
        status[row[0]] = row[1]
    cursor = sqlite_scalar(db, "SELECT * FROM scan_cursors ORDER BY updated_at DESC LIMIT 1")
    latest_run = sqlite_scalar(
        db,
        "SELECT run_id, status, scan_mode, cursor_before, cursor_after, started_at, finished_at "
        "FROM pipeline_runs ORDER BY started_at DESC LIMIT 1",
    )
    return {
        "exists": True,
        "sha256": sha256_file(db),
        "status_counts": status,
        "latest_cursor": dict(zip(
            ("election_id", "cursor_type", "last_article_id", "last_published_at",
             "last_collected_at", "last_successful_run_id", "updated_at"), cursor)) if cursor else None,
        "latest_run": dict(zip(
            ("run_id", "status", "scan_mode", "cursor_before", "cursor_after",
             "started_at", "finished_at"), latest_run)) if latest_run else None,
    }


def scheduled_tasks() -> list[dict]:
    out = []
    for name in ("Taiwan News Monitor", "Taiwan News Event Pipeline", "Tainan Election Candidate Monitor"):
        try:
            r = subprocess.run(
                ["schtasks.exe", "/query", "/tn", name, "/v", "/fo", "LIST"],
                capture_output=True, text=True, timeout=20,
            )
            lines = {}
            for line in r.stdout.splitlines():
                if ":" in line:
                    k, _, v = line.partition(":")
                    lines[k.strip()] = v.strip()
            out.append({
                "task_name": name,
                "exists": r.returncode == 0,
                "enabled": lines.get("Scheduled Task State", ""),
                "last_run_time": lines.get("Last Run Time", ""),
                "last_result": lines.get("Last Result", ""),
                "next_run_time": lines.get("Next Run Time", ""),
                "action": lines.get("Task To Run", ""),
                "working_directory": lines.get("Start In", ""),
            })
        except Exception as exc:  # pragma: no cover
            out.append({"task_name": name, "error": str(exc)})
    return out


def main() -> None:
    PHASE_F1_DIR.mkdir(parents=True, exist_ok=True)
    now = datetime.now(timezone.utc).isoformat()

    # 1) F0 input manifest
    f0_files = sorted(p for p in F0_AUDIT_DIR.rglob("*") if p.is_file())
    f0_manifest = {
        "schema_version": "phase-f1.phase-f0-input-manifest.v1",
        "generated_at": now,
        "f0_audit_dir": str(F0_AUDIT_DIR),
        "final_audit_report_sha256": sha256_file(F0_AUDIT_DIR / "final_audit_report.md"),
        "files": [
            {
                "path": str(p.relative_to(F0_AUDIT_DIR)).replace("\\", "/"),
                "sha256": sha256_file(p),
            }
            for p in f0_files
        ],
        "f0_audit_sha256_aggregate": hashlib.sha256(
            "\n".join(f"{p}|{sha256_file(p)}" for p in f0_files).encode("utf-8")
        ).hexdigest(),
    }

    # 2) deployment environment
    env = {
        "schema_version": "phase-f1.deployment-environment.v1",
        "generated_at": now,
        "production_directory": str(PROD),
        "workspace_directory": str(ROOT),
        "python_executable": r"C:\Users\User\AppData\Local\Programs\Python\Python312\python.exe",
        "venv": "none (system Python 3.12.10 used by production scheduled tasks)",
        "news_monitor_runner": str(PROD / "run_monitor.bat"),
        "news_monitor_command": "python -m app.main",
        "legacy_pipeline_runner": str(PROD / "run_pipeline.bat"),
        "legacy_pipeline_command": "python -m app.election_context.pipeline run",
        "candidate_runner_planned": str(PROD / "run_candidate_monitor.bat"),
        "candidate_command_planned": "python -m app.election_candidates.build_candidate_queue --since-last-success",
        "candidate_config_planned": str(PROD / "config/election_candidate_pipeline.yaml"),
        "candidate_log_planned": str(PROD / "data/election_candidates/tainan_2026/logs/candidate_monitor.log"),
        "scheduled_tasks": scheduled_tasks(),
    }

    # 3) protected business state
    prod_news = PROD / "data/news.db"
    prod_watch = PROD / "data/election_watch.db"
    prod_formal = PROD / "data/election_context.db"
    prod_seed = PROD / "data/election_seed/tainan_2026"
    ws_news = ROOT / "data/news.db"
    ws_watch = ROOT / "data/election_watch.db"
    ws_formal = ROOT / "data/election_context.db"
    ws_seed = ROOT / "data/election_seed/tainan_2026"
    ws_candidate = ROOT / "data/election_candidates/tainan_2026/candidate_fact_pipeline.db"
    rc2_candidate = ROOT / "dist/tainan-assessment-production-rc2/data/election_candidates/tainan_2026/candidate_fact_pipeline.db"
    prod_coverage = PROD / "data/election_seed/tainan_2026/fact_coverage_20260801_v4"
    ws_coverage = ROOT / "data/election_seed/tainan_2026/fact_coverage_20260801_v4"
    prod_status = PROD / "data/production/production_status.json"
    ws_status = ROOT / "data/production/production_status.json"

    news_latest_prod = sqlite_scalar(
        prod_news, "SELECT max(fetched_at), max(published_at), count(*) FROM articles"
    )
    news_since_prod = sqlite_scalar(
        prod_news, "SELECT count(*) FROM articles WHERE date(published_at) >= '2026-07-28'"
    )

    def coverage_facts_cutoff(status_path: Path) -> str:
        try:
            d = json.loads(status_path.read_text(encoding="utf-8"))
            return str(d.get("coverage", {}).get("facts_cutoff", ""))
        except Exception:
            return ""

    protected = {
        "schema_version": "phase-f1.protected-business-state-before.v1",
        "generated_at": now,
        "production": {
            "news_db": {
                "path": str(prod_news),
                "sha256": sha256_file(prod_news),
                "latest_fetched": news_latest_prod[0] if news_latest_prod else None,
                "latest_published": news_latest_prod[1] if news_latest_prod else None,
                "rows": news_latest_prod[2] if news_latest_prod else None,
                "rows_since_2026_07_28": news_since_prod[0] if news_since_prod else None,
            },
            "election_watch_db": {"sha256": sha256_file(prod_watch)},
            "formal_db": {
                "sha256": sha256_file(prod_formal),
                "business_hash": formal_state_business_hash_from_db(prod_formal),
                "row_counts": {
                    t: db_table_rows(prod_formal, t)[0]
                    for t in ("elections", "actors", "sources", "election_events",
                              "event_sources", "election_polls", "poll_questions",
                              "poll_results", "poll_source_links", "election_state_snapshots")
                },
            },
            "seed": {
                "dir": str(prod_seed),
                "business_hash": formal_state_business_hash_from_seed_dir(prod_seed),
                "events_sha256": sha256_file(prod_seed / "events.jsonl"),
                "sources_sha256": sha256_file(prod_seed / "sources.jsonl"),
                "initial_snapshot_sha256": sha256_file(prod_seed / "initial_snapshot.json"),
                "snapshot_history_sha256": sha256_file(prod_seed / "snapshot_history.jsonl"),
            },
            "monitor_log": {"path": str(PROD / "data/monitor.log"), "sha256": sha256_file(PROD / "data/monitor.log")},
            "pipeline_log": {"path": str(PROD / "data/pipeline.log"), "sha256": sha256_file(PROD / "data/pipeline.log")},
            "coverage_facts_cutoff": coverage_facts_cutoff(prod_status),
            "production_status": {
                "path": str(prod_status),
                "sha256": sha256_file(prod_status),
            },
        },
        "workspace": {
            "formal_db": {
                "sha256": sha256_file(ws_formal),
                "business_hash": formal_state_business_hash_from_db(ws_formal),
            },
            "seed_business_hash": formal_state_business_hash_from_seed_dir(ws_seed),
            "candidate_db": candidate_state(ws_candidate),
            "rc2_candidate_db": candidate_state(rc2_candidate),
            "news_db_sha256": sha256_file(ws_news),
            "election_watch_db_sha256": sha256_file(ws_watch),
            "coverage_facts_cutoff": coverage_facts_cutoff(ws_status),
            "production_status_sha256": sha256_file(ws_status),
        },
        "formal_state_business_hash_recorded": "8a42da2ef1f7ca73dc9777898bc7676076fc5d96f919a68adaad6dab40383207",
        "expected_facts_cutoff": "2026-07-27",
        "expected_active_snapshot_id": "tn_state_20260801_v1",
        "expected_coverage_version": "fact_coverage_20260801_v4",
    }

    baseline = {
        "schema_version": "phase-f1.baseline-manifest.v1",
        "generated_at": now,
        "phase": "Phase F1 - Production Fact Maintenance Loop Closure",
        "f0_conclusion": "FACT_MAINTENANCE_SMALL_GAPS",
        "production_directory": str(PROD),
        "workspace_directory": str(ROOT),
        "news_monitor": {
            "task_exists": True,
            "enabled": True,
            "schedule": "every 30 min (:14/:44, start 2026-08-01 11:14:07)",
            "last_run_time": "2026-08-09T16:14:08+08:00",
            "last_result": 0,
        },
        "candidate_pipeline": {
            "production_deployed": False,
            "production_scheduled": False,
            "workspace_cursor_state": candidate_state(ws_candidate),
        },
        "formal_state": {
            "business_hash": "8a42da2ef1f7ca73dc9777898bc7676076fc5d96f919a68adaad6dab40383207",
            "facts_cutoff": "2026-07-27",
            "poll_cutoff": "2026-03-12",
            "active_snapshot_id": "tn_state_20260801_v1",
            "coverage_version": "fact_coverage_20260801_v4",
            "coverage_status": "partial",
        },
        "test_baseline": {
            "passed": 2156,
            "skipped": 4,
            "failed": 0,
            "xfailed": 0,
            "command": "python -m pytest tests/ -q --tb=short",
            "ran_at": "2026-08-09 (before Phase F1 changes)",
        },
    }

    for name, payload in (
        ("phase_f0_input_manifest.json", f0_manifest),
        ("deployment_environment.json", env),
        ("protected_business_state_before.json", protected),
        ("baseline_manifest.json", baseline),
    ):
        (PHASE_F1_DIR / name).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        print(f"wrote {PHASE_F1_DIR / name}")


if __name__ == "__main__":
    main()
