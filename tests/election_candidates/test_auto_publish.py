"""Tests for the conservative low-risk automated publication component.

Covers: gate acceptance/rejection matrix, history protections, idempotency,
quotas, single-failure stop, circuit breaker, kill switch, disabled mode,
check-only zero side effects and the terminal-status upsert protection.
"""

from __future__ import annotations

import argparse
import json
import sqlite3

import pytest

import app.election_candidates.auto_publish_candidates as auto_pub
from app.election_candidates.auto_publish_candidates import AutoPublishManifest, run_auto_publish
from app.election_candidates.auto_publish_gate import AutoPublishPolicy, evaluate_candidate
from .publication_helpers import (
    make_publication_config,
    open_candidate_repo,
    seed_candidate,
)

APPROVE = "approve_new_event"


def make_auto_config(tmp_path, enabled=True, **ap_overrides):
    config = make_publication_config(tmp_path)
    ap = config.raw.setdefault("auto_publish", {})
    ap.update(
        {
            "enabled": enabled,
            "policy_version": "1.0",
            "max_per_run": 3,
            "max_daily": 10,
            "consecutive_failure_limit": 2,
            "auto_approver": "auto_approver_v1",
            "manifest_dir": str(tmp_path / "auto_publish"),
            "kill_switch_file": str(tmp_path / "locks" / "auto_publish_disabled"),
            "circuit_break_file": str(tmp_path / "locks" / "auto_publish_circuit_open"),
        }
    )
    ap.update(ap_overrides)
    config.raw["paths"]["lock_root"] = str(tmp_path / "locks")
    return config


def low_risk_overrides(**over):
    base = dict(
        risk_level="low",
        relevance_label="direct_event",
        primary_actor="王定宇",
        secondary_actors_json='["林俊憲"]',
        themes_json='["民調"]',
        keywords_json='["王定宇","民調"]',
        candidate_event_type="poll_release",
        canonical_event_date="2026-08-10T10:00:00",
        event_date_basis="explicit_in_title",
        event_date_precision="day",
        event_date_confidence="high",
        candidate_title="王定宇公布台南市長民調",
        candidate_summary="據1篇報導，王定宇公布民調",
        formal_duplicate_status="no_match",
        formal_duplicate_score=0.1,
    )
    base.update(over)
    return base


def seed_eligible(repo, cid="cand_tnn_aaa001", status="review_required", **over):
    return seed_candidate(repo, cid=cid, status=status, **low_risk_overrides(**over))


def run(config, check_only=False):
    args = argparse.Namespace(
        check_only=check_only,
        election_id=None,
        candidate_db=None,
        output_root=None,
    )
    return run_auto_publish(config, args)


def formal_event_count(config):
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    try:
        return conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0]
    finally:
        conn.close()


def manifest_records(config):
    path = AutoPublishManifest(config.get("auto_publish.manifest_dir")).path
    if not path.exists():
        return []
    return AutoPublishManifest(config.get("auto_publish.manifest_dir")).read_records()


def test_happy_path_publishes(tmp_path):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo)
    repo.close()

    result = run(config)
    assert result["status"] == "completed"
    assert result["published"] == 1
    assert result["failed"] == 0
    assert result["rejected"] == 0

    repo = open_candidate_repo(config)
    cand = repo.get_candidate("cand_tnn_aaa001")
    assert cand["review_status"] == "published"
    decisions = repo.list_review_decisions("cand_tnn_aaa001")
    assert len(decisions) == 1
    assert decisions[0]["decision"] == APPROVE
    assert decisions[0]["reviewer"] == "auto_approver_v1"
    assert decisions[0]["candidate_business_hash"]
    repo.close()

    # formal db gained exactly one event
    assert formal_event_count(config) == 3

    recs = manifest_records(config)
    assert "published" in [r["status"] for r in recs]
    pub = [r for r in recs if r["status"] == "published"][0]
    assert pub["decision_origin"] == "automated_policy"
    assert pub["policy_version"] == "1.0"
    assert pub["candidate_business_hash"]
    assert pub["review_decision_id"]
    assert pub["batch_id"]
    assert pub["formal_hash_before"]
    assert pub["formal_hash_after"]
    assert pub["formal_hash_before"] != pub["formal_hash_after"]
    # gate reasons are recorded on the eligible record
    elig = [r for r in recs if r["status"] == "eligible"][0]
    assert elig["gate_results"]
    assert all(g["passed"] for g in elig["gate_results"])


