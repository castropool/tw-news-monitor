"""Auto-publish -> post-publication downstream wiring tests.

Covers the deterministic gate chain: committed batch -> snapshot activation ->
coverage activation (staging -> atomic) -> R2 selector picks the new ready
version.  Every failure marks ``downstream_failed``, stops the round, feeds the
circuit breaker and never leaves a selectable half-product.  All fixtures are
tmp_path isolated; nothing touches production data or installs tasks.
"""

from __future__ import annotations

import argparse
import json
import sqlite3

import pytest

import app.election_candidates.auto_publish_candidates as auto_pub
from app.assessment.evidence_pack_builder import select_coverage_version
from app.election_candidates.auto_publish_candidates import AutoPublishManifest, run_auto_publish
from app.election_candidates.auto_publish_gate import AutoPublishPolicy
from app.election_context.formal_state_hash import (
    formal_state_business_hash_from_db,
    formal_state_business_hash_from_seed_dir,
)

from .publication_helpers import open_candidate_repo
from .test_auto_publish import (
    formal_event_count,
    make_auto_config,
    manifest_records,
    run,
    seed_eligible,
)

FACTS_CUTOFF = "2026-07-27"


def seed_milestone(repo, **over):
    """Low-risk auto-publishable candidate whose event type is a snapshot
    milestone (primary_result -> deterministic auto-activatable snapshot)."""
    over.setdefault("candidate_event_type", "primary_result")
    over.setdefault("candidate_title", "王定宇台南市長初選勝出")
    over.setdefault("keywords_json", '["王定宇","初選"]')
    over.setdefault("themes_json", '["初選"]')
    return seed_eligible(repo, **over)


@pytest.fixture
def pub_env(tmp_path, monkeypatch):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_milestone(repo)
    repo.close()
    # Authoritative reviewed-through cutoff, data-driven, never hardcoded into
    # the flow; patched so tests never read repo-local production files.
    monkeypatch.setattr(
        "app.election_candidates.review_completion.facts_cutoff_for_refresh",
        lambda *a, **k: FACTS_CUTOFF,
    )
    return {"config": config, "tmp_path": tmp_path}


def _active_snapshot_id(config):
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    row = conn.execute(
        "SELECT snapshot_id FROM election_state_snapshots WHERE snapshot_status='active'"
    ).fetchone()
    conn.close()
    return row[0]


def _coverage_dirs(config):
    seed = config.path("events_seed").parent
    return sorted(
        p.name for p in seed.iterdir()
        if p.is_dir() and p.name.startswith("fact_coverage_")
    )


# ---------------------------------------------------------------------------
# happy path: publish -> snapshot -> coverage -> selector
# ---------------------------------------------------------------------------
def test_happy_path_publish_snapshot_coverage_selector(pub_env):
    config = pub_env["config"]
    result = run(config)
    assert result["status"] == "completed"
    assert result["published"] == 1
    assert result["failed"] == 0

    recs = manifest_records(config)
    pub = [r for r in recs if r["status"] == "published"][-1]
    assert pub["downstream_status"] == "ok"
    assert pub["snapshot_id"]
    assert pub["coverage_version"]
    assert pub["facts_cutoff"] == FACTS_CUTOFF
    assert pub["formal_hash_before"] and pub["formal_hash_after"]
    assert pub["formal_hash_before"] != pub["formal_hash_after"]

    # snapshot activated in the formal DB
    assert _active_snapshot_id(config) == pub["snapshot_id"]

    # R2 selector picks exactly the newly activated coverage
    seed = config.path("events_seed").parent
    path, name, preflight, validation = select_coverage_version(seed)
    assert name == pub["coverage_version"]
    assert preflight["preflight_ready"] is True
    assert validation["coverage_ready"] is True
    assert preflight["facts_cutoff"] == FACTS_CUTOFF

    # committed facts are the downstream input; no re-import / second bootstrap
    assert formal_event_count(config) == 3
    assert (
        formal_state_business_hash_from_seed_dir(seed)
        == formal_state_business_hash_from_db(config.path("formal_db"))
    )


