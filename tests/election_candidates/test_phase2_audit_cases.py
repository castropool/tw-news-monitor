from __future__ import annotations

import json

import pytest

from app.election_candidates.formal_diff import diff_links, diff_rows, write_formal_diff
from app.election_candidates.publication_pipeline import (
    batch_hash,
    commit_batch,
    prepare_batch,
    rollback_batch,
)
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


def _case(tmp_path, decision="approve_new_event", event=None, sources=None, target=None):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    d = make_and_save_decision(
        repo, config, tmp_path, "cand_tnn_abc123", decision,
        event=event or default_event_payload(),
        sources=sources if sources is not None else default_sources(),
        target=target,
    )
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [d["review_decision_id"]])
    return config, repo, preview


def _commit(config, repo, preview):
    prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    return commit_batch(
        repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"],
        "local_reviewer", batch_hash(preview), preview,
    )


def test_case_a_new_event_existing_source(tmp_path):
    config, repo, preview = _case(tmp_path)
    result = _commit(config, repo, preview)
    assert result["commit_ready"] is True
    assert len(preview["new_events"]) == 1
    assert len(preview["new_sources"]) == 0
    repo.close()


def test_case_b_new_event_new_source(tmp_path):
    config, repo, preview = _case(tmp_path, sources=default_sources(reuse=False))
    assert len(preview["new_sources"]) == 1
    result = _commit(config, repo, preview)
    assert result["commit_ready"] is True
    repo.close()


def test_case_c_attach_source_to_existing_event(tmp_path):
    config, repo, preview = _case(
        tmp_path, decision="attach_to_existing_event", target="evt_fix_nom_20260121"
    )
    assert any(i["operation_type"] == "attach_source" for i in preview["items"])
    result = _commit(config, repo, preview)
    assert result["commit_ready"] is True
    repo.close()


def test_case_d_duplicate_new_event_blocked(tmp_path):
    event = default_event_payload(
        event_date="2026-01-21T10:00:00+08:00",
        event_type="party_nomination",
        title="民进党正式提名陈亭妃",
        actors=["陳亭妃"],
        themes=["提名"],
        observed_facts=["民进党正式提名陈亭妃"],
    )
    config, repo, preview = _case(tmp_path, event=event)
    validation = validate_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview)
    assert any("duplicate_new_event" in e for e in validation["errors"])
    with pytest.raises(ValueError):
        prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    repo.close()


def test_case_e_stale_review_blocked(tmp_path):
    config, repo, preview = _case(tmp_path)
    decision_id = preview["review_decision_ids"][0]
    repo.attach_article(
        {
            "candidate_id": "cand_tnn_abc123",
            "news_article_id": "888",
            "relationship_type": "same_event",
            "is_anchor": 0,
            "article_title": "漂移",
            "article_url": "https://a.com/888",
            "source_name": "中央社",
            "published_at": "2026-08-14T10:00:00",
            "event_date_candidate": "",
            "event_date_basis": "",
            "match_score": 1.0,
            "attached_run_id": "run_5",
        }
    )
    preview2 = build_preview(
        repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [decision_id]
    )
    assert any("stale_review" in e for e in preview2["errors"])
    repo.close()


def test_case_f_unsafe_fact_blocked(tmp_path):
    event = default_event_payload(observed_facts=["謝龍介指控對手買票"])
    config, repo, preview = _case(tmp_path, event=event)
    validation = validate_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview)
    assert any("unattributed_allegation_count" in e or "unsafe_fact_promotion_count" in e for e in validation["errors"])
    repo.close()


def test_case_g_prepare_then_commit(tmp_path):
    config, repo, preview = _case(tmp_path)
    prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    result = commit_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer", batch_hash(preview), preview)
    assert result["commit_ready"] is True
    repo.close()


def test_case_h_crash_recovery(tmp_path):
    config, repo, preview = _case(tmp_path)
    prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    from app.election_candidates.publication_pipeline import PublicationFault, detect_recovery_required

    with pytest.raises(PublicationFault):
        commit_batch(
            repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"],
            "local_reviewer", batch_hash(preview), preview,
            faults={"fail_during_db_replace": True},
        )
    assert detect_recovery_required(config, preview["batch_id"])["recovery_required"] is True
    repo.close()


def test_case_i_rollback_restores_hash(tmp_path):
    config, repo, preview = _case(tmp_path)
    before = formal_seed_business_hash(config)
    _commit(config, repo, preview)
    result = rollback_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], "local_reviewer")
    assert result["formal_data_hash_after_rollback"] == before
    repo.close()


def test_case_j_duplicate_commit_rejected(tmp_path):
    config, repo, preview = _case(tmp_path)
    _commit(config, repo, preview)
    with pytest.raises(ValueError):
        _commit(config, repo, preview)
    repo.close()


