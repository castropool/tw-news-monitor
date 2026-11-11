"""Auto-review orchestrator: conservative auto-publish + automatic facts_cutoff
advance (2026-09-03 production enablement).

Design contract:
- Step 1 runs the existing conservative ``auto_publish_candidates`` runner
  (low risk + direct_event + explicit-or-inferred date + resolved sources +
  no duplicate + no approve history).  Any candidate outside that subset stays
  in the human review queue untouched.
- Step 2 advances facts_cutoff automatically for the longest contiguous run of
  days that are fully ingested (scan cursor covers end-of-day) AND have zero
  unresolved candidates.  Unresolved candidates (review_required / hold /
  needs_edit / under_review / new) stop the advance at that day; the human
  queue remains the fallback.
- Reviewer for completion rows is the machine principal
  ``auto_approver_v1`` (from ``auto_publish.auto_approver``); it is not
  "system" so no code guard is bypassed.
- Fully idempotent: auto-publish has its own manifest idempotency key and
  daily completion rows are upserts; repeated runs only advance what is new.
- Never relaxes gates and never resolves a candidate the policy does not
  cover; this is "conservative automation + human fallback", not zero-human.
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.time_utils import TAIPEI

from .auto_publish_candidates import run_auto_publish
from .candidate_repository import CandidateRepository
from .config import load_config
from .review_completion import (
    complete_review_through,
    current_facts_cutoff,
    day_review_summary,
    ingestion_covered_through,
)


def _as_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def find_completable_through(
    repo,
    config,
    election_id: str,
    from_date: str | None = None,
) -> dict[str, Any]:
    """Longest contiguous reviewed-through boundary that can be completed now.

    A day is completable when ingestion has covered its end AND it has no
    unresolved candidates.  Returns the boundary date and per-day diagnostics.
    """
    cutoff = _as_date(from_date) or _as_date(current_facts_cutoff(config))
    if cutoff is None:
        return {"completable_through": None, "cutoff": None, "blocked_days": []}
    blocked: list[dict[str, Any]] = []
    probe = cutoff + timedelta(days=1)
    max_probe = cutoff + timedelta(days=45)
    last_completable = cutoff
    while probe <= max_probe:
        covered, reason = ingestion_covered_through(config, repo, election_id, probe)
        if not covered:
            blocked.append(
                {"day": probe.isoformat(), "reason": f"ingestion_not_covered:{reason}"}
            )
            break
        summary = day_review_summary(repo, election_id, probe)
        if summary["unresolved_count"] > 0:
            blocked.append(
                {
                    "day": probe.isoformat(),
                    "reason": "unresolved_candidates",
                    "unresolved_statuses": summary["unresolved_statuses"],
                    "unresolved_count": summary["unresolved_count"],
                }
            )
            break
        last_completable = probe
        probe += timedelta(days=1)
    return {
        "completable_through": last_completable.isoformat(),
        "cutoff": cutoff.isoformat(),
        "blocked_days": blocked,
        "has_new_days": last_completable > cutoff,
    }


def run_orchestrator(config_path: str | Path, args) -> dict[str, Any]:
    config = load_config(config_path)
    election_id = config.resolve_election_id(args.election_id)

    # Step 1: conservative auto-publish (no-op when nothing eligible).
    auto_args = argparse.Namespace(
        check_only=getattr(args, "check_only", False),
        election_id=election_id,
        candidate_db=getattr(args, "candidate_db", None),
        output_root=None,
        skip_downstream=bool(getattr(args, "skip_downstream", False)),
    )
    auto = run_auto_publish(config, auto_args)

    # Step 2: automatic facts_cutoff advance for fully-clear contiguous days.
    repo = CandidateRepository(args.candidate_db or config.path("candidate_db"))
    repo.connect()
    repo.create_tables()
    try:
        boundary = find_completable_through(repo, config, election_id)
        completion: dict[str, Any] = {"attempted": False}
        check_only = bool(getattr(args, "check_only", False))
        if not check_only and boundary.get("has_new_days") and boundary.get("completable_through"):
            reviewer = str(config.get("auto_publish.auto_approver", "auto_approver_v1"))
            completion = complete_review_through(
                repo,
                config,
                election_id=election_id,
                through_date=boundary["completable_through"],
                reviewer=reviewer,
                from_date=None,
                update_facts_cutoff=True,
            )
        return {
            "status": "completed",
            "election_id": election_id,
            "auto_publish": {
                k: auto.get(k)
                for k in (
                    "status", "evaluated", "eligible", "rejected", "published",
                    "failed", "skipped", "circuit_open", "stop_reason",
                )
                if k in auto
            },
            "complete_review": {
                "attempted": completion.get("attempted", False),
                "completed_days": completion.get("completed_days", []),
                "reviewed_through": completion.get("reviewed_through"),
                "facts_cutoff_before": completion.get("facts_cutoff_before", boundary.get("cutoff")),
                "facts_cutoff_after": completion.get("facts_cutoff_after", boundary.get("cutoff")),
                "facts_cutoff_applied": completion.get("facts_cutoff_applied", False),
                "blocked_days": boundary.get("blocked_days", []),
                "next_blocker": boundary.get("blocked_days", [{}])[0]
                if boundary.get("blocked_days") else None,
            },
            "timestamp": datetime.now(TAIPEI).isoformat(),
        }
    finally:
        repo.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Auto-review orchestrator: conservative auto-publish + automatic facts_cutoff advance"
    )
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--election-id", default=None)
    parser.add_argument("--candidate-db", default=None)
    parser.add_argument("--check-only", action="store_true",
                        help="passthrough to auto_publish: evaluate only, never write")
    parser.add_argument("--skip-downstream", action="store_true",
                        help="passthrough to auto_publish (test escape hatch only)")
    args = parser.parse_args()
    result = run_orchestrator(args.config, args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