def test_rerun_after_success_is_idempotent(pub_env):
    config = pub_env["config"]
    first = run(config)
    seed_dir = config.path("events_seed").parent
    dirs_after_first = _coverage_dirs(config)
    name1 = select_coverage_version(seed_dir)[1]
    active1 = _active_snapshot_id(config)

    # plain rerun: the published candidate left the queue; nothing is re-created
    second = run(config)
    assert second["status"] == "completed"
    assert second["published"] == 0
    assert second["failed"] == 0
    assert _coverage_dirs(config) == dirs_after_first
    assert select_coverage_version(seed_dir)[1] == name1
    assert _active_snapshot_id(config) == active1

    # simulated re-entry (bulk refresh) -> idempotency skip, no duplicate
    # snapshot / coverage products, completed manifest reused
    repo = open_candidate_repo(config)
    cand = repo.get_candidate("cand_tnn_aaa001")
    cand["review_status"] = "review_required"
    repo.upsert_candidate(cand, preserve_terminal_status=False)
    repo.close()
    third = run(config)
    assert third["status"] == "completed"
    assert third["skipped"] == 1
    assert third["published"] == 0
    assert _coverage_dirs(config) == dirs_after_first
    assert select_coverage_version(seed_dir)[1] == name1
    assert _active_snapshot_id(config) == active1
    pub_recs = [r for r in manifest_records(config) if r["status"] == "published"]
    assert len(pub_recs) == 1
    assert pub_recs[0]["downstream_status"] == "ok"


# ---------------------------------------------------------------------------
# failure isolation: snapshot
# ---------------------------------------------------------------------------
def test_snapshot_failure_keeps_old_active_and_marks_downstream_failed(pub_env, monkeypatch):
    config = pub_env["config"]
    before_active = _active_snapshot_id(config)

    def boom(*a, **k):
        raise RuntimeError("injected snapshot commit failure")

    monkeypatch.setattr(
        "app.election_context.run_post_publication_pipeline.commit_snapshot", boom
    )
    result = run(config)
    assert result["status"] == "completed"
    assert result["published"] == 0
    assert result["failed"] == 1

    rec = [r for r in manifest_records(config) if r["status"] == "published"][-1]
    assert rec["downstream_status"] == "failed"
    assert "snapshot" in rec["downstream_error"]

    # old active snapshot preserved; committed facts not rolled back
    assert _active_snapshot_id(config) == before_active
    assert (
        formal_state_business_hash_from_db(config.path("formal_db"))
        == rec["formal_state_hash_after"]
    )
    assert formal_event_count(config) == 3

    repo = open_candidate_repo(config)
    batch = repo.get_refresh_batch_by_publication(rec["batch_id"])
    assert batch and batch["status"] == "failed"
    repo.close()


# ---------------------------------------------------------------------------
# failure isolation: coverage
# ---------------------------------------------------------------------------
def test_coverage_failure_keeps_facts_and_blocks_selector(pub_env, monkeypatch):
    config = pub_env["config"]

    def boom(*a, **k):
        raise RuntimeError("injected coverage failure")

    monkeypatch.setattr(
        "app.election_context.run_post_publication_pipeline.build_coverage", boom
    )
    result = run(config)
    assert result["failed"] == 1
    rec = [r for r in manifest_records(config) if r["status"] == "published"][-1]
    assert rec["downstream_status"] == "failed"
    assert "coverage" in rec["downstream_error"]

    # committed facts intact (hash matches the committed batch), no selectable
    # coverage appeared
    assert (
        formal_state_business_hash_from_db(config.path("formal_db"))
        == rec["formal_state_hash_after"]
    )
    assert formal_event_count(config) == 3
    assert _coverage_dirs(config) == []
    with pytest.raises(Exception):
        select_coverage_version(config.path("events_seed").parent)


def test_failed_activation_isolated_and_never_selectable(tmp_path, monkeypatch):
    from app.election_context import coverage_activation as ca

    config = make_auto_config(tmp_path)
    from app.election_context.coverage_builder import build_coverage
    from app.election_context.coverage_validator import validate_coverage

    cov = build_coverage(
        config,
        requested_start="2026-07-16",
        requested_end="2026-07-31",
        facts_cutoff=FACTS_CUTOFF,
    )
    validation = validate_coverage(config, cov["coverage"], cov["manifest"])
    assert validation["coverage_ready"] is True

    # a ready-looking staging dir is never selectable (subdirectory, not name)
    staging = ca.coverage_staging_root(config) / cov["coverage_version"]
    ca.stage_coverage(
        config, cov, validation,
        refresh_batch_id="dr_x", active_snapshot_id="tn_state_fix_v1",
    )
    seed = config.path("events_seed").parent
    with pytest.raises(Exception):
        select_coverage_version(seed)

    # injected failure at the atomic rename -> isolated to staging/failed
    def boom_rename(src, dst):
        raise RuntimeError("injected rename failure")

    monkeypatch.setattr(ca, "_atomic_rename_dir", boom_rename)
    with pytest.raises(RuntimeError, match="rename failure"):
        ca.activate_coverage(
            config, cov, validation,
            refresh_batch_id="dr_x", active_snapshot_id="tn_state_fix_v1",
            allow_real=True,
        )
    failed = list((seed / "staging" / "failed").glob("fact_coverage_*"))
    assert failed and (failed[0] / "failure_reason.json").exists()
    assert (seed / "staging" / cov["coverage_version"]).exists() is False
    with pytest.raises(Exception):
        select_coverage_version(seed)

    # isolated dirs are also not selectable even when placed in the root
    isolated = failed[0]
    renamed = seed / isolated.name
    isolated.rename(renamed)
    with pytest.raises(Exception):
        select_coverage_version(seed)