@pytest.mark.parametrize("risk", ["medium", "high"])
def test_medium_high_risk_rejected(tmp_path, risk):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo, risk_level=risk)
    repo.close()

    result = run(config)
    assert result["status"] == "completed"
    assert result["published"] == 0
    assert result["rejected"] == 1
    rejected = [c for c in result["candidates"] if c["decision"] == "rejected"][0]
    assert any("risk_level_low" in r for r in rejected["reasons"])


def test_direct_statement_rejected(tmp_path):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo, relevance_label="direct_statement")
    repo.close()

    result = run(config)
    assert result["published"] == 0
    assert result["rejected"] == 1


@pytest.mark.parametrize("basis", ["unknown"])
def test_non_explicit_date_rejected(tmp_path, basis):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo, event_date_basis=basis)
    repo.close()

    result = run(config)
    assert result["published"] == 0
    assert result["rejected"] == 1
    rejected = [c for c in result["candidates"] if c["decision"] == "rejected"][0]
    assert any("date_explicit" in r for r in rejected["reasons"])


def test_inferred_date_accepted_when_policy_allows(tmp_path):
    """2026-09-03 决策：direct_event + 低风险 + 有 observed_fact 的候选，其日期
    为 inferred_from_publication 时允许自动发布（router 已挡 date_unknown）。"""
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo, event_date_basis="inferred_from_publication",
                  event_date_confidence="low", date_flagged_inferred=1)
    repo.close()

    result = run(config)
    assert result["status"] == "completed"
    assert result["published"] == 1
    assert result["rejected"] == 0


def test_unknown_event_type_rejected(tmp_path):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo, candidate_event_type="unknown")
    repo.close()

    result = run(config)
    assert result["published"] == 0
    assert result["rejected"] == 1


@pytest.mark.parametrize("dup_status", ["possible_match", "likely_duplicate"])
def test_duplicate_status_rejected(tmp_path, dup_status):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo, formal_duplicate_status=dup_status, formal_duplicate_score=0.8)
    repo.close()

    result = run(config)
    assert result["published"] == 0
    assert result["rejected"] == 1


def test_duplicate_recheck_rejects_even_when_status_no_match(tmp_path):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo)
    repo.close()
    # inject a highly similar formal event so the fresh re-check flags it
    conn = sqlite3.connect(config.path("formal_db"))
    conn.execute(
        "INSERT INTO election_events VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (
            "evt_dup_wang_20260810", "TW-2026-TNN-MAYOR", "2026-08-10T10:00:00+08:00",
            "poll_release", "王定宇公布台南市長民調", "王定宇公布民調",
            "verified", 90, '["王定宇"]', '["民調"]', "[]",
            '{"verified_facts":["民調"]}', "2026-08-01T00:00:00", "2026-08-01T00:00:00",
        ),
    )
    conn.execute("INSERT INTO event_sources VALUES ('evt_dup_wang_20260810','src_fix_cna',1)")
    conn.commit()
    conn.close()

    result = run(config)
    assert result["published"] == 0
    assert result["rejected"] == 1
    rejected = [c for c in result["candidates"] if c["decision"] == "rejected"][0]
    assert any("duplicate_recheck_clean" in r for r in rejected["reasons"])


@pytest.mark.parametrize("src_status", ["new_candidate_source", "unresolved"])
def test_new_or_unresolved_source_rejected(tmp_path, src_status):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo)
    repo.conn.execute(
        "UPDATE candidate_sources SET formal_match_status=?, formal_source_id='' "
        "WHERE candidate_source_id='csrc_cna'",
        (src_status,),
    )
    repo.conn.commit()
    repo.close()

    result = run(config)
    assert result["published"] == 0
    assert result["rejected"] == 1


