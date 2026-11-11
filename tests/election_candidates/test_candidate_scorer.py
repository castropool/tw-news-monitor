from __future__ import annotations

from pathlib import Path

from app.election_candidates.candidate_scorer import (
    assertion_risk_score,
    cluster_confidence,
    completeness_score,
    date_confidence,
    risk_level,
    score_candidate,
)

from .conftest import article_from_fixture, make_config


def _candidate(**over):
    data = {
        "candidate_id": "cand_tnn_abc123",
        "primary_actor": "陳亭妃",
        "secondary_actors_json": "[]",
        "canonical_event_date": "2026-07-19T00:00:00",
        "event_date_confidence": "high",
        "candidate_event_type": "campaign_launch",
        "candidate_title": "陳亭妃出席競選活動",
        "candidate_summary": "據1篇報導，陳亭妃出席活動",
        "article_count": 1,
        "source_count": 1,
    }
    data.update(over)
    return data


def _articles():
    return [
        article_from_fixture(
            {
                "id": "1",
                "title": "陳亭妃宣布參選台南市長",
                "url": "https://a.com/1",
                "source_name": "中央社",
                "category": "politics",
                "published_at": "2026-07-19T08:00:00",
                "match": {
                    "city": "tainan", "relevance": "high", "matched_people": ["陳亭妃"],
                    "matched_issues": ["競選"], "matched_terms": ["陳亭妃", "競選"],
                    "matched_basis": ["candidate_match", "region_match"],
                    "region_match": True, "match_score": 1.0,
                },
            }
        )
    ]


def test_scores_deterministic(tmp_path):
    config = make_config(tmp_path)
    arts = _articles()
    profile = {"counts": {"observed_fact": 1, "allegation": 0, "uncertain_report": 0,
                          "media_interpretation": 0, "unknown": 0, "actor_statement": 0,
                          "planned_action": 0}, "has_observed_fact": True}
    s1 = score_candidate(_candidate(), arts, [{"formal_match_status": "exact"}], [], profile, [], config)
    s2 = score_candidate(_candidate(), arts, [{"formal_match_status": "exact"}], [], profile, [], config)
    assert s1 == s2


def test_completeness_full_score(tmp_path):
    config = make_config(tmp_path)
    arts = _articles()
    score = completeness_score(
        _candidate(), arts, [{"x": 1}], [], config
    )
    assert score >= 0.9


def test_cluster_confidence_single_article(tmp_path):
    config = make_config(tmp_path)
    arts = _articles()
    assert cluster_confidence(_candidate(), arts, config) >= 0.5


def test_date_confidence_mapping():
    assert date_confidence({"event_date_confidence": "high"}) == 1.0
    assert date_confidence({"event_date_confidence": "unknown"}) == 0.0


def test_assertion_risk_only_allegation_high(tmp_path):
    config = make_config(tmp_path)
    profile = {"counts": {"allegation": 1, "uncertain_report": 0, "media_interpretation": 0,
                          "unknown": 0, "observed_fact": 0, "actor_statement": 0,
                          "planned_action": 0}, "has_observed_fact": False,
               "has_allegation": True, "has_uncertain_report": False,
               "has_media_interpretation": False, "has_actor_statement": False}
    assert assertion_risk_score(profile, config) > 0.3
    assert risk_level(assertion_risk_score(profile, config), profile, 1.0, config) == "high"


def test_risk_level_no_observed_fact_with_media_high(tmp_path):
    config = make_config(tmp_path)
    profile = {"counts": {"media_interpretation": 1, "allegation": 0, "uncertain_report": 0,
                          "unknown": 0, "observed_fact": 0, "actor_statement": 0,
                          "planned_action": 0}, "has_observed_fact": False,
               "has_allegation": False, "has_uncertain_report": False,
               "has_media_interpretation": True, "has_actor_statement": False}
    assert risk_level(0.2, profile, 1.0, config) == "high"
