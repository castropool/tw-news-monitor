"""Daily review completion (contiguous reviewed-through boundary).

The completion state lives in the candidate operational DB.  A day may be
marked complete only when:
  - the candidate pipeline has ingested news through the end of that day, and
  - every candidate requiring a final human decision for that day is resolved.

Days are completed contiguously from the last completed day (or an explicit
start date); the boundary is never derived from latest_event_date.
"""

from __future__ import annotations

from datetime import date, datetime, time, timedelta
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


def last_completed_review_date(repo, election_id: str) -> date | None:
    """Latest day already marked complete for the election (or None)."""
    days = [
        _day(r["review_date"])
        for r in repo.list_daily_review_completions(election_id)
        if r.get("review_status") == "complete"
    ]
    days = [d for d in days if d is not None]
    return max(days) if days else None


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


def complete_review_through(
    repo,
    config,
    *,
    election_id: str,
    through_date: str | date,
    reviewer: str,
    from_date: str | date | None = None,
) -> dict[str, Any]:
    """Mark days after ``from_date`` (default: last completed day) through
    ``through_date`` as reviewed, contiguously, with fail-fast checks."""
    if not reviewer or reviewer.strip().lower() == "system":
        raise ValueError("reviewer is required and must not be 'system'")
    through = _day(through_date)
    if through is None:
        raise ValueError(f"invalid through date: {through_date}")
    current_cutoff = _day(from_date) if from_date else last_completed_review_date(repo, election_id)
    if current_cutoff is None:
        raise ValueError("no completed review days yet; pass --from explicitly")
    if through <= current_cutoff:
        raise ValueError("through date must be after the review start date")

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
    return {
        "completed_days": completed_days,
        "reviewed_from": current_cutoff.isoformat(),
        "reviewed_through": reviewed_through.isoformat() if reviewed_through else None,
        "idempotent": True,
    }
