from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.election_candidates import review_and_publish as rap
from app.election_candidates.review_workflow import export_review_template

from .publication_helpers import (
    default_event_payload,
    default_sources,
    make_publication_config,
    open_candidate_repo,
    seed_candidate,
)


def _setup(tmp_path: Path, candidate_date="2026-08-10T10:00:00"):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo, canonical_event_date=candidate_date)
    return config, repo


def _write_decision_file(tmp_path, repo, config, decision, *, event=None, sources=None, target=None):
    template = export_review_template(repo, "cand_tnn_abc123", config)
    template["decision"] = decision
    template["reviewer"] = "local_reviewer"
    template["review_reason"] = "human review"
    template["target_formal_event_id"] = target
    if event is not None:
        template["event"] = event
    if sources is not None:
        template["sources"] = sources
    path = tmp_path / f"decision_{decision}.json"
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def test_approve_new_event_one_step_publishes(tmp_path):
    config, repo = _setup(tmp_path)
    path = _write_decision_file(
        tmp_path, repo, config, "approve_new_event",
        event=default_event_payload(), sources=default_sources(),
    )
    result = rap.execute_review_and_publish(
        repo, config, reviewer="local_reviewer", decision_file=path,
        election_id="TW-2026-TNN-MAYOR",
    )
    assert result["review_decision_recorded"] is True
    assert result["publication_attempted"] is True
    assert result["publication_status"] == "committed"
    assert result["formal_validation_status"] == "passed"
    assert result["downstream_refresh_status"] in ("no_change", "pending_review", "committed")
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "published"
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    count = conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0]
    conn.close()
    assert count == 3
    repo.close()


def test_approve_as_subevent_one_step_publishes(tmp_path):
    config, repo = _setup(tmp_path)
    path = _write_decision_file(
        tmp_path, repo, config, "approve_as_subevent",
        event=default_event_payload(), sources=default_sources(),
        target="evt_fix_nom_20260121",
    )
    result = rap.execute_review_and_publish(
        repo, config, reviewer="local_reviewer", decision_file=path,
        election_id="TW-2026-TNN-MAYOR",
    )
    assert result["publication_status"] == "committed"
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "published"
    repo.close()


def test_attach_existing_one_step_updates_formal_state(tmp_path):
    config, repo = _setup(tmp_path)
    path = _write_decision_file(
        tmp_path, repo, config, "attach_to_existing_event",
        event=default_event_payload(), sources=default_sources(reuse=False),
        target="evt_fix_nom_20260121",
    )
    result = rap.execute_review_and_publish(
        repo, config, reviewer="local_reviewer", decision_file=path,
        election_id="TW-2026-TNN-MAYOR",
    )
    assert result["publication_status"] == "committed"
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "published"
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    linked = conn.execute(
        "SELECT source_id FROM event_sources WHERE event_id='evt_fix_nom_20260121'"
    ).fetchall()
    conn.close()
    assert any("newmedia" in r[0] for r in linked)
    events = json.loads(
        "[" + ",".join(
            l for l in (config.path("events_seed").read_text(encoding="utf-8").splitlines())
            if l.strip()
        ) + "]"
    )
    target = next(e for e in events if e["event_id"] == "evt_fix_nom_20260121")
    assert any("newmedia" in s.get("source_id", "") for s in target.get("sources", []))
    repo.close()


@pytest.mark.parametrize("decision", ["reject", "hold", "needs_edit"])
def test_record_only_decisions_do_not_publish(tmp_path, decision):
    config, repo = _setup(tmp_path)
    path = _write_decision_file(tmp_path, repo, config, decision)
    result = rap.execute_review_and_publish(
        repo, config, reviewer="local_reviewer", decision_file=path,
        election_id="TW-2026-TNN-MAYOR",
    )
    assert result["review_decision_recorded"] is True
    assert result["publication_attempted"] is False
    assert result["publication_status"] == "not_attempted"
    assert len(repo.list_publication_batches()) == 0
    if decision == "reject":
        assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "review_rejected"
    else:
        assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "hold"
    repo.close()


def test_prepare_failure_keeps_decision_and_can_retry(tmp_path, monkeypatch):
    config, repo = _setup(tmp_path)
    path = _write_decision_file(
        tmp_path, repo, config, "approve_new_event",
        event=default_event_payload(), sources=default_sources(),
    )

    def failing_prepare(*args, **kwargs):
        raise RuntimeError("injected prepare failure")

    monkeypatch.setattr(rap, "prepare_batch", failing_prepare)
    result = rap.execute_review_and_publish(
        repo, config, reviewer="local_reviewer", decision_file=path,
        election_id="TW-2026-TNN-MAYOR",
    )
    assert result["publication_status"] == "failed"
    assert "injected prepare failure" in result["errors"][0]
    # decision retained; candidate still approved (not unreviewed)
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "review_approved"
    decisions = repo.list_review_decisions("cand_tnn_abc123")
    assert len(decisions) == 1
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    count = conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0]
    conn.close()
    assert count == 2  # no half-write

    monkeypatch.undo()
    retry = rap.execute_review_and_publish(
        repo, config, reviewer="local_reviewer",
        review_decision_id=decisions[0]["review_decision_id"],
        election_id="TW-2026-TNN-MAYOR",
    )
    assert retry["publication_status"] == "committed"
    assert retry["review_decision_recorded"] is False  # no second approval
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "published"
    repo.close()


