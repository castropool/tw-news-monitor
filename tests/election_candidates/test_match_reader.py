from __future__ import annotations

from app.election_candidates.match_reader import (
    inline_classify,
    matches_signature,
    open_match_connection,
    read_persisted_matches,
    read_matches,
)
from app.election_candidates.news_reader import open_news_connection

from .conftest import article_from_fixture, create_match_db, create_news_db, make_config


def test_persisted_matches_read_and_joined(tmp_path):
    rows = [
        {"id": 1, "title": "台南選情", "url": "https://a.com/1", "source_name": "A",
         "category": "politics", "published_at": "2026-07-01T08:00:00", "summary": ""}
    ]
    create_news_db(tmp_path / "news.db", rows)
    create_match_db(
        tmp_path / "election_watch.db",
        [
            {
                "article_url": "https://a.com/1",
                "city": "tainan",
                "relevance": "high",
                "matched_people": ["陳亭妃"],
                "matched_issues": ["選舉"],
                "matched_basis": ["candidate_match", "issue_match"],
            }
        ],
    )
    config = make_config(tmp_path)
    match_conn = open_match_connection(tmp_path / "election_watch.db")
    news_conn = open_news_connection(tmp_path / "news.db")
    result = read_persisted_matches(match_conn, news_conn, config)
    match_conn.close()
    news_conn.close()
    assert "1" in result
    assert result["1"].matched_people == ["陳亭妃"]
    assert result["1"].match_rule_id == "article_matches"


def test_inline_classifier_reuses_election_watch_filter(tmp_path):
    article = article_from_fixture(
        {
            "id": "1",
            "title": "陳亭妃宣布參選台南市長",
            "url": "https://a.com/1",
            "source_name": "中央社",
            "category": "politics",
            "published_at": "2026-07-01T08:00:00",
            "match": {},
        }
    )
    config = make_config(tmp_path)
    result = inline_classify([article], config, city="tainan")
    assert "1" in result
    assert result["1"].match_rule_id == "inline_election_classifier"
    assert "陳亭妃" in result["1"].matched_people


def test_inline_classifier_ignores_non_tainan(tmp_path):
    article = article_from_fixture(
        {
            "id": "2",
            "title": "阿富汗洪災20死",
            "url": "https://a.com/2",
            "source_name": "中央社",
            "category": "international",
            "published_at": "2026-07-21T02:17:04+08:00",
            "match": {},
        }
    )
    config = make_config(tmp_path)
    result = inline_classify([article], config, city="tainan")
    assert "2" not in result


def test_read_matches_mode_dispatch(tmp_path):
    config = make_config(tmp_path)
    article = article_from_fixture(
        {
            "id": "3",
            "title": "謝龍介質疑卓榮泰",
            "url": "https://a.com/3",
            "source_name": "聯合新聞網",
            "category": "politics",
            "published_at": "2026-07-14T16:34:00",
            "match": {},
        }
    )
    result = read_matches([article], config, mode="inline_classifier")
    assert "3" in result


def test_matches_signature_stable(tmp_path):
    create_match_db(tmp_path / "election_watch.db", [])
    conn = open_match_connection(tmp_path / "election_watch.db")
    assert matches_signature(conn) == matches_signature(conn)
    conn.close()
