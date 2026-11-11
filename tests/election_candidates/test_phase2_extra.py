from __future__ import annotations

import json

from app.election_candidates.publication_pipeline import (
    batch_hash,
    detect_recovery_required,
)
from app.election_candidates.publication_preview import build_preview

from .publication_helpers import (
    default_event_payload,
    default_sources,
    make_and_save_decision,
    make_publication_config,
    open_candidate_repo,
    seed_candidate,
)


def _preview(tmp_path):
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    d = make_and_save_decision(
        repo, config, tmp_path, "cand_tnn_abc123", "approve_new_event",
        event=default_event_payload(), sources=default_sources(),
    )
    preview = build_preview(repo, config, "TW-2026-TNN-MAYOR", "local_reviewer", [d["review_decision_id"]])
    return config, repo, preview


def test_batch_hash_stable(tmp_path):
    _, _, preview = _preview(tmp_path)
    assert batch_hash(preview) == batch_hash(preview)


def test_detect_recovery_clean_after_prepare(tmp_path):
    from app.election_candidates.publication_pipeline import prepare_batch

    config, repo, preview = _preview(tmp_path)
    prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    recovery = detect_recovery_required(config, preview["batch_id"])
    assert recovery["recovery_required"] is False
    repo.close()


def test_preview_markdown_generated(tmp_path):
    config, repo, preview = _preview(tmp_path)
    batch_dir = config.path("output_root") / "publication_batches" / preview["batch_id"]
    md = (batch_dir / "publication_preview.md").read_text(encoding="utf-8")
    assert "发布预览" in md
    assert preview["new_events"][0]["event_id"] in md
    repo.close()


def test_publication_items_consistent(tmp_path):
    config, repo, preview = _preview(tmp_path)
    items = repo.list_publication_items(preview["batch_id"])
    assert len(items) == len(preview["items"])
    ops = {i["operation_type"] for i in items}
    assert ops <= {"create_event", "attach_source", "create_source", "link_event_source"}
    repo.close()


def test_journal_written_after_prepare(tmp_path):
    from app.election_candidates.publication_pipeline import prepare_batch

    config, repo, preview = _preview(tmp_path)
    prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    journal_path = config.path("output_root") / "publication_batches" / preview["batch_id"] / "publication_commit_journal.json"
    journal = json.loads(journal_path.read_text(encoding="utf-8"))
    assert journal["steps"]["prepared"] is True
    assert journal["steps"]["backup_complete"] is True
    repo.close()


def test_rollback_plan_written(tmp_path):
    config, repo, preview = _preview(tmp_path)
    from app.election_candidates.publication_pipeline import prepare_batch

    prepare_batch(repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview, "local_reviewer")
    batch_dir = config.path("output_root") / "publication_batches" / preview["batch_id"]
    assert (batch_dir / "rollback_plan.json").exists()
    repo.close()