def test_unsafe_fact_profile_rejected(tmp_path):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo)
    repo.conn.execute(
        "DELETE FROM candidate_assertions WHERE candidate_id=?",
        ("cand_tnn_aaa001",),
    )
    repo.conn.execute(
        "UPDATE candidate_events SET assertion_profile_json=? WHERE candidate_id=?",
        (
            json.dumps({"counts": {"observed_fact": 0}, "has_observed_fact": False}),
            "cand_tnn_aaa001",
        ),
    )
    repo.conn.commit()
    repo.close()

    result = run(config)
    assert result["published"] == 0
    assert result["rejected"] == 1
    rejected = [c for c in result["candidates"] if c["decision"] == "rejected"][0]
    assert any("has_observed_fact" in r for r in rejected["reasons"])


def test_published_candidate_rejected_and_never_downgraded(tmp_path):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo, status="published")
    # a bulk refresh style upsert must NOT downgrade a published candidate
    cand = repo.get_candidate("cand_tnn_aaa001")
    cand["review_status"] = "new"
    repo.upsert_candidate(cand)
    assert repo.get_candidate("cand_tnn_aaa001")["review_status"] == "published"
    # explicit state machine transition still works
    cand["review_status"] = "under_review"
    repo.upsert_candidate(cand, preserve_terminal_status=False)
    assert repo.get_candidate("cand_tnn_aaa001")["review_status"] == "under_review"
    cand["review_status"] = "published"
    repo.upsert_candidate(cand, preserve_terminal_status=False)
    repo.close()

    # runner never evaluates published candidates (they are not in the input set)
    result = run(config)
    assert result["published"] == 0
    assert result["evaluated"] == 0

    # gate unit-level: a published candidate is rejected by no_terminal_status
    repo = open_candidate_repo(config)
    policy = AutoPublishPolicy.from_config(config)
    ev = evaluate_candidate(repo, config, "cand_tnn_aaa001", policy)
    assert ev["decision"] == "rejected"
    assert any("no_terminal_status" in r for r in ev["reasons"])
    repo.close()


def test_historical_approve_decision_rejected(tmp_path):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo)
    repo.insert_review_decision(
        {
            "review_decision_id": "rev_000001_historical",
            "candidate_id": "cand_tnn_aaa001",
            "decision": APPROVE,
            "reviewer": "human_operator",
            "reviewed_at": "2026-08-01T00:00:00",
            "review_reason": "historical",
            "edited_event_payload_json": "{}",
            "target_formal_event_id": "",
            "source_resolution_json": "[]",
            "decision_version": "0.1.0",
            "candidate_business_hash": "x",
            "created_at": "2026-08-01T00:00:00",
        }
    )
    repo.close()

    result = run(config)
    assert result["published"] == 0
    assert result["rejected"] == 1
    rejected = [c for c in result["candidates"] if c["decision"] == "rejected"][0]
    assert any("no_approve_decision" in r for r in rejected["reasons"])


def test_idempotent_skip_after_publish(tmp_path):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo)
    repo.close()

    first = run(config)
    assert first["published"] == 1
    assert formal_event_count(config) == 3

    # simulate the (now forbidden) re-entry: bulk refresh sets status back
    repo = open_candidate_repo(config)
    cand = repo.get_candidate("cand_tnn_aaa001")
    repo.upsert_candidate(cand, preserve_terminal_status=False)  # explicit override
    cand = repo.get_candidate("cand_tnn_aaa001")
    cand["review_status"] = "review_required"
    repo.upsert_candidate(cand, preserve_terminal_status=False)
    repo.close()

    second = run(config)
    assert second["status"] == "completed"
    assert second["published"] == 0
    assert second["skipped"] == 1
    assert formal_event_count(config) == 3  # no duplicate publication
    recs = manifest_records(config)
    assert any(r["status"] == "skipped" and r["reason"] == "idempotency_key_published" for r in recs)


