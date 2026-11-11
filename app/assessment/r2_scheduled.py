"""Phase R2 scheduled/controlled generation entry (generate-only)."""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from app.time_utils import TAIPEI
from app.assessment.r2.generation import run_generation


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase R2 调度生成入口（只生成，不批准不发送）")
    parser.add_argument("--config", type=Path, default=Path("config/election_assessment.yaml"))
    parser.add_argument("--runs-root", type=Path, default=None)
    parser.add_argument("--as-of", type=_date, default=None)
    parser.add_argument("--period-start", type=_date, default=None)
    parser.add_argument("--period-end", type=_date, default=None)
    parser.add_argument(
        "--trigger-type", choices=("scheduled", "manual", "controlled"), default="scheduled"
    )
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--force-regenerate", action="store_true")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[2]
    runs_root = args.runs_root or project_root / "data/election_assessment/tainan_2026/r2_runs"
    result = run_generation(
        config_path=args.config.resolve(),
        runs_root=runs_root,
        as_of=args.as_of or datetime.now(TAIPEI).date(),
        period_start=args.period_start,
        period_end=args.period_end,
        trigger_type=args.trigger_type,
        check_only=args.check_only,
        force_regenerate=args.force_regenerate,
        project_root=project_root,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
