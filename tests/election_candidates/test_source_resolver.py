from __future__ import annotations

from app.election_candidates.source_resolver import resolve_sources

from .conftest import make_config


def _formal_sources():
    return [
        {"source_id": "src_cna", "publisher": "中央社", "title": "x",
         "url": "https://www.cna.com.tw/news/x.aspx"},
        {"source_id": "src_udn", "publisher": "聯合報", "title": "y",
         "url": "https://udn.com/news/story/1"},
        {"source_id": "src_ebc", "publisher": "東森新聞", "title": "z",
         "url": "https://news.ebc.net.tw/news/politics/1"},
    ]


def test_domain_exact_match(tmp_path):
    config = make_config(tmp_path)
    sources, _ = resolve_sources(
        [{"candidate_id": "c1", "news_article_id": "1", "name": "中央社",
          "url": "https://www.cna.com.tw/news/a.aspx", "first_seen_at": "x", "last_seen_at": "x"}],
        _formal_sources(),
        config,
    )
    assert sources[0]["formal_match_status"] == "exact"
    assert sources[0]["formal_source_id"] == "src_cna"


def test_name_exact_match(tmp_path):
    config = make_config(tmp_path)
    sources, _ = resolve_sources(
        [{"candidate_id": "c1", "news_article_id": "1", "name": "聯合報",
          "url": "https://other.com/x", "first_seen_at": "x", "last_seen_at": "x"}],
        _formal_sources(),
        config,
    )
    assert sources[0]["formal_source_id"] == "src_udn"


def test_alias_match(tmp_path):
    config = make_config(tmp_path)
    sources, _ = resolve_sources(
        [{"candidate_id": "c1", "news_article_id": "1", "name": "中央通訊社",
          "url": "https://other.com/x", "first_seen_at": "x", "last_seen_at": "x"}],
        _formal_sources(),
        config,
    )
    assert sources[0]["formal_match_status"] == "normalized_match"
    assert sources[0]["formal_source_id"] == "src_cna"


def test_normalized_name_match(tmp_path):
    config = make_config(tmp_path)
    sources, _ = resolve_sources(
        [{"candidate_id": "c1", "news_article_id": "1", "name": "東森新聞網",
          "url": "https://other.com/x", "first_seen_at": "x", "last_seen_at": "x"}],
        _formal_sources(),
        config,
    )
    assert sources[0]["formal_match_status"] == "normalized_match"


def test_fuzzy_possible_match(tmp_path):
    config = make_config(tmp_path)
    sources, _ = resolve_sources(
        [{"candidate_id": "c1", "news_article_id": "1", "name": "中央社報",
          "url": "https://other.com/x", "first_seen_at": "x", "last_seen_at": "x"}],
        _formal_sources(),
        config,
    )
    assert sources[0]["formal_match_status"] == "possible_match"


def test_new_candidate_source_not_written_to_formal(tmp_path):
    config = make_config(tmp_path)
    sources, _ = resolve_sources(
        [{"candidate_id": "c1", "news_article_id": "1", "name": "全新媒體",
          "url": "https://new.com/x", "first_seen_at": "x", "last_seen_at": "x"}],
        _formal_sources(),
        config,
    )
    assert sources[0]["formal_match_status"] == "new_candidate_source"
    assert sources[0]["formal_source_id"] == ""


def test_unresolved_without_name_and_domain(tmp_path):
    config = make_config(tmp_path)
    sources, _ = resolve_sources(
        [{"candidate_id": "c1", "news_article_id": "1", "name": "", "url": "",
          "first_seen_at": "x", "last_seen_at": "x"}],
        _formal_sources(),
        config,
    )
    assert sources[0]["formal_match_status"] == "unresolved"