def test_max_per_run(tmp_path):
    config = make_auto_config(tmp_path, max_per_run=2)
    repo = open_candidate_repo(config)
    # candidates differ in actor/type/keywords so the fresh duplicate re-check
    # of later candidates is not tripped by just-published events
    seed_eligible(repo, cid="cand_tnn_aaa001", primary_actor="王定宇",
                  secondary_actors_json="[]",
                  canonical_event_date="2026-08-10T10:00:00")
    seed_eligible(repo, cid="cand_tnn_aaa002", primary_actor="林俊憲",
                  secondary_actors_json="[]",
                  candidate_event_type="policy_proposal",
                  themes_json='["政見"]', keywords_json='["林俊憲","政見"]',
                  candidate_title="林俊憲提出台南市政見",
                  canonical_event_date="2026-08-01T10:00:00")
    seed_eligible(repo, cid="cand_tnn_aaa003", primary_actor="陳亭妃",
                  secondary_actors_json="[]",
                  candidate_event_type="governance_event",
                  themes_json='["防汛"]', keywords_json='["陳亭妃","防汛"]',
                  candidate_title="陳亭妃視察三爺溪防汛工程",
                  canonical_event_date="2026-07-20T10:00:00")
    repo.close()

    result = run(config)
    assert result["status"] == "completed"
    assert result["published"] == 2
    assert result["stop_reason"] == "max_per_run:2"

    repo = open_candidate_repo(config)
    published = [cid for cid in ("cand_tnn_aaa001", "cand_tnn_aaa002", "cand_tnn_aaa003")
                 if repo.get_candidate(cid)["review_status"] == "published"]
    remaining = [cid for cid in ("cand_tnn_aaa001", "cand_tnn_aaa002", "cand_tnn_aaa003")
                 if repo.get_candidate(cid)["review_status"] == "review_required"]
    assert len(published) == 2
    assert len(remaining) == 1
    # the newest candidates were published first (sort by date desc)
    assert "cand_tnn_aaa003" in remaining
    repo.close()


def test_max_daily_blocks(tmp_path):
    config = make_auto_config(tmp_path, max_daily=10)
    manifest = AutoPublishManifest(config.get("auto_publish.manifest_dir"))
    for i in range(10):
        manifest.append(
            {
                "run_id": f"pre_{i}",
                "run_date": manifest_records_dummy_date(),
                "decision_origin": "automated_policy",
                "policy_version": "1.0",
                "timestamp": "2026-08-12T00:00:00",
                "status": "published",
                "candidate_id": f"old_{i}",
                "candidate_business_hash": f"h{i}",
            }
        )
    repo = open_candidate_repo(config)
    seed_eligible(repo)
    repo.close()

    result = run(config)
    assert result["status"] == "blocked"
    assert "daily_limit" in result["reason"]
    assert result["published"] == 0


def manifest_records_dummy_date():
    from datetime import datetime
    from app.time_utils import TAIPEI
    return datetime.now(TAIPEI).strftime("%Y-%m-%d")


def test_single_failure_stops_round(tmp_path, monkeypatch):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo, cid="cand_tnn_aaa001")
    seed_eligible(repo, cid="cand_tnn_aaa002", primary_actor="林俊憲",
                  secondary_actors_json="[]", canonical_event_date="2026-08-09T10:00:00")
    repo.close()

    def boom(*args, **kwargs):
        raise RuntimeError("injected publish failure")

    monkeypatch.setattr(auto_pub, "publish_one", boom)
    result = run(config)
    assert result["status"] == "completed"
    assert result["failed"] == 1
    assert result["published"] == 0
    # round stopped: second candidate untouched
    repo = open_candidate_repo(config)
    assert repo.get_candidate("cand_tnn_aaa001")["review_status"] == "review_required"
    assert repo.get_candidate("cand_tnn_aaa002")["review_status"] == "review_required"
    repo.close()
    assert formal_event_count(config) == 2
    recs = manifest_records(config)
    assert any(r["status"] == "failed" for r in recs)
    assert not any(r["status"] == "published" for r in recs)


