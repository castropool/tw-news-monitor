"""管道运行状态：独立 run 目录、stage 结果、failure summary、latest 指针。"""

from __future__ import annotations

import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Any


def atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    tmp.replace(path)


def create_run_dir(
    pipeline_runs_root: Path,
    period_start: str,
    period_end: str,
    run_id: str,
) -> Path:
    run_dir = pipeline_runs_root / f"{period_start}_{period_end}" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    return run_dir


def append_stage_result(
    run_dir: Path,
    stage_name: str,
    status: str,
    *,
    payload: dict | None = None,
    error: str | None = None,
    started_at: str | None = None,
    finished_at: str | None = None,
) -> None:
    path = run_dir / "stage_results.json"
    data: dict = {}
    if path.exists():
        data = json.loads(path.read_text(encoding="utf-8"))
    stages = data.setdefault("stages", [])
    now = datetime.now().isoformat()
    stages.append(
        {
            "stage": stage_name,
            "status": status,
            "started_at": started_at or now,
            "finished_at": finished_at or now,
            "payload": payload or {},
            "error": error,
        }
    )
    data["updated_at"] = now
    atomic_write_json(path, data)


def write_failure_summary(
    run_dir: Path,
    *,
    failed_stage: str,
    error_category: str,
    error_message: str,
    election_id: str = "",
    period_start: str,
    period_end: str,
    facts_cutoff: str | None = None,
    poll_cutoff: str | None = None,
    active_snapshot_id: str | None = None,
    coverage_version: str | None = None,
    local_draft_generated: bool = False,
    artifact_generated: bool = False,
    delivery_attempted: bool = False,
    log_filename: str = "pipeline.log",
    suggested_actions: list[str] | None = None,
    suggested_action: str | None = None,
    alert_status: str = "not_attempted",
    data_context_resolution_error: str | None = None,
) -> Path:
    actions = list(suggested_actions or [])
    if suggested_action and suggested_action not in actions:
        actions.append(suggested_action)
    if not actions:
        actions.append("查看 failure_summary.json 与 pipeline.log 后重试")
    payload = {
        "failed_stage": failed_stage,
        "error_category": error_category,
        "error_message": error_message,
        "election_id": election_id,
        "period_start": period_start,
        "period_end": period_end,
        "facts_cutoff": facts_cutoff,
        "poll_cutoff": poll_cutoff,
        "active_snapshot_id": active_snapshot_id,
        "coverage_version": coverage_version,
        "local_draft_generated": local_draft_generated,
        "artifact_generated": artifact_generated,
        "delivery_attempted": delivery_attempted,
        "log_filename": log_filename,
        "suggested_actions": actions,
        "alert_status": alert_status,
        "data_context_resolution_error": data_context_resolution_error,
        "written_at": datetime.now().isoformat(),
    }
    path = run_dir / "failure_summary.json"
    atomic_write_json(path, payload)
    return path


def write_latest(pipeline_runs_root: Path, run_dir: Path, manifest: dict) -> Path:
    """原子写入 latest.json；只允许成功运行调用。"""
    if manifest.get("status") != "success":
        raise ValueError("latest.json 只能指向成功运行")
    path = pipeline_runs_root / "latest.json"
    try:
        run_rel = str(run_dir.relative_to(pipeline_runs_root))
    except ValueError:
        run_rel = str(run_dir)
    atomic_write_json(
        path,
        {
            "run_id": manifest.get("run_id"),
            "period_start": manifest.get("period_start"),
            "period_end": manifest.get("period_end"),
            "mode": manifest.get("mode"),
            "status": manifest.get("status"),
            "run_dir": run_rel,
            "updated_at": datetime.now().isoformat(),
        },
    )
    return path


def setup_pipeline_logger(run_dir: Path) -> logging.Logger:
    logger = logging.getLogger(f"assessment_pipeline.{run_dir.name}")
    logger.setLevel(logging.INFO)
    logger.handlers.clear()
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(message)s")
    fh = logging.FileHandler(run_dir / "pipeline.log", encoding="utf-8")
    fh.setFormatter(fmt)
    logger.addHandler(fh)
    return logger
