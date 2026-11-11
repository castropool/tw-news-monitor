from __future__ import annotations

from app.election_candidates.article_normalizer import (
    normalize_domain,
    normalize_source_name,
    normalize_title,
    normalize_url,
    parse_date,
)

from .conftest import make_config


def test_url_normalization_lowercases_and_strips_tracking():
    url = "HTTPS://WWW.Udn.com/News/Story/1?from=catelist&utm_source=xx#frag"
    assert normalize_url(url) == "https://www.udn.com/news/story/1"


def test_url_normalization_keeps_raw_url_field(tmp_path):
    from app.election_candidates.article_normalizer import normalize_article

    config = make_config(tmp_path)
    art = normalize_article(
        {
            "id": 1,
            "title": " 測試 標題 ",
            "url": "https://a.com/x?utm_source=1",
            "source_name": "　中央社 ",
            "category": "politics",
            "published_at": "2026/07/01 08:00:00",
            "fetched_at": "2026-07-01T09:00:00",
            "summary": "",
        },
        config,
    )
    assert art.raw_url == "https://a.com/x?utm_source=1"
    assert art.normalized_url == "https://a.com/x"
    assert art.raw_title == " 測試 標題 "


def test_source_name_fullwidth_normalization():
    assert normalize_source_name("　中央社　") == "中央社"
    assert normalize_source_name("ＥＢＣ東森新聞") == "EBC東森新聞"


def test_title_prefix_stripping():
    assert normalize_title("快訊：陳亭妃宣布參選") == "陳亭妃宣布參選"
    assert normalize_title("【獨家】謝龍介表態") == "【獨家】謝龍介表態"


def test_date_parsing_multiple_formats():
    assert parse_date("2026/07/01 08:00:00").startswith("2026-07-01")
    assert parse_date("2026年7月1日").startswith("2026-07-01")
    assert parse_date("2026-07-01") == "2026-07-01T00:00:00"
    assert parse_date("") == ""


def test_domain_normalization_removes_www():
    assert normalize_domain("https://www.cna.com.tw/x") == "cna.com.tw"
