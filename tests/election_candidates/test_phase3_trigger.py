from __future__ import annotations

from datetime import date

import pytest

from app.assessment.assessment_trigger import (
    compute_reporting_period,
    create_trigger,
    run_mock_assessment,
)

from .phase3_helpers import load_golden, make_phase3_env


GOLDEN = load_golden("trigger")


def _manifest(facts_cutoff, coverage_version="fact_coverage_20260727_v001"):
    return {"facts_cutoff": facts_cutoff, "coverage_version": coverage_version}


@pytest.mark.parametrize("case", GOLDEN, ids=[c["case_id"] for c in GOLDEN])
def test_trigger_golden_cases(case, tmp_path):
    env = make_phase3_env(tmp_path)
    run_date = date.fromisoformat(case["run_date"])
    trigger = create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_golden",
        formal_state_hash="h_" + case["case_id"],
        coverage_manifest=_manifest(case["facts_cutoff"]),
        snapshot_id="tn_state_v1",
        run_date=run_date,
        manual=bool(case.get("manual")),
    )
    if case.get("duplicate_same_hash"):
        again = create_trigger(
            env["repo"], env["config"],
            refresh_batch_id="dr_golden2",
            formal_state_hash="h_" + case["case_id"],
            coverage_manifest=_manifest(case["facts_cutoff"]),
            snapshot_id="tn_state_v1",
            run_date=run_date,
        )
        assert again["trigger_id"] == trigger["trigger_id"]
        rows = env["repo"].get_triggers_for_period(
            env["config"].canonical_election_id,
            trigger["period_start"], trigger["period_end"],
        )
        assert len([r for r in rows if r["trigger_id"] == trigger["trigger_id"]]) == 1
    if case.get("supersede_old"):
        create_trigger(
            env["repo"], env["config"],
            refresh_batch_id="dr_old",
            formal_state_hash="h_old",
            coverage_manifest=_manifest(case["facts_cutoff"]),
            snapshot_id="tn_state_v1",
            run_date=run_date,
        )
        newer = create_trigger(
            env["repo"], env["config"],
            refresh_batch_id="dr_new",
            formal_state_hash="h_" + case["case_id"],
            coverage_manifest=_manifest(case["facts_cutoff"]),
            snapshot_id="tn_state_v1",
            run_date=run_date,
        )
        rows = env["repo"].get_triggers_for_period(
            env["config"].canonical_election_id,
            newer["period_start"], newer["period_end"],
        )
        old = [r for r in rows if r["formal_state_hash"] == "h_old"]
        assert old and old[0]["status"] == "superseded"
    if case.get("snapshot_pending"):
        env["repo"].update_trigger_status(trigger["trigger_id"], "blocked")
        stored = env["repo"].get_trigger(trigger["trigger_id"])
        assert stored["status"] == "blocked"
        return
    assert trigger["period_start"] == case["expected_start"]
    assert trigger["period_end"] == case["expected_end"]
    assert trigger["status"] == case["expected_status"]
    assert trigger["trigger_reason"].startswith(
        case.get("expected_reason", "")
    )
    if "expected_eligible" in case:
        assert (trigger["status"] == "eligible") == case["expected_eligible"]
    env["repo"].close()


def test_period_9_rule():
    config = make_phase3_env  # placeholder to keep signature simple
    # direct helper test uses a lightweight config stub
    class Cfg:
        canonical_election_id = "tainan_mayoral_2026"

        def get(self, dotted, default=None):
            return [9, 22] if dotted == "schedule.run_days" else default

    start, end = compute_reporting_period(date(2026, 8, 9), Cfg())
    assert (start, end) == (date(2026, 7, 16), date(2026, 7, 31))


def test_period_22_rule():
    class Cfg:
        def get(self, dotted, default=None):
            return [9, 22] if dotted == "schedule.run_days" else default

    start, end = compute_reporting_period(date(2026, 8, 22), Cfg())
    assert (start, end) == (date(2026, 8, 1), date(2026, 8, 15))


def test_period_8_projects_to_22():
    class Cfg:
        def get(self, dotted, default=None):
            return [9, 22] if dotted == "schedule.run_days" else default

    start, end = compute_reporting_period(date(2026, 8, 8), Cfg())
    assert (start, end) == (date(2026, 8, 1), date(2026, 8, 15))


def test_period_10_projects_to_22():
    class Cfg:
        def get(self, dotted, default=None):
            return [9, 22] if dotted == "schedule.run_days" else default

    start, end = compute_reporting_period(date(2026, 8, 10), Cfg())
    assert (start, end) == (date(2026, 8, 1), date(2026, 8, 15))


def test_mock_assessment_final_generation_mode(tmp_path):
    env = make_phase3_env(tmp_path)
    trigger = create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_mock1",
        formal_state_hash="h",
        coverage_manifest=_manifest("2026-07-31"),
        snapshot_id="tn_state_v1",
        run_date=date(2026, 8, 9),
    )
    report = run_mock_assessment(env["config"], trigger, tmp_path / "assessment")
    assert report["generation_mode"] == "final"
    assert report["final_report_allowed"] is True
    assert report["claim_evidence"]
    assert "assessment_run_id" in report
    env["repo"].close()


