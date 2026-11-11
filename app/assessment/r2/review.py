"""Phase R2 human review CLI (list/show/approve/reject, append-only)."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

from app.time_utils import TAIPEI
from app.assessment.r2.state import ReportRunStore, utcnow_iso


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def current_report_hash(run: dict) -> str | None:
    output_path = run.get("output_path")
    if not output_path or not Path(output_path).exists():
        return None
    return sha256_file(Path(output_path))


def list_reports(store: ReportRunStore, status: str | None = None) -> list[dict]:
    return store.list(status=status)


def show_report(store: ReportRunStore, run_key: str) -> dict:
    run = store.get(run_key)
    if not run:
        raise KeyError(f"run_key 不存在: {run_key}")
    return run


def _reviewer() -> str:
    return os.environ.get("USERNAME") or os.environ.get("USER") or "operator"


def approve_report(store: ReportRunStore, run_key: str, reviewer: str | None = None) -> dict:
    run = store.get(run_key)
    if not run:
        raise KeyError(f"run_key 不存在: {run_key}")
    if run.get("generation_status") != "ready_for_human_review":
        raise ValueError(
            f"仅 ready_for_human_review 可批准；当前 generation_status={run.get('generation_status')}"
        )
    if run.get("human_review_status") in ("human_approved", "human_rejected"):
        raise ValueError(
            f"该报告已终审（human_review_status={run.get('human_review_status')}），禁止重复审核"
        )
    current_hash = current_report_hash(run)
    if not current_hash or current_hash != run.get("report_hash"):
        raise ValueError("BLOCKED_REPORT_CHANGED: 报告内容哈希与生成时不一致，禁止批准")
    review = {
        "review_id": f"rev_{run_key}_{len(store.reviews(run_key)) + 1}",
        "run_id": run.get("run_id"),
        "run_key": run_key,
        "decision": "approve",
        "reviewer": reviewer or _reviewer(),
        "reviewed_at": utcnow_iso(),
        "reason": "",
        "report_hash": current_hash,
    }
    store.append_review(run_key, review)
    run["generation_status"] = "human_approved"
    run["human_review_status"] = "human_approved"
    run["delivery_status"] = "delivery_pending"
    run["review_id"] = review["review_id"]
    store.save(run)
    return {"status": "approved", "review": review, "run": run}


def reject_report(
    store: ReportRunStore, run_key: str, reviewer: str | None = None, reason: str = ""
) -> dict:
    run = store.get(run_key)
    if not run:
        raise KeyError(f"run_key 不存在: {run_key}")
    if run.get("generation_status") != "ready_for_human_review":
        raise ValueError(
            f"仅 ready_for_human_review 可拒绝；当前 generation_status={run.get('generation_status')}"
        )
    if run.get("human_review_status") in ("human_approved", "human_rejected"):
        raise ValueError(
            f"该报告已终审（human_review_status={run.get('human_review_status')}），禁止重复审核"
        )
    review = {
        "review_id": f"rev_{run_key}_{len(store.reviews(run_key)) + 1}",
        "run_id": run.get("run_id"),
        "run_key": run_key,
        "decision": "reject",
        "reviewer": reviewer or _reviewer(),
        "reviewed_at": utcnow_iso(),
        "reason": reason,
        "report_hash": current_report_hash(run) or "",
    }
    store.append_review(run_key, review)
    run["generation_status"] = "human_rejected"
    run["human_review_status"] = "human_rejected"
    run["delivery_status"] = "blocked_by_human_reject"
    run["review_id"] = review["review_id"]
    store.save(run)
    return {"status": "rejected", "review": review, "run": run}


def _fmt_run(run: dict) -> str:
    return (
        f"{run.get('run_key')} | {run.get('generation_status')} | "
        f"machine={run.get('machine_validation_status')} | "
        f"human={run.get('human_review_status')} | delivery={run.get('delivery_status')} | "
        f"period={run.get('period_start')}~{run.get('period_end')}"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase R2 人工终审 CLI")
    parser.add_argument("--runs-root", type=Path, default=None)
    sub = parser.add_subparsers(dest="command", required=True)
    p_list = sub.add_parser("list")
    p_list.add_argument("--status", default=None)
    p_show = sub.add_parser("show")
    p_show.add_argument("run_key")
    p_show.add_argument("--json", action="store_true")
    p_approve = sub.add_parser("approve")
    p_approve.add_argument("run_key")
    p_approve.add_argument("--reviewer", default=None)
    p_reject = sub.add_parser("reject")
    p_reject.add_argument("run_key")
    p_reject.add_argument("--reviewer", default=None)
    p_reject.add_argument("--reason", default="")
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parents[3]
    runs_root = args.runs_root or project_root / "data/election_assessment/tainan_2026/r2_runs"
    store = ReportRunStore(runs_root)

    if args.command == "list":
        for run in list_reports(store, args.status):
            print(_fmt_run(run))
        return 0
    if args.command == "show":
        run = show_report(store, args.run_key)
        if args.json:
            print(json.dumps(run, ensure_ascii=False, indent=2))
        else:
            print(_fmt_run(run))
            print("machine_disposition:", (run.get("machine_disposition") or {}).get("production_disposition"))
            print("facts_cutoff:", run.get("facts_cutoff"))
            print("poll_cutoff:", run.get("poll_cutoff"))
            print("word_path:", run.get("word_path"))
            print("machine_gate:", json.dumps(run.get("machine_gate_summary") or {}, ensure_ascii=False))
            print("review_notes:")
            for note in run.get("review_notes") or []:
                print(" -", note)
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
