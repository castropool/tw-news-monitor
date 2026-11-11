"""确定性快照状态差异比较（语义分层，不做政治推断、不调用大模型）。"""

from __future__ import annotations

import json
from typing import Any


DIMENSIONS = [
    "overall_race_structure",
    "chen_tingfei_integration",
    "hsieh_longchieh_organization",
    "kmt_tpp_cooperation",
    "poll_status",
    "governance_issues",
    "known_limitations",
]

IGNORED_METADATA_KEYS = {
    "generated_at",
    "updated_at",
    "superseded_at",
    "run_id",
    "created_at",
    "released_at",
    "as_of",
    "coverage_version",
    "version",
    "builder_version",
    "schema_version",
    "release_source_preview_id",
    "snapshot_id",
    "election_id",
    "requested_period_start",
    "requested_period_end",
    "latest_event_date",
    "coverage_status",
    "core_coverage_start",
}


def _strip_metadata(obj: Any) -> Any:
    if isinstance(obj, dict):
        return {
            k: _strip_metadata(v)
            for k, v in obj.items()
            if k not in IGNORED_METADATA_KEYS
        }
    if isinstance(obj, list):
        return [_strip_metadata(x) for x in obj]
    return obj


def _norm(v: Any) -> Any:
    v = _strip_metadata(v)
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in v.items()}
    if isinstance(v, list):
        return sorted(
            (_norm(x) for x in v),
            key=lambda z: json.dumps(z, ensure_ascii=False, sort_keys=True, default=str),
        )
    if v is None or v == "":
        return None
    return v


def _diff_paths(a: Any, b: Any, path: str = "") -> list[str]:
    a2, b2 = _strip_metadata(a), _strip_metadata(b)
    if isinstance(a2, dict) and isinstance(b2, dict):
        out: list[str] = []
        for k in sorted(set(a2) | set(b2)):
            child = f"{path}.{k}" if path else k
            if k not in a2 or k not in b2:
                out.append(child)
                continue
            out.extend(_diff_paths(a2[k], b2[k], child))
        return out
    if isinstance(a2, list) and isinstance(b2, list):
        return [] if _norm(a2) == _norm(b2) else [path or "."]
    if _norm(a2) != _norm(b2):
        return [path or "."]
    return []


def _status_text(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, default=str)


def _dimension_parts(snapshot: dict, dimension: str) -> dict:
    if dimension == "overall_race_structure":
        sl = snapshot.get("structural_lean") or {}
        comp = snapshot.get("competitiveness") or {}
        return {
            "business": {"structural_lean": sl.get("value"), "competitiveness": comp.get("value")},
            "evidence": {
                "events": sorted(sl.get("supporting_event_ids") or [])
                + sorted(comp.get("supporting_event_ids") or []),
                "polls": sorted(sl.get("supporting_poll_ids") or [])
                + sorted(comp.get("supporting_poll_ids") or []),
            },
            "limitations": sorted(
                set((sl.get("limitations") or []) + (comp.get("limitations") or []))
            ),
            "confidence": {
                "structural_lean": sl.get("confidence"),
                "competitiveness": comp.get("confidence"),
            },
        }
    if dimension == "chen_tingfei_integration":
        d = snapshot.get("dpp_integration") or {}
        return {
            "business": {
                "formal_status": d.get("formal_status"),
                "organizational_status": d.get("organizational_status"),
            },
            "evidence": {
                "events": sorted(d.get("supporting_event_ids") or []),
                "polls": sorted(d.get("supporting_poll_ids") or []),
            },
            "limitations": sorted(d.get("limitations") or []),
            "confidence": {"dpp_integration": d.get("confidence")},
        }
    if dimension == "hsieh_longchieh_organization":
        d = snapshot.get("kmt_organization") or {}
        return {
            "business": {"status": d.get("status")},
            "evidence": {
                "events": sorted(d.get("supporting_event_ids") or []),
                "polls": sorted(d.get("supporting_poll_ids") or []),
            },
            "limitations": sorted(d.get("limitations") or []),
            "confidence": {"kmt_organization": d.get("confidence")},
        }
    if dimension == "kmt_tpp_cooperation":
        d = snapshot.get("kmt_tpp_cooperation") or {}
        return {
            "business": {"status": d.get("status"), "formal_agreement": d.get("formal_agreement")},
            "evidence": {
                "events": sorted(d.get("supporting_event_ids") or []),
                "polls": sorted(d.get("supporting_poll_ids") or []),
            },
            "limitations": sorted(d.get("limitations") or []),
            "confidence": {"kmt_tpp_cooperation": d.get("confidence")},
        }
    if dimension == "poll_status":
        pa = snapshot.get("public_poll_assessment") or {}
        pe = snapshot.get("polling_evidence") or {}
        return {
            "business": {
                "status": pa.get("status"),
                "latest_field_end": pe.get("latest_field_end"),
                "poll_count": pe.get("poll_count"),
            },
            "evidence": {
                "events": [],
                "polls": sorted(
                    (pa.get("supporting_poll_ids") or []) + (pe.get("supporting_poll_ids") or [])
                ),
            },
            "limitations": sorted(pa.get("limitations") or []),
            "confidence": {"public_poll_assessment": pa.get("confidence")},
        }
    if dimension == "governance_issues":
        issues = [
            {"issue": x.get("issue"), "status": x.get("status")}
            for x in (snapshot.get("core_issues") or [])
        ]
        return {
            "business": {"core_issues": issues},
            "evidence": {
                "events": sorted(
                    e
                    for x in (snapshot.get("core_issues") or [])
                    for e in (x.get("supporting_event_ids") or [])
                ),
                "polls": [],
            },
            "limitations": [],
            "confidence": {},
        }
    if dimension == "known_limitations":
        gaps = list((snapshot.get("coverage") or {}).get("known_gaps") or [])
        return {
            "business": {"known_gaps": gaps},
            "evidence": {"events": [], "polls": []},
            "limitations": gaps,
            "confidence": {},
        }
    return {"business": snapshot.get(dimension), "evidence": {}, "limitations": [], "confidence": {}}


