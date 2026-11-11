"""Explicit candidate review/publication state machine."""

from __future__ import annotations

from typing import Any


TRANSITIONS: dict[str, set[str]] = {
    "new": {"under_review", "review_required"},
    "review_required": {"under_review", "hold"},
    "hold": {"under_review"},
    "context_only": {"under_review", "review_rejected"},
    "duplicate_candidate": {"under_review", "review_rejected"},
    "auto_reject": {"under_review", "review_rejected"},
    "under_review": {"review_approved", "review_rejected", "hold"},
    "review_approved": {"publication_prepared", "under_review"},
    "review_rejected": {"under_review"},
    "publication_prepared": {"published", "publication_failed", "under_review"},
    "publication_failed": {"publication_prepared", "under_review"},
    "published": {"rolled_back", "under_review"},
    "rolled_back": {"under_review"},
}


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, set())


def assert_transition(current: str, target: str):
    if not can_transition(current, target):
        raise ValueError(
            f"illegal status transition: {current} -> {target}"
        )


def apply_status(repo, candidate_id: str, target: str, updated_run_id: str = "review"):
    candidate = repo.get_candidate(candidate_id)
    if not candidate:
        raise ValueError(f"candidate not found: {candidate_id}")
    current = candidate["review_status"]
    assert_transition(current, target)
    candidate["review_status"] = target
    candidate["status_reason_codes_json"] = (
        __import__("json").dumps([f"state_transition:{current}->{target}"], ensure_ascii=False)
    )
    candidate["updated_run_id"] = updated_run_id
    repo.upsert_candidate(candidate, preserve_terminal_status=False)
    return candidate
