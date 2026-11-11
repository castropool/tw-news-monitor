"""Snapshot candidate builder (deterministic fields + conservative analytical review)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any

from app.time_utils import TAIPEI


MILESTONE_TYPES = {"party_nomination", "primary_result", "candidate_announcement",
                   "alliance_agreement", "campaign_launch"}
ANALYTICAL_TRIGGER_TYPES = {"party_nomination", "faction_conflict", "alliance_proposal",
                            "alliance_coordination", "campaign_launch", "campaign_event",
                            "poll_release", "governance_event", "disaster_response",
                            "campaign_attack", "campaign_response", "party_integration",
                            "joint_campaign"}


def _load_active_snapshot(db_path: Path) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM election_state_snapshots WHERE snapshot_status='active' ORDER BY as_of DESC LIMIT 1"
    ).fetchone()
    conn.close()
    if not row:
        raise ValueError("no active snapshot")
    d = dict(row)
    if isinstance(d.get("state_json"), str):
        d["state_json"] = json.loads(d["state_json"])
    if isinstance(d.get("supporting_event_ids_json"), str):
        d["supporting_event_ids"] = json.loads(d["supporting_event_ids_json"])
    return d


def compute_snapshot_changes(
    *,
    previous_state: dict[str, Any],
    previous_supporting: list[str],
    previous_snapshot_id: str,
    new_event_ids: list[str],
    events_by_id: dict[str, dict[str, Any]],
    coverage: dict[str, Any],
    as_of: str,
    refresh_batch_id: str,
) -> dict[str, Any]:
    prev_milestones = list(previous_state.get("milestone_events", []))
    existing_ids = set(events_by_id)
    missing = [eid for eid in new_event_ids if eid not in existing_ids]
    if missing:
        raise ValueError(f"new_event_ids not found in formal state: {missing}")
    if not new_event_ids:
        return {
            "candidate_snapshot_id": None,
            "previous_snapshot_id": previous_snapshot_id,
            "effective_date": as_of,
            "dimensions": previous_state,
            "dimension_changes": [],
            "new_event_ids": [],
            "supporting_event_ids": sorted(previous_supporting),
            "supporting_poll_ids": [],
            "formal_state_hash": coverage.get("built_from_formal_state_hash", ""),
            "coverage_version": coverage.get("coverage_version", ""),
            "auto_activatable": False,
            "review_required": False,
            "review_reasons": [],
            "snapshot_change_required": False,
            "reason": "no new formal events",
        }
    new_milestones = [
        eid for eid in new_event_ids if events_by_id[eid]["event_type"] in MILESTONE_TYPES
    ]
    analytical_impact = [
        eid for eid in new_event_ids if events_by_id[eid]["event_type"] in ANALYTICAL_TRIGGER_TYPES
    ]
    dimension_changes: list[dict[str, Any]] = []
    milestone_events = sorted(set(prev_milestones) | set(new_milestones))
    supporting_event_ids = sorted(set(previous_supporting) | set(new_event_ids))
    if analytical_impact:
        dimension_changes.append({
            "dimension": "analytical_fields",
            "old_value": "previous",
            "new_value": "unchanged_pending_review",
            "change_type": "analytical_impact_pending_review",
            "supporting_event_ids": analytical_impact,
            "supporting_poll_ids": [],
            "rule_id": "phase3_analytical_review_guard",
            "confidence": "low",
            "auto_activatable": False,
        })
    if not new_milestones and not analytical_impact:
        return {
            "candidate_snapshot_id": None,
            "previous_snapshot_id": previous_snapshot_id,
            "effective_date": as_of,
            "dimensions": previous_state,
            "dimension_changes": [],
            "new_event_ids": sorted(new_event_ids),
            "supporting_event_ids": supporting_event_ids,
            "supporting_poll_ids": [],
            "formal_state_hash": coverage.get("built_from_formal_state_hash", ""),
            "coverage_version": coverage.get("coverage_version", ""),
            "auto_activatable": False,
            "review_required": False,
            "review_reasons": [],
            "snapshot_change_required": False,
            "reason": "no snapshot dimension affected",
            "refresh_batch_id": refresh_batch_id,
        }
    if new_milestones:
        dimension_changes.append({
            "dimension": "milestone_events",
            "old_value": prev_milestones,
            "new_value": milestone_events,
            "change_type": "deterministic_append",
            "supporting_event_ids": new_milestones,
            "supporting_poll_ids": [],
            "rule_id": "phase3_milestone_append",
            "confidence": "high",
            "auto_activatable": True,
        })
    review_required = bool(analytical_impact)
    auto_activatable = not review_required
    n = 1
    if previous_snapshot_id and "_v" in previous_snapshot_id:
        try:
            n = int(previous_snapshot_id.rsplit("_v", 1)[1]) + 1
        except ValueError:
            n = 1
    candidate_snapshot_id = f"tn_state_{as_of.replace('-', '')}_v{n}"
    new_state = dict(previous_state)
    new_state["milestone_events"] = milestone_events
    new_state["coverage"] = coverage
    return {
        "candidate_snapshot_id": candidate_snapshot_id,
        "previous_snapshot_id": previous_snapshot_id,
        "effective_date": as_of,
        "dimensions": new_state,
        "dimension_changes": dimension_changes,
        "new_event_ids": sorted(new_event_ids),
        "supporting_event_ids": supporting_event_ids,
        "supporting_poll_ids": [],
        "formal_state_hash": coverage.get("built_from_formal_state_hash", ""),
        "coverage_version": coverage.get("coverage_version", ""),
        "auto_activatable": auto_activatable,
        "review_required": review_required,
        "review_reasons": ["analytical_fields_require_human_review"] if review_required else [],
        "snapshot_change_required": True,
    }


def build_snapshot_candidate(
    config,
    *,
    refresh_batch_id: str,
    new_event_ids: list[str],
    coverage: dict[str, Any],
    as_of: str | None = None,
) -> dict[str, Any]:
    previous = _load_active_snapshot(config.path("formal_db"))
    prev_state = previous.get("state_json", {})

    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    events = {
        r["event_id"]: dict(r)
        for r in conn.execute("SELECT event_id, occurred_at, event_type, title FROM election_events")
    }
    conn.close()
    return compute_snapshot_changes(
        previous_state=prev_state,
        previous_supporting=list(previous.get("supporting_event_ids", [])),
        previous_snapshot_id=previous.get("snapshot_id", ""),
        new_event_ids=new_event_ids,
        events_by_id=events,
        coverage=coverage,
        as_of=as_of or datetime.now(TAIPEI).strftime("%Y-%m-%d"),
        refresh_batch_id=refresh_batch_id,
    )


def write_candidate(candidate: dict[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    p = out / "snapshot_candidate.json"
    p.write_text(json.dumps(candidate, ensure_ascii=False, indent=2), encoding="utf-8")
    return p
