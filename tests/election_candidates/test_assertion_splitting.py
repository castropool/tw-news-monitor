from __future__ import annotations

from app.election_candidates.assertion_classifier import classify_article_assertions

from .conftest import article_from_fixture, make_config


def _kinds(title, tmp_path, people=None, summary=""):
    config = make_config(tmp_path)
    art = article_from_fixture(
        {
            "id": "s1",
            "title": title,
            "url": "https://a.com/s1",
            "source_name": "中央社",
            "category": "politics",
            "published_at": "2026-07-21T10:00:00",
            "summary": summary,
            "match": {
                "city": "tainan",
                "relevance": "high",
                "matched_people": people or [],
                "matched_parties": [],
                "matched_issues": ["選舉"],
                "matched_terms": (people or [])[:],
                "matched_basis": ["candidate_match"],
                "region_match": True,
                "match_score": 1.0,
            },
        }
    )
    return [a["assertion_kind"] for a in classify_article_assertions(art, "c", "r", config)]


def test_fact_plus_statement_same_sentence(tmp_path):
    kinds = _kinds(
        "謝龍介21日前往安南區參拜，並表示藍白合作有助整合在野力量",
        tmp_path,
        people=["謝龍介"],
    )
    assert "observed_fact" in kinds
    assert "actor_statement" in kinds


def test_action_plus_allegation_same_sentence(tmp_path):
    kinds = _kinds("謝龍介出席座談會，同時指控對手抹黑", tmp_path, people=["謝龍介"])
    assert "observed_fact" in kinds
    assert "allegation" in kinds


def test_connector_speaker_carry(tmp_path):
    kinds = _kinds("陳亭妃出席活動，但強調市政優先", tmp_path, people=["陳亭妃"])
    assert "actor_statement" in kinds


def test_quotation_speaker(tmp_path):
    kinds = _kinds("陳亭妃：「市政建設必須持續推動」", tmp_path, people=["陳亭妃"])
    assert "actor_statement" in kinds


def test_uncertain_only(tmp_path):
    kinds = _kinds("傳王定宇不再選立委", tmp_path, people=["王定宇"])
    assert kinds == ["uncertain_report"]


def test_media_only(tmp_path):
    kinds = _kinds("黃博郎觀點 台南市長選情冷清", tmp_path)
    assert "media_interpretation" in kinds


def test_planned_only(tmp_path):
    kinds = _kinds("陳亭妃擬於月底舉辦造勢晚會", tmp_path, people=["陳亭妃"])
    assert "planned_action" in kinds


def test_statement_not_observed(tmp_path):
    kinds = _kinds("陳亭妃表示支持市政建設", tmp_path, people=["陳亭妃"])
    assert "observed_fact" not in kinds
    assert "actor_statement" in kinds
