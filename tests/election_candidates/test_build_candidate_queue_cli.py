from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from app.election_candidates.build_candidate_queue import run_pipeline
from app.election_candidates.candidate_repository import CandidateRepository

from .conftest import create_formal_db, create_match_db, create_news_db, make_config


def _args(**over):
    data = {
        "config": "config/election_candidate_pipeline.yaml",
        "election_id": None,
        "date_from": "2026-07-01",
        "date_to": "2026-07-31",
        "since_last_success": False,
        "output_root": None,
        "candidate_db": None,
        "match_db": None,
        "match_mode": "inline_classifier",
        "validate_only": False,
        "rebuild_preview": False,
        "reset_test_cursor": False,
        "test_mode": False,
    }
    data.update(over)
    return argparse.Namespace(**data)


def _candidate_dict(cid="cand_tnn_0123456789"):
    return {
        "candidate_id": cid,
        "election_id": "TW-2026-TNN-MAYOR",
        "anchor_article_id": "1",
        "cluster_fingerprint": cid,
        "canonical_event_date": "2026-07-19T00:00:00",
        "event_date_precision": "day",
        "event_date_basis": "explicit_in_title",
        "event_date_confidence": "high",
        "candidate_event_type": "campaign_launch",
        "candidate_title": "陳亭妃出席活動",
        "candidate_summary": "據1篇報導",
        "primary_actor": "陳亭妃",
        "secondary_actors_json": "[]",
        "locations_json": "[]",
        "themes_json": "[]",
        "keywords_json": "[]",
        "assertion_profile_json": '{"has_observed_fact": true}',
        "article_count": 1,
        "source_count": 1,
        "relevance_score": 0.8,
        "completeness_score": 0.9,
        "cluster_confidence": 0.9,
        "date_confidence": 1.0,
        "source_confidence": 0.8,
        "assertion_risk_score": 0.1,
        "formal_duplicate_score": 0.1,
        "formal_duplicate_status": "no_match",
        "risk_level": "low",
        "review_status": "review_required",
        "status_reason_codes_json": '["eligible"]',
        "first_seen_at": "2026-08-01T00:00:00",
        "last_updated_at": "2026-08-01T00:00:00",
        "created_run_id": "run_1",
        "updated_run_id": "run_1",
        "candidate_schema_version": "1.0",
        "region_match": True,
        "has_candidate_actor": True,
    }


def test_cli_rejects_since_last_success_with_dates(monkeypatch):
    import sys

    from app.election_candidates.build_candidate_queue import main as build_main

    monkeypatch.setattr(
        sys,
        "argv",
        [
            "build_candidate_queue.py",
            "--since-last-success",
            "--date-from",
            "2026-07-01",
            "--date-to",
            "2026-07-31",
        ],
    )
    with pytest.raises(SystemExit):
        build_main()


def test_cli_rejects_single_date(monkeypatch):
    import sys

    from app.election_candidates.build_candidate_queue import main as build_main

    monkeypatch.setattr(
        sys,
        "argv",
        ["build_candidate_queue.py", "--date-from", "2026-07-01"],
    )
    with pytest.raises(SystemExit):
        build_main()


def test_reset_test_cursor_requires_test_mode(tmp_path):
    create_news_db(tmp_path / "news.db", [])
    create_match_db(tmp_path / "election_watch.db", [])
    create_formal_db(tmp_path / "election_context.db")
    config = make_config(tmp_path, test_mode=False)
    config.raw["paths"]["candidate_db"] = str(tmp_path / "prod_candidate.db")
    repo = CandidateRepository(tmp_path / "prod_candidate.db")
    repo.connect()
    repo.create_tables()
    repo.set_scan_cursor("TW-2026-TNN-MAYOR", "news_article_id", 10, "", "", "run1", "now")
    repo.close()
    with pytest.raises(PermissionError):
        run_pipeline(config, _args(reset_test_cursor=True, test_mode=False))
    result = run_pipeline(config, _args(reset_test_cursor=True, test_mode=True))
    assert result["reset"] is True


def test_validate_only_writes_validation_json(tmp_path):
    create_formal_db(tmp_path / "election_context.db")
    config = make_config(tmp_path)
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    repo.create_tables()
    repo.upsert_candidate(_candidate_dict())
    repo.upsert_validation(
        {
            "candidate_id": "cand_tnn_0123456789",
            "validation_ready": 0,
            "errors_json": "[]",
            "warnings_json": "[]",
            "checked_at": "x",
            "validator_version": "0.1.0",
        }
    )
    repo.close()
    out = tmp_path / "validation_out"
    payload = run_pipeline(
        config,
        _args(validate_only=True, output_root=str(out)),
    )
    assert (out / "candidate_validation.json").exists()
    assert payload["candidate_count"] == 1


def test_rebuild_preview_requires_successful_run(tmp_path):
    config = make_config(tmp_path)
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    repo.create_tables()
    repo.close()
    with pytest.raises(RuntimeError):
        run_pipeline(config, _args(rebuild_preview=True, output_root=str(tmp_path / "out")))
