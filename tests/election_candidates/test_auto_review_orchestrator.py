"""Tests for the auto-review orchestrator (conservative auto-publish +
automatic facts_cutoff advance).

Covered: completable-boundary detection, stop at unresolved candidates, stop
at ingestion lag, check-only zero side effects.
"""

from __future__ import annotations

import argparse
from datetime import date
from pathlib import Path

import pytest
import yaml

from app.election_candidates.auto_review_orchestrator import (
    find_completable_through,
)

from .publication_helpers import make_publication_config, open_candidate_repo, seed_candidate

ELECTION = "TW-2026-TNN-MAYOR"


def _setup(tmp_path: Path):
    config = make_publication_config(tmp_path)
    # Isolate the auto orchestrator from the real production facts_cutoff:
    # point coverage_root at an empty tmp dir so current_facts_cutoff returns
    # None and tests pass from_date explicitly.
    empty = tmp_path / "no_coverage"
    empty.mkdir(exist_ok=True)
    config.raw["paths"]["coverage_root"] = str(empty)
    repo = open_candidate_repo(config)
    return config, repo


def _set_cursor(repo, last_published_at="2026-07-28T23:59:59.999999", last_id=1000):
    repo.set_scan_cursor(
        ELECTION, "news_article_id", last_id, last_published_at, last_published_at,
        "run_f1", "now",
    )


def test_find_completable_through_all_clear(tmp_path):
    config, repo = _setup(tmp_path)
    _set_cursor(repo, last_published_at="2026-07-29T23:59:59.999999", last_id=2000)
    # 07-28 has one auto-resolved candidate; 07-29 nothing.
    seed_candidate(repo, status="auto_reject", canonical_event_date="2026-07-28T10:00:00")
    repo.close()

    repo = open_candidate_repo(config)
    boundary = find_completable_through(repo, config, ELECTION, from_date="2026-07-27")
    repo.close()
    assert boundary["has_new_days"] is True
    assert boundary["completable_through"] == "2026-07-29"
    # blocked_days records the first day past the cursor (07-30 not yet ingested)
    assert boundary["blocked_days"][0]["day"] == "2026-07-30"
    assert "ingestion" in boundary["blocked_days"][0]["reason"]


def test_find_completable_stops_at_unresolved(tmp_path):
    config, repo = _setup(tmp_path)
    _set_cursor(repo, last_published_at="2026-07-29T23:59:59.999999", last_id=2000)
    # 07-28 is clear; 07-29 has an unresolved candidate -> stop at 07-28.
    seed_candidate(repo, status="auto_reject", canonical_event_date="2026-07-28T10:00:00")
    seed_candidate(repo, status="review_required", canonical_event_date="2026-07-29T10:00:00")
    repo.close()

    repo = open_candidate_repo(config)
    boundary = find_completable_through(repo, config, ELECTION, from_date="2026-07-27")
    repo.close()
    assert boundary["completable_through"] == "2026-07-28"
    assert boundary["has_new_days"] is True
    assert boundary["blocked_days"][0]["day"] == "2026-07-29"
    assert "unresolved" in boundary["blocked_days"][0]["reason"]


def test_find_completable_stops_at_ingestion_lag(tmp_path):
    config, repo = _setup(tmp_path)
    # Cursor only covers through 07-28; 07-29 cannot be completed.
    _set_cursor(repo, last_published_at="2026-07-28T23:59:59.999999", last_id=1000)
    seed_candidate(repo, status="auto_reject", canonical_event_date="2026-07-28T10:00:00")
    repo.close()

    repo = open_candidate_repo(config)
    boundary = find_completable_through(repo, config, ELECTION, from_date="2026-07-27")
    repo.close()
    assert boundary["completable_through"] == "2026-07-28"
    assert boundary["blocked_days"][0]["day"] == "2026-07-29"
    assert "ingestion" in boundary["blocked_days"][0]["reason"]


def test_find_completable_no_new_days_when_cutoff_current(tmp_path):
    config, repo = _setup(tmp_path)
    _set_cursor(repo, last_published_at="2026-07-28T23:59:59.999999", last_id=1000)
    seed_candidate(repo, status="auto_reject", canonical_event_date="2026-07-28T10:00:00")
    repo.close()

    repo = open_candidate_repo(config)
    boundary = find_completable_through(repo, config, ELECTION, from_date="2026-07-28")
    repo.close()
    assert boundary["has_new_days"] is False
    assert boundary["completable_through"] == "2026-07-28"


def test_orchestrator_check_only_writes_nothing(tmp_path):
    """--check-only must not write completion rows nor advance facts_cutoff."""
    config, repo = _setup(tmp_path)
    _set_cursor(repo, last_published_at="2026-07-29T23:59:59.999999", last_id=2000)
    seed_candidate(repo, status="auto_reject", canonical_event_date="2026-07-28T10:00:00")
    repo.close()

    from app.election_candidates.auto_review_orchestrator import run_orchestrator

    # auto_publish disabled in this isolated config so the test only exercises
    # the completion side without needing a full formal publication chain.
    config.raw["auto_publish"]["enabled"] = False
    config.raw["auto_publish"]["manifest_dir"] = str(tmp_path / "ap_manifest")

    # capture pre-state via direct module function with explicit from date
    repo = open_candidate_repo(config)
    from app.election_candidates.review_completion import compute_reviewed_through
    before = compute_reviewed_through(repo, ELECTION, date(2026, 7, 27))
    repo.close()

    args = argparse.Namespace(
        election_id=ELECTION, candidate_db=None, check_only=True, skip_downstream=False
    )
    # run_orchestrator loads config from a YAML path; persist the modified config.
    cfg_path = tmp_path / "candidate_pipeline_test.yaml"
    cfg_path.write_text(yaml.safe_dump(config.raw, allow_unicode=True), encoding="utf-8")
    result = run_orchestrator(cfg_path, args)
    assert result["auto_publish"]["status"] in ("disabled", "completed")
    assert result["complete_review"]["attempted"] is False

    repo = open_candidate_repo(config)
    after = compute_reviewed_through(repo, ELECTION, date(2026, 7, 27))
    repo.close()
    assert after == before  # nothing written
