from __future__ import annotations

import json

import pytest

from app.election_candidates.publication_preview import (
    build_preview,
    formal_seed_business_hash,
)
from app.election_candidates.publication_validator import validate_batch

from .publication_helpers import (
    default_event_payload,
    default_sources,
    make_and_save_decision,
    make_publication_config,
    open_candidate_repo,
    seed_candidate,
)


def _make_approval(tmp_path, config, repo, cid="cand_tnn_abc123", event=None, sources=None, target=None, decision="approve_new_event"):
    if repo.get_candidate(cid) is None:
        seed_candidate(repo, cid=cid)
    return make_and_save_decision(
        repo, config, tmp_path, cid, decision,
        event=event or default_event_payload(),
        sources=sources if sources is not None else default_sources(),
        target=target,
    )


def test_preview_does_not_write_formal_data(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    _make_approval(tmp_path, config, repo)
    before = formal_seed_business_hash(config)
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [repo.get_latest_review_decision("cand_tnn_abc123")["review_decision_id"]])
    after = formal_seed_business_hash(config)
    assert before == after
    assert preview["errors"] == []
    repo.close()


def test_preview_creates_batch_and_items(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    decision = _make_approval(tmp_path, config, repo)
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    batch = repo.get_publication_batch(preview["batch_id"])
    assert batch["status"] == "draft"
    items = repo.list_publication_items(preview["batch_id"])
    assert any(i["operation_type"] == "create_event" for i in items)
    assert any(i["operation_type"] == "link_event_source" for i in items)
    repo.close()


def test_preview_event_id_stable(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    decision = _make_approval(tmp_path, config, repo)
    p1 = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    p2 = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    assert p1["new_events"][0]["event_id"] == p2["new_events"][0]["event_id"]
    repo.close()


def test_preview_new_source_approved(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    decision = _make_approval(tmp_path, config, repo, sources=default_sources(reuse=False))
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    assert preview["errors"] == []
    assert len(preview["new_sources"]) == 1
    repo.close()


def test_preview_unresolved_source_blocks(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    sources = [
        {
            "source_name": "未知來源",
            "domain": "",
            "formal_source_id": "",
            "formal_match_status": "unresolved",
            "approve_new_source": False,
        }
    ]
    decision = _make_approval(tmp_path, config, repo, sources=sources)
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    assert any("unresolved_sources" in e for e in preview["errors"])
    repo.close()


def test_validator_blocks_duplicate_new_event(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    event = default_event_payload(
        event_date="2026-01-21T10:00:00+08:00",
        event_type="party_nomination",
        title="民进党正式提名陈亭妃",
        actors=["陳亭妃"],
        themes=["提名"],
        observed_facts=["民进党正式提名陈亭妃"],
    )
    decision = _make_approval(tmp_path, config, repo, event=event)
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    validation = validate_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview)
    assert any("duplicate_new_event" in e for e in validation["errors"])
    repo.close()


def test_validator_blocks_stale_review(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    decision = _make_approval(tmp_path, config, repo)
    repo.attach_article(
        {
            "candidate_id": "cand_tnn_abc123",
            "news_article_id": "777",
            "relationship_type": "same_event",
            "is_anchor": 0,
            "article_title": "漂移",
            "article_url": "https://a.com/777",
            "source_name": "中央社",
            "published_at": "2026-08-13T10:00:00",
            "event_date_candidate": "",
            "event_date_basis": "",
            "match_score": 1.0,
            "attached_run_id": "run_4",
        }
    )
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    assert any("stale_review" in e for e in preview["errors"])
    validation = validate_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview)
    assert any("review_decision_stale" in e for e in validation["errors"])
    repo.close()


def test_validator_blocks_unsafe_fact_promotion(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    event = default_event_payload(
        observed_facts=["藍白合作已經完成"],
        attributed_statements=[],
    )
    decision = _make_approval(tmp_path, config, repo, event=event)
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    validation = validate_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview)
    assert "unsafe_fact_promotion_count" in validation["errors"]
    repo.close()


def test_validator_blocks_uncertain_promoted_to_fact(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    event = default_event_payload(observed_facts=["據悉謝龍介將參選"])
    decision = _make_approval(tmp_path, config, repo, event=event)
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    validation = validate_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview)
    assert "uncertain_report_promoted_to_fact_count" in validation["errors"]
    repo.close()


def test_validator_blocks_planned_promoted_to_completed(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    event = default_event_payload(observed_facts=["謝龍介擬於月底舉辦造勢晚會"])
    decision = _make_approval(tmp_path, config, repo, event=event)
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    validation = validate_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview)
    assert "planned_action_promoted_to_completed_fact_count" in validation["errors"]
    repo.close()


def test_validator_blocks_media_promoted_to_fact(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    event = default_event_payload(observed_facts=["媒體分析謝龍介選情領先"])
    decision = _make_approval(tmp_path, config, repo, event=event)
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    validation = validate_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview)
    assert "media_interpretation_promoted_to_fact_count" in validation["errors"]
    repo.close()


def test_validator_blocks_unattributed_allegation(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    event = default_event_payload(allegations=["謝龍介指控對手賄選"])
    decision = _make_approval(tmp_path, config, repo, event=event)
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    validation = validate_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview)
    assert "unattributed_allegation_count" in validation["errors"]
    repo.close()


def test_validator_requires_event_date_and_type(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    event = default_event_payload(event_date="", event_type="unknown")
    decision = _make_approval(tmp_path, config, repo, event=event)
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    validation = validate_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview)
    assert any("event_date_valid" in e for e in validation["errors"])
    assert any("event_type_valid" in e for e in validation["errors"])
    repo.close()


def test_validator_attach_target_missing_blocks(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    decision = _make_approval(
        tmp_path, config, repo, decision="attach_to_existing_event",
        target="evt_missing",
    )
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    assert any("target_event_not_found" in e for e in preview["errors"])
    repo.close()


def test_validator_attach_target_exists(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    decision = _make_approval(
        tmp_path, config, repo, decision="attach_to_existing_event",
        target="evt_fix_nom_20260121",
    )
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    assert preview["errors"] == []
    assert any(i["operation_type"] == "attach_source" for i in preview["items"])
    repo.close()


def test_validator_reviewer_and_latest(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    decision = _make_approval(tmp_path, config, repo)
    # second (older) decision no longer latest
    make_and_save_decision(
        repo, config, tmp_path, "cand_tnn_abc123", "hold",
        event=default_event_payload(), sources=default_sources(),
    )
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    validation = validate_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview)
    assert "review_decision_latest" in validation["errors"]
    repo.close()


def test_preview_files_written(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    decision = _make_approval(tmp_path, config, repo)
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision["review_decision_id"]])
    batch_dir = config.path("output_root") / "publication_batches" / preview["batch_id"]
    assert (batch_dir / "publication_preview.json").exists()
    assert (batch_dir / "publication_manifest.json").exists() is False
    repo.close()
