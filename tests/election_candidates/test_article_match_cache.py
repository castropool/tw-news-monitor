from __future__ import annotations

from app.election_candidates.candidate_repository import CandidateRepository


def test_article_match_cache_upsert_idempotent(tmp_path):
    repo = CandidateRepository(tmp_path / "cand.db")
    repo.connect()
    repo.create_tables()
    match = {
        "news_article_id": "1",
        "election_id": "TW-2026-TNN-MAYOR",
        "match_mode": "inline_classifier",
        "relevance_label": "direct_event",
        "matched_people": ["陳亭妃"],
        "matched_parties": [],
        "matched_issues": ["提名"],
        "matched_basis": ["candidate_match"],
        "match_score": 1.0,
        "classified_at": "2026-08-06T00:00:00",
        "classifier_version": "0.2.0",
    }
    repo.upsert_article_match(match)
    repo.upsert_article_match(match)
    rows = repo.conn.execute(
        "SELECT COUNT(*) FROM candidate_article_matches WHERE news_article_id='1'"
    ).fetchone()[0]
    assert rows == 1
    cached = repo.get_article_match("1", "TW-2026-TNN-MAYOR", "inline_classifier")
    assert cached["relevance_label"] == "direct_event"
    repo.close()