def test_coverage_activation_idempotent_reuse_and_collision(tmp_path, monkeypatch):
    from app.election_context import coverage_activation as ca
    from app.election_context.coverage_builder import build_coverage
    from app.election_context.coverage_validator import validate_coverage

    config = make_auto_config(tmp_path)
    cov = build_coverage(
        config,
        requested_start="2026-07-16",
        requested_end="2026-07-31",
        facts_cutoff=FACTS_CUTOFF,
    )
    validation = validate_coverage(config, cov["coverage"], cov["manifest"])

    first = ca.activate_coverage(
        config, cov, validation,
        refresh_batch_id="dr_a", active_snapshot_id="tn_state_fix_v1",
        allow_real=True,
    )
    assert first["activated"] is True
    second = ca.activate_coverage(
        config, cov, validation,
        refresh_batch_id="dr_b", active_snapshot_id="tn_state_fix_v1",
        allow_real=True,
    )
    assert second["reused"] is True  # same version + business hash -> no-op

    # different content, same version name -> collision -> failed + isolated
    tampered = dict(cov)
    tampered["manifest"] = dict(cov["manifest"])
    tampered["manifest"]["business_hash"] = "tampered"
    tampered["business_hash"] = "tampered"
    with pytest.raises(RuntimeError, match="collision"):
        ca.activate_coverage(
            config, tampered, validation,
            refresh_batch_id="dr_c", active_snapshot_id="tn_state_fix_v1",
            allow_real=True,
        )
    seed = config.path("events_seed").parent
    assert list((seed / "staging" / "failed").glob("fact_coverage_*"))


# ---------------------------------------------------------------------------
# deterministic gates
# ---------------------------------------------------------------------------
def test_input_hash_drift_detected(pub_env, monkeypatch):
    config = pub_env["config"]
    real_publish = auto_pub.publish_one

    def publish_then_tamper(*a, **k):
        outcome = real_publish(*a, **k)
        conn = sqlite3.connect(config.path("formal_db"))
        conn.execute(
            "INSERT INTO election_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                "evt_drift_20260812", "TW-2026-TNN-MAYOR", "2026-08-12T10:00:00+08:00",
                "campaign_event", "漂移事件", "漂移事件",
                "verified", 50, '["王定宇"]', "[]", "[]",
                "{}", "2026-08-12T00:00:00", "2026-08-12T00:00:00",
            ),
        )
        conn.commit()
        conn.close()
        return outcome

    monkeypatch.setattr(auto_pub, "publish_one", publish_then_tamper)
    result = run(config)
    assert result["failed"] == 1
    rec = [r for r in manifest_records(config) if r["status"] == "published"][-1]
    assert rec["downstream_status"] == "failed"
    assert "input_hash_drift" in rec["downstream_error"]
    # the tampered fact is NOT rolled back (publication transaction independent)
    assert formal_event_count(config) == 4


def test_pending_downstream_journal_blocks(pub_env):
    config = pub_env["config"]
    post_root = config.path("post_publication_root")
    run_dir = post_root / "dr_fake_pending"
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "downstream_refresh_journal.json").write_text(
        json.dumps({"steps": {"coverage_started": True}}), encoding="utf-8"
    )
    result = run(config)
    assert result["failed"] == 1
    rec = [r for r in manifest_records(config) if r["status"] == "published"][-1]
    assert rec["downstream_status"] == "failed"
    assert "recovery gate" in rec["downstream_error"]
    # no coverage product created anywhere
    assert _coverage_dirs(config) == []


