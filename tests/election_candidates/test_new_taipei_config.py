"""New Taipei candidate pipeline config sanity + match_reader city derivation."""

from __future__ import annotations

import pytest

from app.election_candidates.config import load_config


def test_new_taipei_candidate_config_loads():
    cfg = load_config("config/election_candidate_pipeline.new_taipei.yaml")
    assert cfg.canonical_election_id == "new_taipei_mayoral_2026"
    assert cfg.election_id_aliases["new_taipei_mayoral_2026"] == "TW-2026-NTP-MAYOR"
    assert cfg.candidate_id_prefix == "cand_ntp"
    assert cfg.get("match_reader.city_values") == ["new_taipei"]
    assert cfg.path("candidate_db").name == "candidate_fact_pipeline.db"
    assert "new_taipei_2026" in str(cfg.path("candidate_db"))
    # shared dbs stay shared; formal db is per-election
    assert cfg.path("news_db").name == "news.db"
    assert "new_taipei" in str(cfg.path("formal_db"))
    # other_race markers must treat 台南 as other and NOT contain 新北
    other = cfg.get("relevance.other_race_markers") or []
    assert "台南" in other
    assert not any("新北" in str(t) for t in other)
    # direct action markers contain 參選新北 not 參選台南
    direct = cfg.get("relevance.direct_action_markers") or []
    assert any("參選新北" in str(t) for t in direct)
    assert not any("參選台南" in str(t) for t in direct)


def test_tainan_config_untouched():
    cfg = load_config("config/election_candidate_pipeline.yaml")
    assert cfg.canonical_election_id == "tainan_mayoral_2026"
    assert cfg.get("match_reader.city_values") == ["tainan"]
    other = cfg.get("relevance.other_race_markers") or []
    assert "新北" in other
    assert "台南" not in other


def test_inline_classify_uses_config_city(monkeypatch, tmp_path):
    """inline_classify must derive city from config city_values, not hardcode tainan."""
    from app.election_candidates import match_reader as mr

    captured: dict = {}

    class _FakeClassifier:
        def __init__(self, path):
            captured["path"] = str(path)

        def classify_article(self, title, category, source_name):
            # 模拟：文章同时命中 tainan 与 new_taipei 两个 city
            return [
                {"city": "tainan", "relevance": "high", "matched_people": ["陈亭妃"],
                 "matched_parties": [], "matched_issues": ["选举"], "matched_terms": ["陈亭妃"],
                 "matched_basis": ["candidate_match"]},
                {"city": "new_taipei", "relevance": "high", "matched_people": ["李四川"],
                 "matched_parties": [], "matched_issues": ["选举"], "matched_terms": ["李四川"],
                 "matched_basis": ["candidate_match"]},
            ]

    monkeypatch.setattr("app.election_classifier.ElectionClassifier", _FakeClassifier)

    class _Cfg:
        def path(self, key):
            return "config/election_watch.yaml"

        def get(self, key, default=None):
            if key == "match_reader.city_values":
                return ["new_taipei"]
            return default

    class _Art:
        news_article_id = "1"
        raw_title = "李四川掃街"
        category = ""
        source_name = ""

    result = mr.inline_classify([_Art()], _Cfg())
    assert "1" in result
    assert result["1"].city == "new_taipei"
    assert result["1"].matched_people == ["李四川"]