def test_commit_failure_sets_publication_failed_and_retry_works(tmp_path, monkeypatch):
    config, repo = _setup(tmp_path)
    path = _write_decision_file(
        tmp_path, repo, config, "approve_new_event",
        event=default_event_payload(), sources=default_sources(),
    )
    real_commit = rap.commit_batch

    def failing_commit(*args, **kwargs):
        raise RuntimeError("injected commit failure")

    monkeypatch.setattr(rap, "commit_batch", failing_commit)
    result = rap.execute_review_and_publish(
        repo, config, reviewer="local_reviewer", decision_file=path,
        election_id="TW-2026-TNN-MAYOR",
    )
    assert result["publication_status"] == "failed"
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "publication_failed"
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    count = conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0]
    conn.close()
    assert count == 2

    monkeypatch.setattr(rap, "commit_batch", real_commit)
    rid = repo.list_review_decisions("cand_tnn_abc123")[0]["review_decision_id"]
    retry = rap.execute_review_and_publish(
        repo, config, reviewer="local_reviewer", review_decision_id=rid,
        election_id="TW-2026-TNN-MAYOR",
    )
    assert retry["publication_status"] == "committed"
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "published"
    repo.close()


def test_commit_preserves_nonempty_poll_seeds(tmp_path):
    """Regression: staging must copy poll seeds so commit does not drop polls."""
    config, repo = _setup(tmp_path)
    seed = config.path("events_seed").parent
    (seed / "polls.jsonl").write_text(
        '{"poll_id": "poll_f1_x", "election_id": "TW-2026-TNN-MAYOR", '
        '"poll_type": "primary_poll", "fact_status": "poll_result", '
        '"methodology_complete": 1, "verification_tier": "", '
        '"recommended_disposition": "", "canonical_origin": "x", '
        '"publication_json": "{}", "fieldwork_json": "{}", '
        '"methodology_json": "{}", "population_json": "{}", '
        '"limitations_json": "[]", "usable_for_poll_trend": 1}\n',
        encoding="utf-8",
    )
    (seed / "poll_questions.jsonl").write_text(
        '{"poll_id": "poll_f1_x", "question_id": "q1", "question_type": "head_to_head", '
        '"candidate_set_json": "[]", "base_population": "", "population_filter": "", '
        '"trend_eligible": 0, "trend_scope": "", "comparable_group_key": "", '
        '"note": "", "question_order": 0}\n',
        encoding="utf-8",
    )
    (seed / "poll_results.jsonl").write_text(
        '{"poll_id": "poll_f1_x", "question_id": "q1", "option_id": "o1", '
        '"option_name": "a", "option_type": "candidate", "reported_value": "1%", '
        '"value": 1.0, "normalized_value": null, "unit": "percent", '
        '"base_population": "", "is_derived": 0, "result_order": 0}\n',
        encoding="utf-8",
    )
    (seed / "poll_sources.jsonl").write_text("", encoding="utf-8")
    (seed / "poll_source_links.jsonl").write_text("", encoding="utf-8")

    path = _write_decision_file(
        tmp_path, repo, config, "approve_new_event",
        event=default_event_payload(), sources=default_sources(),
    )
    result = rap.execute_review_and_publish(
        repo, config, reviewer="local_reviewer", decision_file=path,
        election_id="TW-2026-TNN-MAYOR",
    )
    assert result["publication_status"] == "committed"
    assert result["downstream_refresh_status"] not in ("failed",)
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    counts = {
        t: conn.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
        for t in ("election_polls", "poll_questions", "poll_results")
    }
    conn.close()
    assert counts == {"election_polls": 1, "poll_questions": 1, "poll_results": 1}

    from app.election_context.formal_state_hash import (
        formal_state_business_hash_from_db,
        formal_state_business_hash_from_seed_dir,
    )

    assert formal_state_business_hash_from_seed_dir(seed) == formal_state_business_hash_from_db(
        config.path("formal_db")
    )
    repo.close()


def test_retry_after_rollback_without_reapproval(tmp_path):
    """Regression: published + rolled-back batch can be retried from the decision."""
    from app.election_candidates.publication_pipeline import rollback_batch

    config, repo = _setup(tmp_path)
    path = _write_decision_file(
        tmp_path, repo, config, "approve_new_event",
        event=default_event_payload(), sources=default_sources(),
    )
    first = rap.execute_review_and_publish(
        repo, config, reviewer="local_reviewer", decision_file=path,
        election_id="TW-2026-TNN-MAYOR",
    )
    assert first["publication_status"] == "committed"
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "published"

    rollback_batch(repo, config, "TW-2026-TNN-MAYOR", first["batch_id"], "local_reviewer")
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "published"
    rid = repo.list_review_decisions("cand_tnn_abc123")[0]["review_decision_id"]

    retry = rap.execute_review_and_publish(
        repo, config, reviewer="local_reviewer", review_decision_id=rid,
        election_id="TW-2026-TNN-MAYOR",
    )
    assert retry["publication_status"] == "committed"
    assert retry["review_decision_recorded"] is False
    assert repo.get_candidate("cand_tnn_abc123")["review_status"] == "published"
    assert len(repo.list_review_decisions("cand_tnn_abc123")) == 1
    repo.close()
