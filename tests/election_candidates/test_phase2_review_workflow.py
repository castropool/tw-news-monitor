from __future__ import annotations

import json

import pytest

from app.election_candidates.review_workflow import (
    ALLOWED_DECISIONS,
    candidate_business_hash,
    export_review_template,
    is_review_stale,
    save_review_decision,
)

from .publication_helpers import (
    default_event_payload,
    default_sources,
    make_and_save_decision,
    make_publication_config,
    open_candidate_repo,
    seed_candidate,
)


def test_allowed_decisions():
    assert ALLOWED_DECISIONS == {
        "approve_new_event", "attach_to_existing_event", "approve_as_subevent",
        "reject", "hold", "needs_edit",
    }


def test_reviewer_required(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    template = export_review_template(repo, "cand_tnn_abc123", config)
    template["decision"] = "approve_new_event"
    template["reviewer"] = "system"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        save_review_decision(repo, path, "system", config)
    repo.close()


def test_illegal_decision(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    template = export_review_template(repo, "cand_tnn_abc123", config)
    template["decision"] = "auto_approve"
    template["reviewer"] = "local_reviewer"
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    with pytest.raises(ValueError):
        save_review_decision(repo, path, "local_reviewer", config)
    repo.close()


def test_approve_new_event_decision(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    decision = make_and_save_decision(
        repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
        event=default_event_payload(), sources=default_sources(),
    )
    assert decision["decision"] == "approve_new_event"
    assert decision["reviewer"] == "local_reviewer"
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "review_approved"
    repo.close()


def test_attach_existing_requires_target(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    with pytest.raises(ValueError):
        make_and_save_decision(
            repo, config, tmp_path, "cand_tnn_abc123", "attach_to_existing_event",
            event=default_event_payload(), sources=default_sources(), target=None,
        )
    repo.close()


def test_attach_existing_decision(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    decision = make_and_save_decision(
        repo, config, tmp_path, "cand_tnn_abc123", "attach_to_existing_event",
        event=default_event_payload(), sources=default_sources(),
        target="evt_fix_nom_20260121",
    )
    assert decision["target_formal_event_id"] == "evt_fix_nom_20260121"
    repo.close()


def test_reject_decision(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "reject")
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "review_rejected"
    repo.close()


def test_hold_decision(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "hold")
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "hold"
    repo.close()


def test_needs_edit_decision(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "needs_edit")
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "hold"
    repo.close()


def test_decision_history_append_only(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "hold")
    make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "needs_edit")
    make_and_save_decision(
        repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
        event=default_event_payload(), sources=default_sources(),
    )
    history = repo.list_review_decisions("cand_tnn_abc123")
    assert len(history) == 3
    assert [h["decision"] for h in history] == ["hold", "needs_edit", "approve_new_event"]
    repo.close()


def test_stale_review_blocked(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    template = export_review_template(repo, "cand_tnn_abc123", config)
    template["decision"] = "approve_new_event"
    template["reviewer"] = "local_reviewer"
    template["event"] = default_event_payload()
    template["sources"] = default_sources()
    path = tmp_path / "decision.json"
    path.write_text(json.dumps(template, ensure_ascii=False), encoding="utf-8")
    # drift the candidate
    repo.attach_article(
        {
            "candidate_id": "cand_tnn_abc123",
            "news_article_id": "99",
            "relationship_type": "same_event",
            "is_anchor": 0,
            "article_title": "後續報導",
            "article_url": "https://a.com/99",
            "source_name": "中央社",
            "published_at": "2026-08-11T10:00:00",
            "event_date_candidate": "",
            "event_date_basis": "",
            "match_score": 1.0,
            "attached_run_id": "run_2",
        }
    )
    with pytest.raises(ValueError):
        save_review_decision(repo, path, "local_reviewer", config)
    repo.close()


def test_is_review_stale_detection(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    decision = make_and_save_decision(
        repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
        event=default_event_payload(), sources=default_sources(),
    )
    assert is_review_stale(repo, decision) is False
    repo.attach_article(
        {
            "candidate_id": "cand_tnn_abc123",
            "news_article_id": "100",
            "relationship_type": "same_event",
            "is_anchor": 0,
            "article_title": "新報導",
            "article_url": "https://a.com/100",
            "source_name": "中央社",
            "published_at": "2026-08-12T10:00:00",
            "event_date_candidate": "",
            "event_date_basis": "",
            "match_score": 1.0,
            "attached_run_id": "run_3",
        }
    )
    assert is_review_stale(repo, decision) is True
    repo.close()


def test_candidate_business_hash_stable(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    h1 = candidate_business_hash(repo, "cand_tnn_abc123")
    h2 = candidate_business_hash(repo, "cand_tnn_abc123")
    assert h1 == h2
    repo.close()


def test_latest_decision_wins(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    make_and_save_decision(repo, config, tmp_path, "cand_tnn_abc123", "hold")
    latest = repo.get_latest_review_decision("cand_tnn_abc123")
    assert latest["decision"] == "hold"
    repo.close()
