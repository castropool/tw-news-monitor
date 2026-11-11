"""research-driven 生产状态 CLI。"""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from app.assessment.r2.period import next_scheduled_datetime
from app.assessment.r2.security import feishu_gate
from app.assessment.r2.state import ReportRunStore
from app.assessment.research_driven.generation import PRODUCTION_ROOT_REL, load_config
from app.time_utils import TAIPEI


def build_status(store: ReportRunStore, config_path: Path) -> dict:
    config = load_config(config_path)
    latest = store.latest()
    counts: dict[str, int] = {}
    for run in store.list():
        status = run.get("generation_status") or "unknown"
        counts[status] = counts.get(status, 0) + 1
    coverage_cutoff = ""
    if latest:
        coverage_cutoff = latest.get("facts_cutoff") or ""
    return {
        "latest_report": (
            {
                "run_key": latest["run_key"],
                "period": f"{latest.get('period_start')} 至 {latest.get('period_end')}",
                "generation_status": latest.get("generation_status"),
                "human_review_status": latest.get("human_review_status"),
                "facts_cutoff": latest.get("facts_cutoff"),
                "poll_cutoff": latest.get("poll_cutoff"),
                "model": latest.get("model"),
                "fact_safety_status": latest.get("fact_safety_status", "n/a"),
                "word_path": latest.get("word_path") or "",
                "completed_at": latest.get("completed_at") or "",
            }
            if latest
            else None
        ),
        "run_count_by_status": counts,
        "facts_cutoff": coverage_cutoff,
        "next_scheduled_run": next_scheduled_datetime(datetime.now(TAIPEI)).isoformat(),
        "feishu_gate": feishu_gate(config),
        "generation_mode": "research_driven",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="research-driven 生产状态")
    parser.add_argument("--config", default="config/election_assessment.yaml")
    parser.add_argument("--runs-root", type=Path, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[3]
    runs_root = args.runs_root or project_root / PRODUCTION_ROOT_REL
    store = ReportRunStore(runs_root)
    status = build_status(store, Path(args.config).resolve())
    if args.json:
        print(json.dumps(status, ensure_ascii=False, indent=2))
    else:
        latest = status["latest_report"]
        if latest:
            print(f"最新报告: {latest['period']} [{latest['generation_status']}]")
            print(f"  facts_cutoff={latest['facts_cutoff']} poll_cutoff={latest['poll_cutoff']} model={latest['model']}")
            print(f"  事实安全: {latest['fact_safety_status']}")
            print(f"  Word: {latest['word_path']}")
        else:
            print("暂无生产报告")
        print(f"生成模式: research_driven")
        print(f"下次计划运行: {status['next_scheduled_run']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