def test_diff_events_added():
    before = [{"event_id": "e1", "title": "a"}]
    after = [{"event_id": "e1", "title": "a"}, {"event_id": "e2", "title": "b"}]
    d = diff_rows(before, after, "event_id")
    assert d["added"] == ["e2"]
    assert d["removed"] == []


def test_diff_events_removed_and_modified():
    before = [{"event_id": "e1", "title": "a"}, {"event_id": "e2", "title": "b"}]
    after = [{"event_id": "e1", "title": "a"}]
    d = diff_rows(before, after, "event_id")
    assert d["removed"] == ["e2"]
    before2 = [{"event_id": "e1", "title": "a"}]
    after2 = [{"event_id": "e1", "title": "c"}]
    assert diff_rows(before2, after2, "event_id")["modified"] == ["e1"]


def test_diff_links_added_removed():
    before = [{"event_id": "e1", "source_id": "s1"}]
    after = [{"event_id": "e1", "source_id": "s1"}, {"event_id": "e1", "source_id": "s2"}]
    d = diff_links(before, after)
    assert d["added"] == ["e1|s2"]
    assert d["removed"] == []


def test_write_formal_diff(tmp_path):
    payload = {
        "events_diff": {"added": ["e2"], "removed": [], "modified": []},
        "sources_diff": {"added": [], "removed": [], "modified": []},
        "links_diff": {"added": ["e2|s1"], "removed": []},
        "snapshot_changed": False,
        "coverage_changed": False,
        "poll_changed": False,
    }
    path = write_formal_diff(payload, payload, tmp_path / "diff.json")
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data["events_added"] == ["e2"]
    assert data["snapshot_changed"] is False


def test_audit_log_append_only(tmp_path):
    config, repo, preview = _case(tmp_path)
    _commit(config, repo, preview)
    before = len(repo.list_publication_audit(preview["batch_id"]))
    repo.append_publication_audit(
        {
            "audit_id": "aud_extra",
            "batch_id": preview["batch_id"],
            "candidate_id": "",
            "review_decision_id": "",
            "reviewer": "local_reviewer",
            "action": "test",
            "event_id": "",
            "source_ids": [],
            "timestamp": "2026-08-07T00:00:00",
            "formal_hash_before": "",
            "formal_hash_after": "",
            "result": "success",
            "reason": "append test",
        }
    )
    assert len(repo.list_publication_audit(preview["batch_id"])) == before + 1
    repo.close()


def test_downstream_refresh_markers(tmp_path):
    config, repo, preview = _case(tmp_path)
    _commit(config, repo, preview)
    batch_dir = config.path("output_root") / "publication_batches" / preview["batch_id"]
    marker = json.loads((batch_dir / "downstream_refresh_request.json").read_text(encoding="utf-8"))
    assert marker["snapshot_refresh_required"] is True
    assert marker["coverage_refresh_required"] is True
    assert marker["assessment_refresh_required"] is True
    repo.close()


def test_audit_md_sections(tmp_path):
    config, repo, preview = _case(tmp_path)
    _commit(config, repo, preview)
    batch_dir = config.path("output_root") / "publication_batches" / preview["batch_id"]
    md = (batch_dir / "publication_audit.md").read_text(encoding="utf-8")
    for section in ["一、发布批次", "二、审核人", "十二、回滚信息"]:
        assert section in md
    repo.close()


def test_post_commit_searchable(tmp_path):
    import sqlite3

    config, repo, preview = _case(tmp_path)
    _commit(config, repo, preview)
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    row = conn.execute(
        "SELECT COUNT(*) FROM election_events_fts WHERE title LIKE ?",
        (f"%{preview['new_events'][0]['title'][:10]}%",),
    ).fetchone()
    conn.close()
    assert row[0] >= 1
    repo.close()


def test_old_events_unchanged_after_commit(tmp_path):
    import sqlite3

    config, repo, preview = _case(tmp_path)
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    before = conn.execute(
        "SELECT title FROM election_events WHERE event_id='evt_fix_nom_20260121'"
    ).fetchone()[0]
    conn.close()
    _commit(config, repo, preview)
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    after = conn.execute(
        "SELECT title FROM election_events WHERE event_id='evt_fix_nom_20260121'"
    ).fetchone()[0]
    conn.close()
    assert before == after
    repo.close()


def test_no_secrets_in_backup(tmp_path):
    config, repo, preview = _case(tmp_path)
    from app.election_candidates.publication_pipeline import create_backup

    backup = create_backup(config, preview["batch_id"], preview)
    names = [p.name for p in backup.iterdir()]
    assert ".env" not in names
    assert "api_key" not in " ".join(names).lower()
    assert "webhook" not in " ".join(names).lower()
    repo.close()
