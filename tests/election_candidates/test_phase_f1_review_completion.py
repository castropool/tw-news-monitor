from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

from app.election_candidates.review_completion import (
    complete_review_through,
    compute_reviewed_through,
)

from .publication_helpers import make_publication_config, open_candidate_repo, seed_candidate


ELECTION = "TW-2026-TNN-MAYOR"


def _setup(tmp_path: Path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    return config, repo


def _set_cursor(repo, last_published_at="2026-07-28T23:59:59.999999", last_id=1000):
    repo.set_scan_cursor(
        ELECTION, "news_article_id", last_id, last_published_at, last_published_at,
        "run_f1", "now",
    )


def test_complete_all_resolved(tmp_path):
    config, repo = _setup(tmp_path)
    seed_candidate(repo, status="auto_reject", canonical_event_date="2026-07-28T10:00:00")
    _set_cursor(repo)
    result = complete_review_through(
        repo, config, election_id=ELECTION, through_date="2026-07-28",
        reviewer="local_reviewer", from_date="2026-07-27",
    )
    assert result["reviewed_through"] == "2026-07-28"
    row = repo.get_daily_review_completion(ELECTION, "2026-07-28")
    assert row is not None
    assert row["review_status"] == "complete"
    assert row["candidate_total"] == 1
    assert row["resolved_count"] == 1
    assert row["unresolved_count"] == 0
    assert row["no_material_event"] == 1
    repo.close()


def test_pending_blocks_complete(tmp_path):
    config, repo = _setup(tmp_path)
    seed_candidate(repo, status="review_required", canonical_event_date="2026-07-28T10:00:00")
    _set_cursor(repo)
    with pytest.raises(ValueError, match="unresolved"):
        complete_review_through(
            repo, config, election_id=ELECTION, through_date="2026-07-28",
            reviewer="local_reviewer", from_date="2026-07-27",
        )
    assert repo.get_daily_review_completion(ELECTION, "2026-07-28") is None
    repo.close()


def test_hold_blocks_complete(tmp_path):
    config, repo = _setup(tmp_path)
    seed_candidate(repo, status="hold", canonical_event_date="2026-07-28T10:00:00")
    _set_cursor(repo)
    with pytest.raises(ValueError, match="hold"):
        complete_review_through(
            repo, config, election_id=ELECTION, through_date="2026-07-28",
            reviewer="local_reviewer", from_date="2026-07-27",
        )
    repo.close()


def test_needs_edit_blocks_complete(tmp_path):
    config, repo = _setup(tmp_path)
    seed_candidate(repo, status="needs_edit", canonical_event_date="2026-07-28T10:00:00")
    _set_cursor(repo)
    with pytest.raises(ValueError, match="needs_edit|hold"):
        complete_review_through(
            repo, config, election_id=ELECTION, through_date="2026-07-28",
            reviewer="local_reviewer", from_date="2026-07-27",
        )
    repo.close()


def test_approved_event_means_material_event(tmp_path):
    config, repo = _setup(tmp_path)
    seed_candidate(repo, status="review_approved", canonical_event_date="2026-07-28T10:00:00")
    _set_cursor(repo)
    result = complete_review_through(
        repo, config, election_id=ELECTION, through_date="2026-07-28",
        reviewer="local_reviewer", from_date="2026-07-27",
    )
    assert result["reviewed_through"] == "2026-07-28"
    row = repo.get_daily_review_completion(ELECTION, "2026-07-28")
    assert row["material_event_count"] == 1
    assert row["no_material_event"] == 0
    repo.close()


def test_ingestion_not_covered_blocks(tmp_path):
    config, repo = _setup(tmp_path)
    with pytest.raises(ValueError, match="ingestion"):
        complete_review_through(
            repo, config, election_id=ELECTION, through_date="2026-07-28",
            reviewer="local_reviewer", from_date="2026-07-27",
        )
    repo.close()


def test_cursor_before_day_end_blocks(tmp_path):
    config, repo = _setup(tmp_path)
    _set_cursor(repo, last_published_at="2026-07-28T10:00:00")
    with pytest.raises(ValueError, match="ingestion"):
        complete_review_through(
            repo, config, election_id=ELECTION, through_date="2026-07-28",
            reviewer="local_reviewer", from_date="2026-07-27",
        )
    repo.close()


def test_contiguous_advance_with_gap_rejected(tmp_path):
    config, repo = _setup(tmp_path)
    _set_cursor(repo, last_published_at="2026-07-31T23:59:59.999999", last_id=5000)
    seed_candidate(repo, status="hold", canonical_event_date="2026-07-30T10:00:00")
    result = complete_review_through(
        repo, config, election_id=ELECTION, through_date="2026-07-28",
        reviewer="local_reviewer", from_date="2026-07-27",
    )
    assert result["reviewed_through"] == "2026-07-28"
    result = complete_review_through(
        repo, config, election_id=ELECTION, through_date="2026-07-29",
        reviewer="local_reviewer", from_date="2026-07-27",
    )
    assert result["reviewed_through"] == "2026-07-29"
    # 7/30 not completed -> cannot jump to 7/30
    with pytest.raises(ValueError):
        complete_review_through(
            repo, config, election_id=ELECTION, through_date="2026-07-30",
            reviewer="local_reviewer", from_date="2026-07-27",
        )
    assert compute_reviewed_through(repo, ELECTION, date(2026, 7, 27)) == date(2026, 7, 29)
    assert repo.get_daily_review_completion(ELECTION, "2026-07-30") is None
    repo.close()


def test_no_event_day_advances(tmp_path):
    config, repo = _setup(tmp_path)
    _set_cursor(repo, last_published_at="2026-07-28T23:59:59.999999")
    result = complete_review_through(
        repo, config, election_id=ELECTION, through_date="2026-07-28",
        reviewer="local_reviewer", from_date="2026-07-27",
    )
    assert result["reviewed_through"] == "2026-07-28"
    row = repo.get_daily_review_completion(ELECTION, "2026-07-28")
    assert row["candidate_total"] == 0
    assert row["no_material_event"] == 1
    repo.close()


def test_later_event_but_unreviewed_does_not_advance(tmp_path):
    config, repo = _setup(tmp_path)
    _set_cursor(repo, last_published_at="2026-07-30T23:59:59.999999", last_id=6000)
    seed_candidate(repo, status="hold", canonical_event_date="2026-07-30T10:00:00")
    with pytest.raises(ValueError):
        complete_review_through(
            repo, config, election_id=ELECTION, through_date="2026-07-30",
            reviewer="local_reviewer", from_date="2026-07-27",
        )
    # 7/28 and 7/29 are valid and recorded; the boundary must not cross 7/30.
    assert compute_reviewed_through(repo, ELECTION, date(2026, 7, 27)) == date(2026, 7, 29)
    assert repo.get_daily_review_completion(ELECTION, "2026-07-30") is None
    repo.close()


def test_repeat_complete_idempotent(tmp_path):
    config, repo = _setup(tmp_path)
    _set_cursor(repo)
    complete_review_through(
        repo, config, election_id=ELECTION, through_date="2026-07-28",
        reviewer="local_reviewer", from_date="2026-07-27",
    )
    complete_review_through(
        repo, config, election_id=ELECTION, through_date="2026-07-28",
        reviewer="local_reviewer", from_date="2026-07-27",
    )
    rows = repo.list_daily_review_completions(ELECTION)
    assert len(rows) == 1
    assert compute_reviewed_through(repo, ELECTION, date(2026, 7, 27)) == date(2026, 7, 28)
    repo.close()


def test_update_facts_cutoff_writes_preflight_and_status(tmp_path):
    config, repo = _setup(tmp_path)
    preflight = config.path("coverage_root") / "coverage_preflight.json"
    preflight.write_text(
        json.dumps({"facts_cutoff": "2026-07-27", "coverage_status": "partial"}), encoding="utf-8"
    )
    status_path = tmp_path / "production_status.json"
    status_path.write_text(
        json.dumps({"coverage": {"facts_cutoff": "2026-07-27"}}), encoding="utf-8"
    )
    _set_cursor(repo)
    result = complete_review_through(
        repo, config, election_id=ELECTION, through_date="2026-07-28",
        reviewer="local_reviewer", from_date="2026-07-27", update_facts_cutoff=True,
        status_path=status_path,
    )
    assert result["facts_cutoff_applied"] is True
    assert json.loads(preflight.read_text(encoding="utf-8"))["facts_cutoff"] == "2026-07-28"
    assert json.loads(status_path.read_text(encoding="utf-8"))["coverage"]["facts_cutoff"] == "2026-07-28"
    repo.close()
