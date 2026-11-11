from __future__ import annotations

from app.election_candidates.candidate_id import (
    candidate_id_for_anchor,
    choose_anchor,
    cluster_fingerprint,
    write_candidate_id_strategy,
)

from .conftest import article_from_fixture, make_config


def _art(aid, title, url, published):
    return article_from_fixture(
        {
            "id": aid,
            "title": title,
            "url": url,
            "source_name": "中央社",
            "category": "politics",
            "published_at": published,
            "match": {},
        }
    )


def test_same_anchor_same_id():
    a1 = _art("1", "陳亭妃宣布參選", "https://a.com/1", "2026-07-01T08:00:00")
    a2 = _art("1", "陳亭妃宣布參選", "https://a.com/1", "2026-07-01T08:00:00")
    assert candidate_id_for_anchor(a1) == candidate_id_for_anchor(a2)


def test_title_change_does_not_change_id():
    a1 = _art("1", "舊標題", "https://a.com/1", "2026-07-01T08:00:00")
    a2 = _art("1", "新標題", "https://a.com/1", "2026-07-01T08:00:00")
    assert candidate_id_for_anchor(a1) == candidate_id_for_anchor(a2)


def test_new_article_does_not_change_id():
    a1 = _art("1", "標題", "https://a.com/1", "2026-07-01T08:00:00")
    a2 = _art("2", "後續報導", "https://a.com/2", "2026-07-02T08:00:00")
    assert candidate_id_for_anchor(a1) == candidate_id_for_anchor(choose_anchor([a1, a2]))


def test_run_id_not_in_id():
    a1 = _art("1", "標題", "https://a.com/1", "2026-07-01T08:00:00")
    cid = candidate_id_for_anchor(a1)
    assert "run_" not in cid
    assert cid.startswith("cand_tnn_")


def test_id_collision_safe_with_primary_key_and_url():
    a1 = _art("1", "同標題", "https://a.com/1", "2026-07-01T08:00:00")
    a2 = _art("2", "同標題", "https://a.com/2", "2026-07-01T08:00:00")
    assert candidate_id_for_anchor(a1) != candidate_id_for_anchor(a2)


def test_cluster_fingerprint_and_strategy_doc(tmp_path):
    a1 = _art("1", "標題", "https://a.com/1", "2026-07-01T08:00:00")
    config = make_config(tmp_path)
    fp = cluster_fingerprint([a1])
    assert fp == candidate_id_for_anchor(a1)
    path = write_candidate_id_strategy(tmp_path / "out", config)
    assert path.exists()