def _meaningful_confidence_change(prev: dict, cur: dict) -> bool:
    for key in set(prev) | set(cur):
        a, b = prev.get(key), cur.get(key)
        if isinstance(a, (int, float)) and isinstance(b, (int, float)):
            if abs(float(a) - float(b)) >= 0.05:
                return True
    return False


def snapshot_supporting_ids(snapshot: dict) -> tuple[set[str], set[str]]:
    event_ids: set[str] = set()
    poll_ids: set[str] = set()
    for eid in (snapshot.get("supporting_event_ids") or []):
        if eid:
            event_ids.add(eid)
    for pid in (snapshot.get("supporting_poll_ids") or []):
        if pid:
            poll_ids.add(pid)
    for section in (
        "structural_lean",
        "competitiveness",
        "dpp_integration",
        "kmt_organization",
        "kmt_tpp_cooperation",
        "public_poll_assessment",
    ):
        obj = snapshot.get(section) or {}
        for eid in (obj.get("supporting_event_ids") or []):
            if eid:
                event_ids.add(eid)
        for pid in (obj.get("supporting_poll_ids") or []):
            if pid:
                poll_ids.add(pid)
    pe = snapshot.get("polling_evidence") or {}
    for pid in (pe.get("supporting_poll_ids") or []):
        if pid:
            poll_ids.add(pid)
    return event_ids, poll_ids