# ---------------------------------------------------------------------------
# circuit breaker / stop on downstream failure
# ---------------------------------------------------------------------------
def test_downstream_failure_counts_toward_circuit(pub_env, monkeypatch):
    config = pub_env["config"]

    def boom(*a, **k):
        raise RuntimeError("injected downstream failure")

    monkeypatch.setattr(
        "app.election_context.run_post_publication_pipeline.run_post_publication_pipeline",
        boom,
    )
    policy = AutoPublishPolicy.from_config(config)

    first = run(config)
    assert first["status"] == "completed"
    assert first["failed"] == 1
    assert first["circuit_open"] is False
    assert not policy.circuit_break_file.exists()
    # facts were published and stay published
    assert formal_event_count(config) == 3

    second = run(config)
    assert second["failed"] == 1
    assert second["circuit_open"] is True
    assert policy.circuit_break_file.exists()
    # no duplicate publication on the retry path
    assert formal_event_count(config) == 3

    third = run(config)
    assert third["status"] == "blocked"
    assert "circuit_open" in third["reason"]
    assert third["evaluated"] == 0


# ---------------------------------------------------------------------------
# check-only / disabled / skip flag
# ---------------------------------------------------------------------------
def test_check_only_never_runs_downstream(pub_env):
    config = pub_env["config"]
    result = run(config, check_only=True)
    assert result["status"] == "completed"
    assert result["eligible"] == 1
    assert result["published"] == 0
    # zero side effects: no post-publication state, no coverage products
    assert not config.path("post_publication_root").exists()
    assert _coverage_dirs(config) == []
    assert not AutoPublishManifest(config.get("auto_publish.manifest_dir")).path.exists()
    assert formal_event_count(config) == 2


def test_disabled_never_runs_downstream(tmp_path):
    config = make_auto_config(tmp_path, enabled=False)
    repo = open_candidate_repo(config)
    seed_milestone(repo)
    repo.close()
    result = run(config)
    assert result["status"] == "disabled"
    assert result["published"] == 0
    assert not config.path("post_publication_root").exists()
    assert _coverage_dirs(config) == []


def test_skip_downstream_refused_by_production_default(pub_env):
    config = pub_env["config"]  # allow_skip_downstream defaults to False
    args = argparse.Namespace(
        check_only=False, election_id=None, candidate_db=None,
        output_root=None, skip_downstream=True,
    )
    result = run_auto_publish(config, args)
    assert result["status"] == "blocked"
    assert "skip_downstream_not_allowed" in result["reason"]
    assert result["published"] == 0
    assert not config.path("post_publication_root").exists()


def test_skip_downstream_allowed_when_configured(pub_env):
    config = pub_env["config"]
    config.raw["auto_publish"]["allow_skip_downstream"] = True
    args = argparse.Namespace(
        check_only=False, election_id=None, candidate_db=None,
        output_root=None, skip_downstream=True,
    )
    result = run_auto_publish(config, args)
    assert result["status"] == "completed"
    assert result["published"] == 1
    rec = [r for r in manifest_records(config) if r["status"] == "published"][-1]
    assert rec["downstream_status"] == "skipped_by_flag"
    assert not config.path("post_publication_root").exists()
    assert _coverage_dirs(config) == []
    assert formal_event_count(config) == 3


# ---------------------------------------------------------------------------
# manifest audit semantics
# ---------------------------------------------------------------------------
def test_manifest_audit_counting_semantics(tmp_path):
    m = AutoPublishManifest(tmp_path / "ap")
    m.append({"status": "failed", "candidate_id": "a", "run_date": "2026-08-12"})
    # downstream failure is a failure for the breaker streak
    m.append({"status": "published", "candidate_id": "b", "run_date": "2026-08-12",
              "downstream_status": "failed"})
    assert m.consecutive_failed_count() == 2
    # a full success resets the streak
    m.append({"status": "published", "candidate_id": "c", "run_date": "2026-08-12",
              "downstream_status": "ok", "snapshot_id": "s1",
              "coverage_version": "fact_coverage_20260727_v001",
              "facts_cutoff": "2026-07-27"})
    assert m.consecutive_failed_count() == 0
    # only full success counts toward the daily quota
    assert m.count_published_on("2026-08-12") == 1
    m.append({"status": "published", "candidate_id": "d", "run_date": "2026-08-12",
              "downstream_status": "ok"})
    assert m.consecutive_failed_count() == 0
    # published key protects against republish even after downstream failure
    assert m.has_published_key("b", "h", "1.0") is False  # no business hash in row
    m.append({"status": "published", "candidate_id": "b", "candidate_business_hash": "h",
              "policy_version": "1.0", "run_date": "2026-08-12",
              "downstream_status": "failed"})
    assert m.has_published_key("b", "h", "1.0") is True
    assert m.count_published_on("2026-08-12") == 2
