from __future__ import annotations

import json

from app.election_candidates.candidate_validator import (
    build_global_validation,
    scan_package_for_forbidden_writes,
    validate_candidate,
)

from .conftest import make_config


def _candidate(**over):
    data = {
        "candidate_id": "cand_tnn_0123456789",
        "review_status": "review_required",
        "status_reason_codes_json": json.dumps(["eligible"]),
        "anchor_article_id": "1",
        "article_count": 1,
        "canonical_event_date": "2026-07-19T00:00:00",
        "event_date_basis": "explicit_in_title",
        "event_date_confidence": "high",
        "candidate_title": "陳亭妃出席活動",
        "candidate_summary": "據1篇報導",
        "relevance_score": 0.8,
        "completeness_score": 0.9,
        "cluster_confidence": 0.9,
        "formal_duplicate_score": 0.1,
        "risk_level": "low",
    }
    data.update(over)
    return data


def _valid_ctx():
    articles = [{"news_article_id": "1"}]
    assertions = [
        {"assertion_kind": "observed_fact", "evidence_article_id": "1",
         "speaker": "", "risk_flags_json": "[]"},
        {"assertion_kind": "actor_statement", "evidence_article_id": "1",
         "speaker": "陳亭妃", "risk_flags_json": "[]"},
    ]
    sources = [{"candidate_source_id": "csrc_1"}]
    suggestions = [{"formal_event_id": "evt_x", "suggested_action": "no_material_match"}]
    return articles, assertions, sources, suggestions


def test_valid_candidate_passes(tmp_path):
    config = make_config(tmp_path)
    articles, assertions, sources, suggestions = _valid_ctx()
    result = validate_candidate(
        _candidate(), articles, assertions, sources, suggestions, {"evt_x"}, config
    )
    assert result["validation_ready"] == 1


def test_invalid_candidate_id(tmp_path):
    config = make_config(tmp_path)
    articles, assertions, sources, suggestions = _valid_ctx()
    result = validate_candidate(
        _candidate(candidate_id="bad"), articles, assertions, sources, suggestions, {"evt_x"}, config
    )
    assert "candidate_id_invalid" in json.loads(result["errors_json"])


def test_statement_speaker_required(tmp_path):
    config = make_config(tmp_path)
    articles, assertions, sources, suggestions = _valid_ctx()
    assertions = [dict(a, speaker="") for a in assertions]
    result = validate_candidate(
        _candidate(), articles, assertions, sources, suggestions, {"evt_x"}, config
    )
    assert "statement_speaker_present" in json.loads(result["errors_json"])


def test_forbidden_status_rejected(tmp_path):
    config = make_config(tmp_path)
    articles, assertions, sources, suggestions = _valid_ctx()
    result = validate_candidate(
        _candidate(review_status="approved"), articles, assertions, sources, suggestions, {"evt_x"}, config
    )
    assert "review_status_forbidden" in json.loads(result["errors_json"])


def test_formal_event_id_must_exist(tmp_path):
    config = make_config(tmp_path)
    articles, assertions, sources, suggestions = _valid_ctx()
    result = validate_candidate(
        _candidate(), articles, assertions, sources, suggestions, set(), config
    )
    assert "formal_event_ids_exist" in json.loads(result["errors_json"])


def test_political_inference_detected(tmp_path):
    config = make_config(tmp_path)
    articles, assertions, sources, suggestions = _valid_ctx()
    result = validate_candidate(
        _candidate(candidate_summary="陳亭妃勝算提高"), articles, assertions, sources, suggestions, {"evt_x"}, config
    )
    assert "no_political_inference" in json.loads(result["errors_json"])


def test_forbidden_write_scan_finds_no_imports():
    from pathlib import Path

    pkg = Path("app/election_candidates")
    hits = scan_package_for_forbidden_writes(pkg, ["save_event", "save_source", "create_event", "insert_event_source"])
    assert hits == []


def test_global_validation_reports_unchanged_inputs():
    from pathlib import Path

    config = make_config(Path("."))
    before = {"news_db_unchanged": "a", "article_matches_unchanged": "b",
              "formal_data_unchanged": "c", "frozen_release_unchanged": "d"}
    after = dict(before)
    result = build_global_validation(
        2, 2, {"review_required": 2}, before, after, 0, Path("app/election_candidates"), config
    )
    assert result["candidate_pipeline_ready"] is True
    assert result["formal_database_open_mode"] == "read_only"
    assert result["formal_write_method_call_count"] == 0
