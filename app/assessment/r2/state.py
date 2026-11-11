"""Phase R2 report run store (JSON-based, atomic, append-only review/delivery logs)."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


RUNS_ROOT_REL = Path("data/election_assessment/tainan_2026/r2_runs")


def default_runs_root(project_root: Path) -> Path:
    return project_root / RUNS_ROOT_REL


def utcnow_iso() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


class ReportRunStore:
    """Small operational store; separate from formal election_context."""

    def __init__(self, root: Path):
        self.root = Path(root)
        self.runs_dir = self.root / "runs"
        self.reviews_dir = self.root / "reviews"
        self.deliveries_dir = self.root / "deliveries"
        self.evaluations_dir = self.root / "evaluations"

    def run_path(self, run_key: str) -> Path:
        return self.runs_dir / f"{run_key}.json"

    def get(self, run_key: str) -> dict | None:
        path = self.run_path(run_key)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def save(self, run: dict) -> None:
        if not run.get("run_key"):
            raise ValueError("run_key 必填")
        _atomic_write_json(self.run_path(run["run_key"]), run)

    def list(self, status: str | None = None) -> list[dict]:
        runs: list[dict] = []
        if self.runs_dir.exists():
            for path in sorted(self.runs_dir.glob("*.json")):
                run = json.loads(path.read_text(encoding="utf-8"))
                if status is None or run.get("generation_status") == status:
                    runs.append(run)
        return runs

    def latest(self) -> dict | None:
        runs = self.list()
        if not runs:
            return None
        return max(runs, key=lambda r: r.get("started_at") or "")

    def reviews(self, run_key: str) -> list[dict]:
        path = self.reviews_dir / f"{run_key}.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def append_review(self, run_key: str, review: dict) -> None:
        path = self.reviews_dir / f"{run_key}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(review, ensure_ascii=False) + "\n")

    def deliveries(self, run_key: str) -> list[dict]:
        path = self.deliveries_dir / f"{run_key}.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]

    def append_delivery(self, run_key: str, receipt: dict) -> None:
        path = self.deliveries_dir / f"{run_key}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(receipt, ensure_ascii=False) + "\n")

    def snapshot_run(self, run: dict) -> Path:
        """Append-only snapshot before a run record is overwritten by retry."""
        history_dir = self.root / "history"
        history_dir.mkdir(parents=True, exist_ok=True)
        stamp = utcnow_iso().replace("-", "").replace(":", "").replace("+", "_")
        path = history_dir / f"{run.get('run_key')}__{run.get('run_id')}__{stamp}.json"
        _atomic_write_json(path, run)
        return path

    def append_evaluation(self, run_key: str, evaluation: dict) -> None:
        path = self.evaluations_dir / f"{run_key}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(evaluation, ensure_ascii=False) + "\n")

    def evaluations(self, run_key: str) -> list[dict]:
        path = self.evaluations_dir / f"{run_key}.jsonl"
        if not path.exists():
            return []
        return [
            json.loads(line)
            for line in path.read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]


def new_run_record(
    *,
    run_id: str,
    run_key: str,
    election_id: str,
    period_start: str,
    period_end: str,
    trigger_type: str,
    scheduled_for: str,
) -> dict:
    return {
        "run_id": run_id,
        "run_key": run_key,
        "election_id": election_id,
        "period_start": period_start,
        "period_end": period_end,
        "trigger_type": trigger_type,
        "scheduled_for": scheduled_for,
        "started_at": utcnow_iso(),
        "completed_at": "",
        "facts_cutoff": "",
        "poll_cutoff": "",
        "coverage_version": "",
        "model": "",
        "input_hash": "",
        "report_hash": "",
        "word_hash": "",
        "generation_status": "running",
        "machine_validation_status": "not_run",
        "human_review_status": "not_required",
        "delivery_status": "not_attempted",
        "output_path": "",
        "word_path": "",
        "machine_gate_summary": {},
        "review_notes": [],
        "blocking_issues": [],
        "error": "",
    }
