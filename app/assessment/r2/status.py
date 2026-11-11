"""Phase R2 status CLI."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

import yaml

from app.time_utils import TAIPEI
from app.assessment.r2.period import next_scheduled_datetime
from app.assessment.r2.state import ReportRunStore


def build_status(store: ReportRunStore, config_path: Path) -> dict:
    config = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    latest = store.latest()
    runs = store.list()
    by_status: dict[str, int] = {}
    for run in runs:
        key = run.get("generation_status") or "unknown"
        by_status[key] = by_status.get(key, 0) + 1
    coverage = {}
    from app.assessment.r2.security import feishu_gate

    return {
        "latest_report": latest,
        "run_count_by_status": by_status,
        "facts_cutoff": latest.get("facts_cutoff") if latest else None,
        "next_scheduled_run": next_scheduled_datetime().isoformat(),
        "feishu_gate": feishu_gate(config),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase R2 状态 CLI")
    parser.add_argument("--config", type=Path, default=Path("config/election_assessment.yaml"))
    parser.add_argument("--runs-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[3]
    runs_root = args.runs_root or project_root / "data/election_assessment/tainan_2026/r2_runs"
    store = ReportRunStore(runs_root)
    status = build_status(store, args.config)
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        latest = status["latest_report"]
        print("latest run:", latest.get("run_key") if latest else None)
        print("generation:", latest.get("generation_status") if latest else None)
        print("disposition:", (latest.get("machine_disposition") or {}).get("production_disposition") if latest else None)
        print("human review:", latest.get("human_review_status") if latest else None)
        print("delivery:", latest.get("delivery_status") if latest else None)
        print("facts_cutoff:", status["facts_cutoff"])
        print("next scheduled run:", status["next_scheduled_run"])
        print("production_delivery_ready:", status["feishu_gate"]["production_delivery_ready"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
