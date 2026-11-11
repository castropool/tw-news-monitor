from __future__ import annotations

import pytest

from app.election_candidates.event_clusterer import extract_event_date
from app.election_candidates.quality_reports import date_basis_stats

from .conftest import article_from_fixture, make_config


def _art(title, published, summary=""):
    return article_from_fixture(
        {
            "id": "d1",
            "title": title,
            "url": "https://a.com/d1",
            "source_name": "中央社",
            "category": "politics",
            "published_at": published,
            "summary": summary,
            "match": {},
        }
    )


def test_title_absolute_date(tmp_path):
    art = _art("陳亭妃7月19日出席活動", "2026-07-20T10:00:00")
    date, basis, conf = extract_event_date(art, make_config(tmp_path))
    assert date.startswith("2026-07-19") and basis == "explicit_in_title" and conf == "high"


def test_summary_absolute_date(tmp_path):
    art = _art("陳亭妃出席活動", "2026-07-20T10:00:00", "活動於7月19日舉行")
    date, basis, conf = extract_event_date(art, make_config(tmp_path))
    assert date.startswith("2026-07-19") and basis == "explicit_in_summary" and conf == "high"


@pytest.mark.parametrize(
    "word,delta",
    [("昨日", -1), ("今天", 0), ("明日", 1)],
)
def test_relative_dates(tmp_path, word, delta):
    art = _art(f"陳亭妃{word}出席活動", "2026-07-20T10:00:00")
    date, basis, conf = extract_event_date(art, make_config(tmp_path))
    expected = 20 + delta
    assert date.startswith(f"2026-07-{expected:02d}")
    assert basis == "explicit_in_title" and conf == "medium"


def test_bare_day_number(tmp_path):
    art = _art("國民黨25日號召民眾上凱道", "2026-07-17T07:53:16")
    date, basis, conf = extract_event_date(art, make_config(tmp_path))
    assert date.startswith("2026-07-25")
    assert basis == "explicit_in_title" and conf == "high"


def test_cross_month_absolute(tmp_path):
    art = _art("陳亭妃6月30日登記", "2026-07-01T10:00:00")
    date, _, conf = extract_event_date(art, make_config(tmp_path))
    assert date.startswith("2026-06-30") and conf == "high"


def test_publication_inferred(tmp_path):
    art = _art("陳亭妃出席活動", "2026-07-20T10:00:00")
    _, basis, conf = extract_event_date(art, make_config(tmp_path))
    assert basis == "inferred_from_publication" and conf == "low"


def test_unknown_date(tmp_path):
    art = _art("陳亭妃出席活動", "")
    date, basis, conf = extract_event_date(art, make_config(tmp_path))
    assert date == "" and basis == "unknown" and conf == "unknown"


def test_date_stats_separate_inferred_from_explicit(tmp_path):
    stats = date_basis_stats(
        [
            {"event_date_basis": "explicit_in_title", "event_date_confidence": "high"},
            {"event_date_basis": "explicit_in_title", "event_date_confidence": "medium"},
            {"event_date_basis": "inferred_from_publication", "event_date_confidence": "low"},
            {"event_date_basis": "unknown", "event_date_confidence": "unknown"},
        ]
    )
    assert stats == {
        "explicit_event_date_count": 1,
        "relative_event_date_count": 1,
        "publication_inferred_date_count": 1,
        "unknown_event_date_count": 1,
    }