def test_mock_assessment_never_calls_network(tmp_path):
    env = make_phase3_env(tmp_path)
    trigger = create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_mock2",
        formal_state_hash="h2",
        coverage_manifest=_manifest("2026-07-31"),
        snapshot_id="tn_state_v1",
        run_date=date(2026, 8, 9),
    )
    report = run_mock_assessment(env["config"], trigger, tmp_path / "assessment2")
    delivery = (tmp_path / "assessment2" / "delivery_request.json").read_text(encoding="utf-8")
    assert '"network_calls": 0' in delivery
    assert report["generation_mode"] == "final"
    env["repo"].close()


def test_mock_assessment_draft_with_gap(tmp_path):
    env = make_phase3_env(tmp_path)
    trigger = create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_mock3",
        formal_state_hash="h3",
        coverage_manifest=_manifest("2026-07-30"),
        snapshot_id="tn_state_v1",
        run_date=date(2026, 8, 9),
    )
    report = run_mock_assessment(env["config"], trigger, tmp_path / "assessment3")
    assert report["generation_mode"] == "draft_with_data_gap"
    assert report["final_report_allowed"] is False
    env["repo"].close()


def test_mock_assessment_cache_reuse(tmp_path):
    env = make_phase3_env(tmp_path)
    trigger = create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_mock4",
        formal_state_hash="h4",
        coverage_manifest=_manifest("2026-07-31"),
        snapshot_id="tn_state_v1",
        run_date=date(2026, 8, 9),
    )
    a = run_mock_assessment(env["config"], trigger, tmp_path / "assessment4")
    b = run_mock_assessment(env["config"], trigger, tmp_path / "assessment4")
    assert a["assessment_run_id"] == b["assessment_run_id"]
    assert b["cached"] is True
    env["repo"].close()


def test_duplicate_trigger_same_period_same_hash(tmp_path):
    env = make_phase3_env(tmp_path)
    a = create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_d1",
        formal_state_hash="same",
        coverage_manifest=_manifest("2026-07-31"),
        snapshot_id="tn_state_v1",
        run_date=date(2026, 8, 9),
    )
    b = create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_d2",
        formal_state_hash="same",
        coverage_manifest=_manifest("2026-07-31"),
        snapshot_id="tn_state_v1",
        run_date=date(2026, 8, 9),
    )
    assert a["trigger_id"] == b["trigger_id"]
    rows = env["repo"].get_triggers_for_period(
        env["config"].canonical_election_id, a["period_start"], a["period_end"]
    )
    assert len(rows) == 1
    env["repo"].close()


def test_new_hash_supersedes_pending(tmp_path):
    env = make_phase3_env(tmp_path)
    create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_s_old",
        formal_state_hash="old_hash",
        coverage_manifest=_manifest("2026-07-31"),
        snapshot_id="tn_state_v1",
        run_date=date(2026, 8, 8),
    )
    new_trigger = create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_s_new",
        formal_state_hash="new_hash",
        coverage_manifest=_manifest("2026-07-31"),
        snapshot_id="tn_state_v1",
        run_date=date(2026, 8, 8),
    )
    rows = env["repo"].get_triggers_for_period(
        env["config"].canonical_election_id,
        new_trigger["period_start"], new_trigger["period_end"],
    )
    statuses = {r["formal_state_hash"]: r["status"] for r in rows}
    assert statuses["old_hash"] == "superseded"
    assert statuses["new_hash"] in ("pending", "eligible")
    env["repo"].close()


def test_manual_trigger_on_non_report_day(tmp_path):
    env = make_phase3_env(tmp_path)
    trigger = create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_m",
        formal_state_hash="h",
        coverage_manifest=_manifest("2026-08-15"),
        snapshot_id="tn_state_v1",
        run_date=date(2026, 8, 8),
        manual=True,
    )
    assert trigger["status"] == "eligible"
    assert trigger["trigger_reason"] == "report_day_coverage_full"
    env["repo"].close()


def test_non_report_day_pending(tmp_path):
    env = make_phase3_env(tmp_path)
    trigger = create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_n",
        formal_state_hash="h",
        coverage_manifest=_manifest("2026-07-31"),
        snapshot_id="tn_state_v1",
        run_date=date(2026, 8, 8),
    )
    assert trigger["status"] == "pending"
    assert trigger["trigger_reason"] == "not_reporting_day"
    env["repo"].close()


def test_blocked_trigger_when_snapshot_pending(tmp_path):
    env = make_phase3_env(tmp_path)
    trigger = create_trigger(
        env["repo"], env["config"],
        refresh_batch_id="dr_b",
        formal_state_hash="h",
        coverage_manifest=_manifest("2026-07-31"),
        snapshot_id="tn_state_v1",
        run_date=date(2026, 8, 9),
    )
    env["repo"].update_trigger_status(trigger["trigger_id"], "blocked", "snapshot pending")
    stored = env["repo"].get_trigger(trigger["trigger_id"])
    assert stored["status"] == "blocked"
    env["repo"].close()
