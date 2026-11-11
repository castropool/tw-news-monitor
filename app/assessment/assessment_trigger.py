"""Assessment trigger: decide whether/when a report should be generated.

The trigger only decides; it never writes a report. Report generation reuses the
existing assessment pipeline (app.assessment.run_assessment_pipeline or the mock
entry used in development/dry-run mode).
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any

from app.time_utils import TAIPEI

from app.assessment.reporting_period import scheduled_period_for


def run_days_from_config(config) -> tuple[int, ...]:
    days = config.get("schedule.run_days", [9, 22]) if hasattr(config, "get") else [9, 22]
    return tuple(int(d) for d in days)


def compute_reporting_period(run_date: date, config) -> tuple[date, date]:
    """Reuse the existing scheduled_period_for helper.

    - On a scheduled day (9/22): use the exact existing rule.
    - On other days: project to the next scheduled day so the period is the
      half-month this publication belongs to (1-15 -> month 22; 16-end ->
      next month 9, i.e. this month 16-end).
    """
    days = run_days_from_config(config)
    if run_date.day in days:
        return scheduled_period_for(run_date, days)
    if run_date.day <= 15:
        anchor = run_date.replace(day=22)
    else:
        if run_date.month == 12:
            anchor = date(run_date.year + 1, 1, 9)
        else:
            anchor = date(run_date.year, run_date.month + 1, 9)
    return scheduled_period_for(anchor, days)


def create_trigger(
    repo,
    config,
    *,
    refresh_batch_id: str,
    formal_state_hash: str,
    coverage_manifest: dict[str, Any],
    snapshot_id: str,
    run_date: date | None = None,
    manual: bool = False,
) -> dict[str, Any]:
    if isinstance(run_date, str):
        run_date = date.fromisoformat(run_date)
    run_date = run_date or datetime.now(TAIPEI).date()
    days = run_days_from_config(config)
    is_report_day = run_date.day in days or manual
    period_start, period_end = compute_reporting_period(run_date, config)
    trigger_id = "trg_" + hashlib.sha256(
        f"{config.canonical_election_id}|{period_start}|{period_end}|{formal_state_hash}".encode("utf-8")
    ).hexdigest()[:16]
    now = datetime.now(TAIPEI).isoformat()
    coverage_version = coverage_manifest.get("coverage_version", "")
    facts_cutoff = coverage_manifest.get("facts_cutoff", "")
    if is_report_day:
        eligible = bool(facts_cutoff and facts_cutoff >= period_end.isoformat())
        status = "eligible" if eligible else "pending"
        reason = (
            "report_day_coverage_full"
            if eligible
            else "report_day_coverage_insufficient_draft_allowed"
        )
    else:
        eligible = False
        status = "pending"
        reason = "not_reporting_day"
    trigger = {
        "trigger_id": trigger_id,
        "election_id": config.canonical_election_id,
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "refresh_batch_id": refresh_batch_id,
        "formal_state_hash": formal_state_hash,
        "coverage_version": coverage_version,
        "facts_cutoff": facts_cutoff,
        "snapshot_id": snapshot_id,
        "trigger_reason": reason,
        "status": status,
        "created_at": now,
        "eligible_at": now if eligible else "",
        "started_at": "",
        "finished_at": "",
        "assessment_run_id": "",
        "error_summary": "",
    }
    existing = repo.get_trigger(trigger_id)
    if existing and existing.get("status") != "superseded":
        return {**existing, **trigger, "status": existing["status"]}
    repo.insert_trigger(trigger)
    repo.supersede_triggers(
        config.canonical_election_id,
        period_start.isoformat(),
        period_end.isoformat(),
        trigger_id,
    )
    return trigger


def _cache_key(trigger: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(
            {
                "formal_state_hash": trigger.get("formal_state_hash"),
                "coverage_version": trigger.get("coverage_version"),
                "snapshot_id": trigger.get("snapshot_id"),
                "provider": "mock",
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()


def run_mock_assessment(
    config,
    trigger: dict[str, Any],
    out_dir: str | Path,
    *,
    network_calls: int = 0,
) -> dict[str, Any]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    key = _cache_key(trigger)
    cache_dir = out / "cache"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / f"{key}.json"
    if cached.exists():
        report = json.loads(cached.read_text(encoding="utf-8"))
        report["cached"] = True
        return report
    period_end = trigger.get("period_end", "")
    facts_cutoff = trigger.get("facts_cutoff", "")
    generation_mode = "final" if trigger.get("status") == "eligible" else "draft_with_data_gap"
    report = {
        "assessment_run_id": "mock_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:16],
        "trigger_id": trigger["trigger_id"],
        "generation_mode": generation_mode,
        "final_report_allowed": generation_mode == "final",
        "sections": [
            {
                "section": "overall_judgment",
                "content": "（Mock）基于正式事实底表的结构化摘要。",
                "claim_evidence": [],
            },
            {
                "section": "tainan",
                "content": "（Mock）台南部分。",
                "claim_evidence": [],
            },
            {
                "section": "new_taipei",
                "content": "（Mock）新北部分。",
                "claim_evidence": [],
            },
        ],
        "claim_evidence": [
            {
                "claim": f"正式事件覆盖至{facts_cutoff}",
                "evidence": ["coverage_manifest"],
            }
        ],
        "cached": False,
    }
    cached.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    delivery = {
        "delivery_provider": "mock",
        "network_calls": network_calls,
        "status": "requested",
        "delivery_requested_at": datetime.now(TAIPEI).isoformat(),
    }
    (out / "delivery_request.json").write_text(
        json.dumps(delivery, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "mock_report.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return report
