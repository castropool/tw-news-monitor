"""research-driven 调度/受控生成入口（只生成，不批准不发送）。"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime
from pathlib import Path

from app.assessment.research_driven.generation import (
    load_config,
    runs_root_from_config,
    run_generation,
)
from app.time_utils import TAIPEI


def _date(value: str) -> date:
    return date.fromisoformat(value)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="台南选情研判 research-driven 生成入口（只生成，不批准不发送）"
    )
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
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--mock-fixture", default=None)
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[3]
    config = load_config(args.config.resolve())
    runs_root = (
        args.runs_root
        or runs_root_from_config(config, project_root)
        or project_root / "data/election_assessment/tainan_2026/production"
    )
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
        provider=args.provider,
        model=args.model,
        mock_fixture=args.mock_fixture,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