def test_consecutive_failures_open_circuit(tmp_path, monkeypatch):
    config = make_auto_config(tmp_path, consecutive_failure_limit=2)
    repo = open_candidate_repo(config)
    seed_eligible(repo)
    repo.close()

    def boom(*args, **kwargs):
        raise RuntimeError("injected publish failure")

    monkeypatch.setattr(auto_pub, "publish_one", boom)

    first = run(config)
    assert first["status"] == "completed"
    assert first["failed"] == 1
    assert first["circuit_open"] is False
    policy = AutoPublishPolicy.from_config(config)
    assert not policy.circuit_break_file.exists()

    second = run(config)
    assert second["failed"] == 1
    assert second["circuit_open"] is True
    assert policy.circuit_break_file.exists()

    third = run(config)
    assert third["status"] == "blocked"
    assert "circuit_open" in third["reason"]
    assert third["evaluated"] == 0


def test_kill_switch_blocks(tmp_path):
    config = make_auto_config(tmp_path)
    policy = AutoPublishPolicy.from_config(config)
    policy.kill_switch_file.parent.mkdir(parents=True, exist_ok=True)
    policy.kill_switch_file.write_text("manual", encoding="utf-8")
    repo = open_candidate_repo(config)
    seed_eligible(repo)
    repo.close()

    result = run(config)
    assert result["status"] == "blocked"
    assert "kill_switch" in result["reason"]
    assert result["evaluated"] == 0
    assert manifest_records(config) == []


def test_disabled_zero_side_effect(tmp_path):
    config = make_auto_config(tmp_path, enabled=False)
    repo = open_candidate_repo(config)
    seed_eligible(repo)
    repo.close()

    result = run(config)
    assert result["status"] == "disabled"
    assert result["published"] == 0
    assert not AutoPublishManifest(config.get("auto_publish.manifest_dir")).path.exists()

    repo = open_candidate_repo(config)
    assert repo.get_candidate("cand_tnn_aaa001")["review_status"] == "review_required"
    repo.close()
    assert formal_event_count(config) == 2


def test_check_only_zero_side_effect(tmp_path):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo)
    repo.close()

    result = run(config, check_only=True)
    assert result["status"] == "completed"
    assert result["eligible"] == 1
    assert result["published"] == 0
    assert result["check_only"] is True

    # nothing written: candidate unchanged, no manifest, formal db untouched
    assert not AutoPublishManifest(config.get("auto_publish.manifest_dir")).path.exists()
    repo = open_candidate_repo(config)
    assert repo.get_candidate("cand_tnn_aaa001")["review_status"] == "review_required"
    assert repo.list_review_decisions("cand_tnn_aaa001") == []
    repo.close()
    assert formal_event_count(config) == 2


def test_terminal_upsert_protection_repository_level(tmp_path):
    config = make_auto_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_eligible(repo, status="published")
    for target in ("new", "review_required", "under_review", "review_approved"):
        cand = repo.get_candidate("cand_tnn_aaa001")
        cand["review_status"] = target
        repo.upsert_candidate(cand)
        assert repo.get_candidate("cand_tnn_aaa001")["review_status"] == "published"
    # explicit override path
    cand = repo.get_candidate("cand_tnn_aaa001")
    cand["review_status"] = "under_review"
    repo.upsert_candidate(cand, preserve_terminal_status=False)
    assert repo.get_candidate("cand_tnn_aaa001")["review_status"] == "under_review"
    repo.close()


def test_policy_defaults_from_config(tmp_path):
    policy = AutoPublishPolicy.from_config(make_auto_config(tmp_path))
    assert policy.enabled is True
    assert policy.max_per_run == 3
    assert policy.max_daily == 10
    assert policy.consecutive_failure_limit == 2
    assert policy.auto_approver == "auto_approver_v1"
    assert policy.allowed_risk_levels == ("low",)
    assert policy.allowed_relevance_labels == ("direct_event",)
    assert policy.forbidden_event_date_basis == ("unknown",)
    assert policy.allowed_source_match_statuses == ("exact", "normalized_match")
    assert policy.required_formal_duplicate_status == "no_match"
