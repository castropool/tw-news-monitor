from __future__ import annotations

import sqlite3

import pytest

from app.election_candidates.candidate_repository import CandidateRepository


@pytest.fixture
def repo(tmp_path):
    r = CandidateRepository(tmp_path / "cand.db")
    r.connect()
    r.create_tables()
    yield r
    r.close()


def _candidate(cid="cand_tnn_0123456789", **over):
    data = {
        "candidate_id": cid,
        "election_id": "TW-2026-TNN-MAYOR",
        "anchor_article_id": "1",
        "cluster_fingerprint": cid,
        "canonical_event_date": "2026-07-19T00:00:00",
        "event_date_precision": "day",
        "event_date_basis": "explicit_in_title",
        "event_date_confidence": "high",
        "candidate_event_type": "campaign_launch",
        "candidate_title": "陳亭妃出席活動",
        "candidate_summary": "據1篇報導",
        "primary_actor": "陳亭妃",
        "secondary_actors_json": "[]",
        "locations_json": "[]",
        "themes_json": "[]",
        "keywords_json": "[]",
        "assertion_profile_json": "{}",
        "article_count": 1,
        "source_count": 1,
        "relevance_score": 0.8,
        "completeness_score": 0.9,
        "cluster_confidence": 0.9,
        "date_confidence": 1.0,
        "source_confidence": 0.8,
        "assertion_risk_score": 0.1,
        "formal_duplicate_score": 0.1,
        "formal_duplicate_status": "no_match",
        "risk_level": "low",
        "review_status": "review_required",
        "status_reason_codes_json": '["eligible"]',
        "first_seen_at": "2026-08-01T00:00:00",
        "last_updated_at": "2026-08-01T00:00:00",
        "created_run_id": "run_1",
        "updated_run_id": "run_1",
        "candidate_schema_version": "1.0",
    }
    data.update(over)
    return data


def test_create_tables_and_schema(repo):
    names = {
        r[0]
        for r in repo.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }
    assert {
        "pipeline_runs", "scan_cursors", "candidate_events", "candidate_event_articles",
        "candidate_assertions", "candidate_sources", "candidate_event_sources",
        "formal_duplicate_suggestions", "candidate_validation_results",
    } <= names
    fk = repo.conn.execute("PRAGMA foreign_keys").fetchone()[0]
    assert fk == 1


def test_candidate_upsert_and_read(repo):
    repo.upsert_candidate(_candidate())
    c = repo.get_candidate("cand_tnn_0123456789")
    assert c["primary_actor"] == "陳亭妃"
    assert c["review_status"] == "review_required"


def test_attach_article_unique_constraint(repo):
    repo.upsert_candidate(_candidate())
    link = {
        "candidate_id": "cand_tnn_0123456789",
        "news_article_id": "1",
        "relationship_type": "same_event",
        "is_anchor": 1,
        "article_title": "t",
        "article_url": "u",
        "source_name": "s",
        "published_at": "p",
        "event_date_candidate": "d",
        "event_date_basis": "b",
        "match_score": 0.5,
        "attached_run_id": "run_1",
    }
    repo.attach_article(link)
    repo.attach_article(link)
    rows = repo.get_articles("cand_tnn_0123456789")
    assert len(rows) == 1


def test_scan_cursor_single_row_per_election(repo):
    repo.set_scan_cursor("e1", "news_article_id", 10, "p", "c", "run1", "now")
    repo.set_scan_cursor("e1", "news_article_id", 20, "p2", "c2", "run2", "now2")
    rows = repo.conn.execute(
        "SELECT COUNT(*) FROM scan_cursors WHERE election_id='e1' AND cursor_type='news_article_id'"
    ).fetchone()[0]
    assert rows == 1
    assert repo.get_scan_cursor("e1", "news_article_id")["last_article_id"] == 20


def test_reset_test_cursor_requires_test_mode(repo):
    with pytest.raises(PermissionError):
        repo.reset_test_cursor("e1", "news_article_id", allow_test=False)
    assert repo.reset_test_cursor("e1", "news_article_id", allow_test=True)["reset"] is True


def test_business_output_hash_stable_across_run_ids(repo):
    repo.upsert_candidate(_candidate(created_run_id="run_a", updated_run_id="run_a"))
    h1 = repo.business_output_hash()
    repo.upsert_candidate(_candidate(created_run_id="run_b", updated_run_id="run_b"))
    h2 = repo.business_output_hash()
    assert h1 == h2
