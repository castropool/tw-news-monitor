from __future__ import annotations

import json

import pytest

from app.election_candidates.publication_pipeline import rollback_batch
from app.election_context.downstream_refresh import (
    create_or_reuse_refresh_batch,
    validate_refresh_request,
)

from .phase3_helpers import make_phase3_env, refresh_batch_id_for, write_request


def test_refresh_request_valid_committed_batch(tmp_path):
    env = make_phase3_env(tmp_path)
    result = validate_refresh_request(env["repo"], env["config"], env["request_path"])
    assert result["request_valid"] is True
    assert result["errors"] == []
    assert result["publication_batch_id"] == env["batch_id"]
    env["repo"].close()


def test_refresh_request_missing_batch(tmp_path):
    env = make_phase3_env(tmp_path)
    path = write_request(
        env["request_path"], env["repo"], env["config"], env["batch_id"],
        publication_batch_id="pub_missing",
    )
    result = validate_refresh_request(env["repo"], env["config"], path)
    assert "publication_batch_id_exists" in result["errors"]
    env["repo"].close()


def test_refresh_request_rejects_prepared_batch(tmp_path):
    env = make_phase3_env(tmp_path)
    batch = env["repo"].get_publication_batch(env["batch_id"])
    batch["status"] = "staged"
    env["repo"].upsert_publication_batch(batch)
    result = validate_refresh_request(env["repo"], env["config"], env["request_path"])
    assert "batch_status_committed" in result["errors"]
    env["repo"].close()


def test_refresh_request_rejects_rolled_back_batch(tmp_path):
    env = make_phase3_env(tmp_path)
    rollback_batch(
        env["repo"], env["config"], "TW-2026-TNN-MAYOR", env["batch_id"],
        "local_reviewer",
    )
    result = validate_refresh_request(env["repo"], env["config"], env["request_path"])
    assert "batch_status_committed" in result["errors"]
    env["repo"].close()


def test_refresh_request_requires_new_ids(tmp_path):
    env = make_phase3_env(tmp_path)
    path = write_request(
        env["request_path"], env["repo"], env["config"], env["batch_id"],
        new_event_ids=[], new_source_ids=[],
    )
    result = validate_refresh_request(env["repo"], env["config"], path)
    assert "new_ids_present" in result["errors"]
    env["repo"].close()


def test_refresh_request_requires_id_fields(tmp_path):
    env = make_phase3_env(tmp_path)
    request = json.loads(env["request_path"].read_text(encoding="utf-8"))
    request.pop("new_event_ids", None)
    request.pop("new_source_ids", None)
    env["request_path"].write_text(json.dumps(request), encoding="utf-8")
    result = validate_refresh_request(env["repo"], env["config"], env["request_path"])
    assert "new_ids_fields_present" in result["errors"]
    env["repo"].close()


def test_refresh_request_hash_mismatch(tmp_path):
    env = make_phase3_env(tmp_path)
    path = write_request(
        env["request_path"], env["repo"], env["config"], env["batch_id"],
        formal_state_hash="deadbeef",
    )
    result = validate_refresh_request(env["repo"], env["config"], path)
    assert "formal_state_hash_matches" in result["errors"]
    env["repo"].close()


def test_refresh_request_election_id_required(tmp_path):
    env = make_phase3_env(tmp_path)
    path = write_request(
        env["request_path"], env["repo"], env["config"], env["batch_id"],
        election_id="",
    )
    result = validate_refresh_request(env["repo"], env["config"], path)
    assert "election_id_present" in result["errors"]
    env["repo"].close()


def test_refresh_batch_one_to_one(tmp_path):
    env = make_phase3_env(tmp_path)
    batch = create_or_reuse_refresh_batch(
        env["repo"], env["config"], env["batch_id"], "h1"
    )
    again = create_or_reuse_refresh_batch(
        env["repo"], env["config"], env["batch_id"], "h1"
    )
    assert again["refresh_batch_id"] == batch["refresh_batch_id"]
    rows = env["repo"].conn.execute(
        "SELECT COUNT(*) FROM downstream_refresh_batches WHERE publication_batch_id=?",
        (env["batch_id"],),
    ).fetchone()[0]
    assert rows == 1
    assert batch["refresh_batch_id"] == refresh_batch_id_for(env["config"], env["batch_id"])
    env["repo"].close()


def test_refresh_batch_stores_period_and_previous(tmp_path):
    env = make_phase3_env(tmp_path)
    batch = create_or_reuse_refresh_batch(
        env["repo"], env["config"], env["batch_id"], "h1",
        previous_coverage_version="fact_coverage_v0",
        previous_snapshot_id="tn_state_old_v1",
        requested_period_start="2026-07-01",
        requested_period_end="2026-07-31",
    )
    assert batch["previous_coverage_version"] == "fact_coverage_v0"
    assert batch["previous_snapshot_id"] == "tn_state_old_v1"
    assert batch["requested_period_start"] == "2026-07-01"
    assert batch["requested_period_end"] == "2026-07-31"
    env["repo"].close()


def test_refresh_batch_status_initial_pending(tmp_path):
    env = make_phase3_env(tmp_path)
    batch = create_or_reuse_refresh_batch(
        env["repo"], env["config"], env["batch_id"], "h1"
    )
    assert batch["status"] == "pending"
    assert batch["coverage_refresh_required"] == 1
    assert batch["snapshot_refresh_required"] == 1
    assert batch["assessment_refresh_required"] == 1
    env["repo"].close()


def test_refresh_request_audit_warning_when_missing(tmp_path):
    env = make_phase3_env(tmp_path)
    env["repo"].conn.execute(
        "DELETE FROM publication_audit_log WHERE batch_id=?", (env["batch_id"],)
    )
    env["repo"].conn.commit()
    result = validate_refresh_request(env["repo"], env["config"], env["request_path"])
    assert result["request_valid"] is True
    assert any("audit" in w for w in result["warnings"])
    env["repo"].close()
