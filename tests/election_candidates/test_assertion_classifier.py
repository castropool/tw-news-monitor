from __future__ import annotations

import pytest

from app.election_candidates.assertion_classifier import classify_article_assertions

from .conftest import article_from_fixture, make_config


def _assert_one_kind(title, tmp_path, expected, summary="", people=None):
    config = make_config(tmp_path)
    match = {
        "city": "tainan",
        "relevance": "high",
        "matched_people": people or [],
        "matched_parties": [],
        "matched_issues": [],
        "matched_terms": (people or [])[:],
        "matched_basis": ["candidate_match"] if people else [],
        "region_match": True,
        "match_score": 1.0,
    }
    art = article_from_fixture(
        {
            "id": "1",
            "title": title,
            "url": "https://a.com/1",
            "source_name": "中央社",
            "category": "politics",
            "published_at": "2026-07-01T08:00:00",
            "summary": summary,
            "match": match,
        }
    )
    results = classify_article_assertions(art, "cand_x", "run_x", config)
    return results[0]


def test_observed_fact(tmp_path):
    a = _assert_one_kind("民進黨正式提名陳亭妃參選台南市長", tmp_path, "observed_fact")
    assert a["assertion_kind"] == "observed_fact"


def test_actor_statement(tmp_path):
    a = _assert_one_kind("陳亭妃表示支持市政建設", tmp_path, "actor_statement", people=["陳亭妃"])
    assert a["assertion_kind"] == "actor_statement"
    assert a["speaker"] != ""


def test_allegation(tmp_path):
    a = _assert_one_kind("謝龍介質疑對手賄選", tmp_path, "allegation")
    assert a["assertion_kind"] == "allegation"
    assert "unverified" in __import__("json").loads(a["risk_flags_json"])


def test_media_interpretation(tmp_path):
    results = _assert_one_kind("黃博郎觀點 台南市長選情冷清", tmp_path, "media_interpretation")
    assert results["assertion_kind"] == "media_interpretation"


def test_planned_action(tmp_path):
    a = _assert_one_kind("陳亭妃擬於月底宣布參選", tmp_path, "planned_action")
    assert a["assertion_kind"] == "planned_action"
    assert "future" in __import__("json").loads(a["risk_flags_json"])


def test_uncertain_report(tmp_path):
    a = _assert_one_kind("傳王定宇不再選立委", tmp_path, "uncertain_report")
    assert a["assertion_kind"] == "uncertain_report"


def test_observed_fact_for_public_meeting(tmp_path):
    a = _assert_one_kind("市政會議今天召開", tmp_path, "observed_fact")
    assert a["assertion_kind"] == "observed_fact"


@pytest.mark.parametrize(
    "title",
    [
        "謝龍介質疑對手賄選",
        "傳王定宇將換將",
        "黃博郎觀點：選情冷清",
    ],
)
def test_claim_never_classified_as_observed_fact(title, tmp_path):
    a = _assert_one_kind(title, tmp_path, "not_observed")
    assert a["assertion_kind"] != "observed_fact"


def test_statement_speaker_present(tmp_path):
    a = _assert_one_kind("陳亭妃表示將推動老人福利", tmp_path, "actor_statement", people=["陳亭妃"])
    assert "陳亭妃" in a["speaker"]


def test_assertion_id_stable_across_runs(tmp_path):
    config = make_config(tmp_path)
    art = article_from_fixture(
        {
            "id": "1", "title": "陳亭妃表示將推動老人福利", "url": "https://a.com/1",
            "source_name": "中央社", "category": "politics",
            "published_at": "2026-07-01T08:00:00", "match": {},
        }
    )
    r1 = classify_article_assertions(art, "cand_x", "run_a", config)[0]
    r2 = classify_article_assertions(art, "cand_x", "run_b", config)[0]
    assert r1["assertion_id"] == r2["assertion_id"]
