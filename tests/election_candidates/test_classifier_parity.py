from __future__ import annotations

from pathlib import Path

from app.election_candidates.candidate_models import MatchInfo
from app.election_candidates.classifier_parity import build_classifier_parity_report

from .conftest import make_config
from .golden_runner import article_from_row
from .conftest import article_from_fixture


def test_classifier_parity_on_frozen_fixture(tmp_path):
    config = make_config(tmp_path)
    rows = [
        {
            "id": "p1",
            "title": "陳亭妃宣布參選台南市長",
            "url": "https://www.cna.com.tw/news/aipl/p1.aspx",
            "source_name": "中央社",
            "category": "politics",
            "published_at": "2026-03-01T09:00:00+08:00",
            "summary": "",
            "match": {
                "city": "tainan", "relevance": "high",
                "matched_people": ["陳亭妃"], "matched_parties": [],
                "matched_issues": [], "matched_terms": ["陳亭妃"],
                "matched_basis": ["region_match", "candidate_match", "issue_match"],
                "region_match": True, "election_context_match": True, "match_score": 1.0,
            },
        },
        {
            "id": "p2",
            "title": "謝龍介質疑對手賄選",
            "url": "https://www.cna.com.tw/news/aipl/p2.aspx",
            "source_name": "中央社",
            "category": "politics",
            "published_at": "2026-07-14T16:00:00+08:00",
            "summary": "",
            "match": {
                "city": "tainan", "relevance": "medium",
                "matched_people": ["謝龍介"], "matched_parties": [],
                "matched_issues": ["賄選"], "matched_terms": ["謝龍介", "賄選"],
                "matched_basis": ["region_match", "candidate_match"],
                "region_match": True, "election_context_match": False, "match_score": 0.65,
            },
        },
        {
            "id": "p3",
            "title": "柯文哲談藍白合作",
            "url": "https://www.cna.com.tw/news/aipl/p3.aspx",
            "source_name": "中央社",
            "category": "politics",
            "published_at": "2026-07-26T12:00:00+08:00",
            "summary": "",
            "match": None,
        },
    ]
    arts = [article_from_row(r) for r in rows if r["match"]]
    expected = {}
    for r in rows:
        m = r["match"] or {}
        expected[r["id"]] = MatchInfo(
            city=m.get("city", "tainan"),
            relevance=m.get("relevance", "low"),
            matched_people=list(m.get("matched_people", [])),
            matched_parties=list(m.get("matched_parties", [])),
            matched_issues=list(m.get("matched_issues", [])),
            matched_terms=list(m.get("matched_terms", [])),
            matched_basis=list(m.get("matched_basis", [])),
            region_match=bool(m.get("region_match", False)),
            election_context_match=bool(m.get("election_context_match", False)),
            match_score=float(m.get("match_score", 0)),
        )
    report = build_classifier_parity_report(arts, expected, config)
    assert report["classifier_parity_ready"] is True
    assert set(report["matched_by_both"]) == {"p1", "p2"}
    assert report["inline_only"] == []
    assert report["persisted_logic_only"] == []
    assert report["conflict_count"] == 0
