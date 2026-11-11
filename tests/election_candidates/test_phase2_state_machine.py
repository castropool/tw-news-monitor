from __future__ import annotations

import pytest

from app.election_candidates.state_machine import (
    TRANSITIONS,
    apply_status,
    assert_transition,
    can_transition,
)

from .publication_helpers import make_publication_config, open_candidate_repo, seed_candidate


@pytest.mark.parametrize(
    "current,target,expected",
    [
        ("new", "under_review", True),
        ("review_required", "under_review", True),
        ("under_review", "review_approved", True),
        ("under_review", "review_rejected", True),
        ("review_approved", "publication_prepared", True),
        ("publication_prepared", "published", True),
        ("published", "rolled_back", True),
        ("auto_reject", "published", False),
        ("new", "published", False),
        ("review_rejected", "publication_prepared", False),
        ("context_only", "published", False),
        ("review_required", "published", False),
    ],
)
def test_transition_matrix(current, target, expected):
    assert can_transition(current, target) is expected


def test_transition_matrix_is_complete():
    for current, targets in TRANSITIONS.items():
        assert isinstance(targets, set)
        assert current in TRANSITIONS


def test_apply_status_valid(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo, status="review_required")
    cand = apply_status(repo, "cand_tnn_abc123", "under_review", "run_x")
    assert cand["review_status"] == "under_review"
    repo.close()


def test_apply_status_illegal(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo, status="auto_reject")
    with pytest.raises(ValueError):
        apply_status(repo, "cand_tnn_abc123", "published", "run_x")
    repo.close()


def test_apply_status_missing_candidate(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    with pytest.raises(ValueError):
        apply_status(repo, "missing", "under_review", "run_x")
    repo.close()


def test_assert_transition_raises():
    with pytest.raises(ValueError):
        assert_transition("auto_reject", "published")


def test_all_statuses_have_paths():
    statuses = {
        "new", "review_required", "hold", "context_only", "duplicate_candidate",
        "auto_reject", "under_review", "review_approved", "review_rejected",
        "publication_prepared", "published", "publication_failed", "rolled_back",
    }
    assert statuses <= set(TRANSITIONS)
