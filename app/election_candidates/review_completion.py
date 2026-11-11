"""Daily review completion and contiguous facts_cutoff advance (Phase F1 C).

The completion state lives in the candidate operational DB, never in the
formal fact schema.  A day may be marked complete only when:
  - the candidate pipeline has ingested news through the end of that day, and
  - every candidate requiring a final human decision for that day is resolved.

facts_cutoff is advanced only as a contiguous reviewed-through boundary, and
is never derived from latest_event_date.
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, time, timedelta
from pathlib import Path
from typing import Any

from app.time_utils import TAIPEI


# Statuses that count as "final human decision made".
RESOLVED_STATUSES = frozenset({
    "auto_reject",
    "context_only",
    "duplicate_candidate",
    "review_rejected",
    "review_approved",
    "publication_prepared",
    "publication_failed",
    "published",
    "rolled_back",
})

# Statuses that still need a human decision and therefore block completion.
UNRESOLVED_STATUSES = frozenset({
    "new",
    "review_required",
    "hold",
    "needs_edit",
    "under_review",
})

# Statuses that represent an approved material event for that day.
APPROVED_FAMILY = frozenset({
    "review_approved",
    "publication_prepared",
    "publication_failed",
    "published",
    "rolled_back",
})


def _latest_coverage_dir(coverage_root: Path) -> Path | None:
    """Latest coverage version dir, e.g. fact_coverage_20260811_v005.

    Historical coverage versions are frozen; only the latest version carries
    the authoritative facts_cutoff.
    """
    candidates = [
        p
        for p in coverage_root.iterdir()
        if p.is_dir() and re.fullmatch(r"fact_coverage_\d{8}_v\d+", p.name)
    ]
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.name)


def _day(value: str | date | None) -> date | None:
    if isinstance(value, date):
        return value
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value)[:19]).date()
    except ValueError:
        try:
            return date.fromisoformat(str(value)[:10])
        except ValueError:
            return None


def _end_of_day(d: date) -> str:
    return datetime.combine(d, time(23, 59, 59, 999999)).isoformat()


def ingestion_covered_through(config, repo, election_id: str, d: date) -> tuple[bool, str]:
    """True when the candidate pipeline has processed news to end of day d."""
    cursor = repo.get_scan_cursor(
        election_id, config.get("scan.cursor_type", "news_article_id")
    )
    if not cursor:
        return False, "no_scan_cursor"
    latest = cursor.get("last_published_at") or ""
    if not latest:
        return False, "cursor_no_published_at"
    if _day(latest) is None:
        return False, "cursor_invalid_published_at"
    if latest >= _end_of_day(d):
        return True, "cursor_covers_day"
    return False, "cursor_before_day_end"


def day_review_summary(repo, election_id: str, d: date) -> dict[str, Any]:
    ds = d.isoformat()
    candidates = [
        c for c in repo.list_candidates(limit=100000)
        if (_day(c.get("canonical_event_date")) or date.min) == d
    ]
    statuses = [c["review_status"] for c in candidates]
    unresolved_statuses = sorted(s for s in statuses if s in UNRESOLVED_STATUSES)
    unresolved_count = len(unresolved_statuses)
    resolved_count = sum(1 for s in statuses if s in RESOLVED_STATUSES)
    unknown_count = len(statuses) - resolved_count - unresolved_count
    material_event_count = sum(1 for s in statuses if s in APPROVED_FAMILY)
    return {
        "review_date": ds,
        "election_id": election_id,
        "candidate_total": len(candidates),
        "resolved_count": resolved_count,
        "unresolved_count": unresolved_count,
        "unknown_status_count": unknown_count,
        "material_event_count": material_event_count,
        "no_material_event": material_event_count == 0,
        "unresolved_statuses": unresolved_statuses,
        "status_counts": {s: statuses.count(s) for s in sorted(set(statuses))},
    }


def current_facts_cutoff(config) -> str | None:
    """Authoritative facts_cutoff from coverage preflight / production status."""
    coverage_root = Path(config.get("paths.coverage_root", "data/election_seed/tainan_2026"))
    latest_dir = _latest_coverage_dir(coverage_root)
    preflight_paths = []
    if latest_dir is not None:
        p = latest_dir / "coverage_preflight.json"
        if p.exists():
            preflight_paths.append(p)
    else:
        preflight_paths = sorted(coverage_root.rglob("coverage_preflight.json"))
    for p in preflight_paths:
        try:
            v = json.loads(p.read_text(encoding="utf-8")).get("facts_cutoff")
            if v:
                return str(v)
        except Exception:
            continue
    status = config.root / "data" / "production" / "production_status.json"
    if status.exists():
        try:
            v = json.loads(status.read_text(encoding="utf-8")).get("coverage", {}).get("facts_cutoff")
            if v:
                return str(v)
        except Exception:
            pass
    return None


def compute_reviewed_through(repo, election_id: str, from_date: date) -> date | None:
    """Max contiguous date after from_date with a completion record."""
    completed = {
        _day(r["review_date"])
        for r in repo.list_daily_review_completions(election_id)
        if r.get("review_status") == "complete"
    }
    cursor = from_date
    while (cursor + timedelta(days=1)) in completed:
        cursor = cursor + timedelta(days=1)
    return cursor if cursor > from_date else None


def facts_cutoff_for_refresh(repo, config, election_id: str) -> str | None:
    """facts_cutoff to carry into downstream refresh (reviewed-through, never regress)."""
    current = current_facts_cutoff(config)
    base = _day(current)
    if base is None:
        return None
    rt = compute_reviewed_through(repo, election_id, base)
    return rt.isoformat() if rt else base.isoformat()


def complete_review_through(
    repo,
    config,
    *,
    election_id: str,
    through_date: str | date,
    reviewer: str,
    from_date: str | date | None = None,
    update_facts_cutoff: bool = False,
    provenance: str = "review_completion",
    status_path: str | Path | None = None,
) -> dict[str, Any]:
    if not reviewer or reviewer.strip().lower() == "system":
        raise ValueError("reviewer is required and must not be 'system'")
    through = _day(through_date)
    if through is None:
        raise ValueError(f"invalid through date: {through_date}")
    current_cutoff = _day(from_date) if from_date else _day(current_facts_cutoff(config))
    if current_cutoff is None:
        raise ValueError("no authoritative facts_cutoff; pass --from explicitly")
    if through <= current_cutoff:
        raise ValueError("through date must be after current facts_cutoff")

    completed_days: list[dict[str, Any]] = []
    cursor = repo.get_scan_cursor(election_id, config.get("scan.cursor_type", "news_article_id"))
    cursor_at_completion = int(cursor["last_article_id"] or 0) if cursor else 0

    d = current_cutoff + timedelta(days=1)
    while d <= through:
        covered, reason = ingestion_covered_through(config, repo, election_id, d)
        if not covered:
            raise ValueError(
                f"cannot complete {d.isoformat()}: ingestion not covered ({reason})"
            )
        summary = day_review_summary(repo, election_id, d)
        if summary["unresolved_count"] > 0:
            raise ValueError(
                f"cannot complete {d.isoformat()}: unresolved candidates "
                f"{summary['unresolved_statuses']}"
            )
        row = {
            "election_id": election_id,
            "review_date": d.isoformat(),
            "review_status": "complete",
            "completed_at": datetime.now(TAIPEI).isoformat(),
            "completed_by": reviewer,
            "candidate_total": summary["candidate_total"],
            "resolved_count": summary["resolved_count"],
            "unresolved_count": 0,
            "material_event_count": summary["material_event_count"],
            "no_material_event": int(summary["no_material_event"]),
            "candidate_cursor_at_completion": cursor_at_completion,
            "business_hash": repo.business_output_hash(),
        }
        repo.upsert_daily_review_completion(row)
        completed_days.append({**row, "unresolved_statuses": summary["unresolved_statuses"]})
        d += timedelta(days=1)

    reviewed_through = compute_reviewed_through(repo, election_id, current_cutoff)
    cutoff_applied = False
    facts_cutoff_after = current_cutoff.isoformat()
    if reviewed_through is not None and reviewed_through > current_cutoff:
        facts_cutoff_after = reviewed_through.isoformat()
        if update_facts_cutoff:
            apply_result = advance_facts_cutoff(
                config,
                facts_cutoff_after,
                provenance=provenance,
                status_path=status_path,
                allow_write=True,
            )
            cutoff_applied = bool(apply_result.get("applied"))
    return {
        "completed_days": completed_days,
        "reviewed_through": reviewed_through.isoformat() if reviewed_through else None,
        "facts_cutoff_before": current_cutoff.isoformat(),
        "facts_cutoff_after": facts_cutoff_after,
        "facts_cutoff_applied": cutoff_applied,
        "idempotent": True,
    }


def advance_facts_cutoff(
    config,
    new_cutoff: str | date,
    *,
    provenance: str = "review_completion",
    allow_write: bool = False,
    status_path: str | Path | None = None,
) -> dict[str, Any]:
    """Update authoritative facts_cutoff files (preflight/validation/status)."""
    if not (allow_write or config.test_mode):
        return {"applied": False, "reason": "facts_cutoff write not authorized"}
    cutoff = new_cutoff.isoformat() if isinstance(new_cutoff, date) else str(new_cutoff)
    updated: list[str] = []
    coverage_root = Path(config.get("paths.coverage_root", "data/election_seed/tainan_2026"))
    if config.test_mode and coverage_root.resolve().is_relative_to(config.root.resolve()):
        return {
            "applied": False,
            "reason": "test mode must not write coverage under the project root",
        }
    latest_dir = _latest_coverage_dir(coverage_root)
    target_dirs = [latest_dir] if latest_dir is not None else [coverage_root]
    for name in ("coverage_preflight.json", "coverage_validation.json"):
        for d in target_dirs:
            p = d / name
            if not p.exists():
                continue
            try:
                payload = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(payload, dict) and "facts_cutoff" in payload:
                    payload["facts_cutoff"] = cutoff
                    if provenance:
                        payload["facts_cutoff_provenance"] = provenance
                    p.write_text(
                        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    updated.append(str(p))
            except Exception:
                continue
    statuses: list[Path] = []
    if status_path is not None:
        statuses.append(Path(status_path))
    elif not config.test_mode:
        default_status = config.root / "data" / "production" / "production_status.json"
        if default_status.exists():
            statuses.append(default_status)
    for status in statuses:
        try:
            payload = json.loads(status.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and isinstance(payload.get("coverage"), dict):
                payload["coverage"]["facts_cutoff"] = cutoff
                status.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
                )
                updated.append(str(status))
        except Exception:
            continue
    applied = bool(updated)
    result = {
        "applied": applied,
        "facts_cutoff": cutoff,
        "provenance": provenance,
        "updated": updated,
    }
    if not applied:
        result["reason"] = "no authoritative facts_cutoff file updated"
    return result