def _dimension_change(current: dict, previous: dict, dimension: str) -> dict:
    cur = _dimension_parts(current, dimension)
    prev = _dimension_parts(previous, dimension)

    business_changed = _norm(cur["business"]) != _norm(prev["business"])
    evidence_changed = _norm(cur["evidence"]) != _norm(prev["evidence"])
    limitations_changed = _norm(cur["limitations"]) != _norm(prev["limitations"])
    confidence_changed = _norm(cur["confidence"]) != _norm(prev["confidence"])
    meaningful_conf = _meaningful_confidence_change(prev["confidence"], cur["confidence"])

    scope: list[str] = []
    if business_changed:
        scope.append("business_state")
    if evidence_changed:
        scope.append("evidence_support")
    if limitations_changed:
        scope.append("limitations")
    if confidence_changed:
        scope.append("confidence")

    changed_paths = _diff_paths(prev, cur, dimension)
    metadata_only = not scope and bool(changed_paths)
    if metadata_only:
        scope.append("metadata_only")
    change_status = "changed" if scope and not metadata_only else "unchanged"
    material = (business_changed or meaningful_conf) and dimension != "known_limitations"

    prev_evidence = prev["evidence"]
    cur_evidence = cur["evidence"]
    ev_add = sorted(
        [f"event:{e}" for e in set(cur_evidence.get("events") or []) - set(prev_evidence.get("events") or [])]
        + [f"poll:{p}" for p in set(cur_evidence.get("polls") or []) - set(prev_evidence.get("polls") or [])]
    )
    ev_rem = sorted(
        [f"event:{e}" for e in set(prev_evidence.get("events") or []) - set(cur_evidence.get("events") or [])]
        + [f"poll:{p}" for p in set(prev_evidence.get("polls") or []) - set(cur_evidence.get("polls") or [])]
    )
    lim_add = sorted(set(cur["limitations"]) - set(prev["limitations"]))
    lim_rem = sorted(set(prev["limitations"]) - set(cur["limitations"]))

    material_summary = ""
    if material:
        material_summary = (
            f"business: {_status_text(prev['business'])} -> {_status_text(cur['business'])}"
        )
        if meaningful_conf:
            material_summary += "; confidence 变化达到有意义阈值(>=0.05)"
    evidence_summary = (
        f"additions={ev_add}; removals={ev_rem}" if (ev_add or ev_rem) else ""
    )
    limitations_summary = (
        f"added={lim_add}; removed={lim_rem}" if (lim_add or lim_rem) else ""
    )

    return {
        "dimension": dimension,
        "previous_status": _status_text(prev["business"]),
        "current_status": _status_text(cur["business"]),
        "change_status": change_status,
        "change_scope": scope,
        "changed_paths": changed_paths,
        "material_for_report": material,
        "material_change_summary": material_summary,
        "evidence_only_change_summary": evidence_summary,
        "limitations_change_summary": limitations_summary,
    }


def diff_snapshots(current: dict | None, previous: dict | None) -> dict:
    """Compare two snapshots deterministically with semantic layering."""
    if current is None:
        raise ValueError("current snapshot must not be None")
    if previous is None:
        return {
            "status": "initial_baseline",
            "state_diff_mode": "initial_baseline",
            "dimensions": [
                {
                    "dimension": dim,
                    "previous_status": "",
                    "current_status": _status_text(_dimension_parts(current, dim)["business"]),
                    "change_status": "unchanged",
                    "change_scope": [],
                    "changed_paths": [],
                    "material_for_report": False,
                    "material_change_summary": "",
                    "evidence_only_change_summary": "",
                    "limitations_change_summary": "",
                }
                for dim in DIMENSIONS
            ],
            "changed_dimensions": [],
            "unchanged_dimensions": DIMENSIONS,
            "material_dimensions": [],
            "confidence_changes": [],
            "snapshot_evidence_reference_additions": [],
            "snapshot_evidence_reference_removals": [],
            "formal_records_added": [],
            "formal_records_removed": [],
            "new_risks": [],
            "risk_changes": [],
        }

    dimensions = [_dimension_change(current, previous, dim) for dim in DIMENSIONS]
    changed = [d["dimension"] for d in dimensions if d["change_status"] == "changed"]
    unchanged = [d["dimension"] for d in dimensions if d["change_status"] == "unchanged"]
    material = [d["dimension"] for d in dimensions if d["material_for_report"]]

    confidence_changes = []
    for d in dimensions:
        scope = d["change_scope"]
        if "confidence" in scope and "business_state" in scope:
            confidence_changes.append(
                {"dimension": d["dimension"], "change_summary": d["material_change_summary"]}
            )

    cur_events, cur_polls = snapshot_supporting_ids(current)
    prev_events, prev_polls = snapshot_supporting_ids(previous)
    ref_add = sorted(
        [f"event:{e}" for e in cur_events - prev_events]
        + [f"poll:{p}" for p in cur_polls - prev_polls]
    )
    ref_rem = sorted(
        [f"event:{e}" for e in prev_events - cur_events]
        + [f"poll:{p}" for p in prev_polls - cur_polls]
    )

    return {
        "status": "changed" if changed else "unchanged",
        "state_diff_mode": "structured_comparison",
        "dimensions": dimensions,
        "changed_dimensions": changed,
        "unchanged_dimensions": unchanged,
        "material_dimensions": material,
        "confidence_changes": confidence_changes,
        "snapshot_evidence_reference_additions": ref_add,
        "snapshot_evidence_reference_removals": ref_rem,
        "formal_records_added": [],
        "formal_records_removed": [],
        "new_risks": [],
        "risk_changes": [],
    }
