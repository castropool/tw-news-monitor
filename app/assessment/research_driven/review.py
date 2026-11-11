"""research-driven 人工终审 CLI（list/show/approve/reject）。

与 r2 review 语义一致：approve/reject 前校验文章哈希，防止审核前后内容变化。
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from app.assessment.r2.state import ReportRunStore
from app.assessment.research_driven.generation import PRODUCTION_ROOT_REL


def default_runs_root() -> Path:
    return Path(__file__).resolve().parents[3] / PRODUCTION_ROOT_REL


def _store(runs_root: Path) -> ReportRunStore:
    return ReportRunStore(runs_root)


def _reviewer() -> str:
    return os.getenv("USERNAME") or os.getenv("USER") or "operator"


def current_report_hash(run: dict) -> str | None:
    path = Path(run.get("output_path") or "")
    if path.exists():
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()
    return None


def list_reports(store: ReportRunStore, status: str | None = None) -> list[dict]:
    return store.list(status)


def show_report(store: ReportRunStore, run_key: str) -> dict:
    run = store.get(run_key)
    if not run:
        raise SystemExit(f"run 不存在: {run_key}")
    return run


def approve_report(store: ReportRunStore, run_key: str, reviewer: str | None = None) -> dict:
    run = store.get(run_key)
    if not run:
        return {"code": "NOT_FOUND", "run_key": run_key}
    if run.get("generation_status") != "ready_for_review":
        return {
            "code": "BLOCKED_NOT_READY",
            "run_key": run_key,
            "generation_status": run.get("generation_status"),
        }
    current = current_report_hash(run)
    if current and run.get("article_hash") and current != run.get("article_hash"):
        return {
            "code": "BLOCKED_REPORT_CHANGED",
            "run_key": run_key,
            "stored_hash": run.get("article_hash"),
            "current_hash": current,
        }
    reviewer = reviewer or _reviewer()
    run["generation_status"] = "human_approved"
    run["human_review_status"] = "human_approved"
    run["delivery_status"] = "delivery_pending"
    run["approved_by"] = reviewer
    store.save(run)
    store.append_review(
        run_key,
        {"action": "approve", "reviewer": reviewer, "at": run.get("completed_at") or ""},
    )
    return {"code": "APPROVED", "run_key": run_key, "reviewer": reviewer}


def reject_report(
    store: ReportRunStore, run_key: str, reviewer: str | None = None, reason: str = ""
) -> dict:
    run = store.get(run_key)
    if not run:
        return {"code": "NOT_FOUND", "run_key": run_key}
    if run.get("generation_status") != "ready_for_review":
        return {
            "code": "BLOCKED_NOT_READY",
            "run_key": run_key,
            "generation_status": run.get("generation_status"),
        }
    reviewer = reviewer or _reviewer()
    run["generation_status"] = "human_rejected"
    run["human_review_status"] = "human_rejected"
    run["delivery_status"] = "blocked_by_human_reject"
    run["rejected_by"] = reviewer
    run["rejection_reason"] = reason
    store.save(run)
    store.append_review(
        run_key,
        {"action": "reject", "reviewer": reviewer, "reason": reason, "at": ""},
    )
    return {"code": "REJECTED", "run_key": run_key, "reviewer": reviewer}


def main() -> int:
    parser = argparse.ArgumentParser(description="research-driven 人工终审")
    parser.add_argument("--runs-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="列出所有 run")
    p_list.add_argument("--status", default=None)

    p_show = sub.add_parser("show", help="查看 run 详情")
    p_show.add_argument("run_key")
    p_show.add_argument("--json", action="store_true")

    p_approve = sub.add_parser("approve", help="批准报告")
    p_approve.add_argument("run_key")
    p_approve.add_argument("--reviewer", default=None)

    p_reject = sub.add_parser("reject", help="拒绝报告")
    p_reject.add_argument("run_key")
    p_reject.add_argument("--reviewer", default=None)
    p_reject.add_argument("--reason", default="")

    args = parser.parse_args()
    runs_root = args.runs_root or default_runs_root()
    store = _store(runs_root)

    if args.command == "list":
        for run in list_reports(store, args.status):
            print(
                f"{run['run_key']}  {run.get('period_start')}~{run.get('period_end')}  "
                f"{run.get('generation_status')}  {run.get('word_path') or ''}"
            )
        return 0
    if args.command == "show":
        run = show_report(store, args.run_key)
        if args.json:
            print(json.dumps(run, ensure_ascii=False, indent=2))
        else:
            print(f"run_key: {run['run_key']}")
            print(f"周期: {run.get('period_start')} 至 {run.get('period_end')}")
            print(f"facts_cutoff: {run.get('facts_cutoff')}  poll_cutoff: {run.get('poll_cutoff')}")
            print(f"模型: {run.get('model')}  状态: {run.get('generation_status')}")
            print(f"事实安全检查: {run.get('fact_safety_status', 'n/a')}")
            print(f"Word: {run.get('word_path') or ''}")
            print("review_notes:")
            for note in run.get("review_notes") or []:
                print(f"  - {note}")
        return 0
    if args.command == "approve":
        result = approve_report(store, args.run_key, args.reviewer)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "reject":
        result = reject_report(store, args.run_key, args.reviewer, args.reason)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
