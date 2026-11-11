from __future__ import annotations

from pathlib import Path

from app.election_candidates.candidate_router import route_candidate

from .conftest import make_config


def _scores(**over):
    data = {
        "relevance_score": 0.8,
        "completeness_score": 0.9,
        "cluster_confidence": 0.9,
        "source_confidence": 0.9,
        "formal_duplicate_score": 0.1,
        "date_confidence": 1.0,
    }
    data.update(over)
    return data


def _profile(**over):
    data = {
        "has_observed_fact": True,
        "has_allegation": False,
        "has_uncertain_report": False,
        "has_media_interpretation": False,
        "has_actor_statement": False,
        "counts": {"observed_fact": 1, "allegation": 0, "uncertain_report": 0,
                   "media_interpretation": 0, "actor_statement": 0, "planned_action": 0,
                   "unknown": 0},
    }
    data.update(over)
    return data


def _candidate(**over):
    data = {
        "candidate_title": "陳亭妃出席活動",
        "candidate_summary": "據1篇報導",
        "source_count": 1,
        "canonical_event_date": "2026-07-19T00:00:00",
        "region_match": True,
        "has_candidate_actor": True,
    }
    data.update(over)
    return data


def test_not_directly_tainan_auto_reject(tmp_path):
    config = make_config(tmp_path)
    status, reasons = route_candidate(
        _candidate(relevance_label="irrelevant"), _scores(), _profile(), config
    )
    assert status == "auto_reject"
    assert "relevance_label_irrelevant" in reasons


def test_eligible_review_required(tmp_path):
    config = make_config(tmp_path)
    status, reasons = route_candidate(_candidate(), _scores(), _profile(), config)
    assert status == "review_required"


def test_date_unknown_hold(tmp_path):
    config = make_config(tmp_path)
    status, reasons = route_candidate(
        _candidate(relevance_label="direct_event", canonical_event_date="", event_date_basis="unknown"),
        _scores(),
        _profile(),
        config,
    )
    assert status == "hold"
    assert "date_unknown" in reasons


def test_publication_inferred_does_not_hold(tmp_path):
    config = make_config(tmp_path)
    status, _ = route_candidate(
        _candidate(
            relevance_label="direct_event",
            canonical_event_date="2026-07-14T00:00:00",
            event_date_basis="inferred_from_publication",
        ),
        _scores(),
        _profile(),
        config,
    )
    assert status == "review_required"


def test_no_observed_fact_hold(tmp_path):
    config = make_config(tmp_path)
    status, reasons = route_candidate(
        _candidate(),
        _scores(),
        _profile(has_observed_fact=False, has_allegation=True),
        config,
    )
    assert status == "hold"
    assert "no_observed_fact_no_statement" in reasons


def test_high_duplicate_score_duplicate_candidate(tmp_path):
    config = make_config(tmp_path)
    status, _ = route_candidate(
        _candidate(),
        _scores(formal_duplicate_score=0.95),
        _profile(),
        config,
    )
    assert status == "duplicate_candidate"


def test_low_cluster_confidence_hold(tmp_path):
    config = make_config(tmp_path)
    status, reasons = route_candidate(
        _candidate(), _scores(cluster_confidence=0.1), _profile(), config
    )
    assert status == "hold"
    assert "low_cluster_confidence" in reasons
