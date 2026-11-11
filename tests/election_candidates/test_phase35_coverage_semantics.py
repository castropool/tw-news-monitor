"""Phase 3.5 coverage semantic golden gate tests."""

from __future__ import annotations

import json
import re
from copy import deepcopy
from pathlib import Path

import pytest

from app.election_context.coverage_builder import compute_coverage_payload
from app.election_context.coverage_rules import (
    DEFAULT_RULES,
    load_acceptance_rules,
    rules_hash,
)
from app.election_context.coverage_validator import validate_coverage

from .phase3_helpers import load_golden
from .publication_helpers import make_publication_config


FIXTURE = (
    Path(__file__).resolve().parent.parent
    / "fixtures" / "election_candidates" / "coverage_semantic_golden_v1.json"
)
SEMANTIC = json.loads(FIXTURE.read_text(encoding="utf-8"))


def _run(case: dict):
    return compute_coverage_payload(
        events=case.get("events", []),
        polls=case.get("polls", []),
        event_source_ids=case.get("event_source_ids", []),
        poll_source_ids=case.get("poll_source_ids", []),
        source_count=case.get("source_count", 0),
        requested_start=case["period_start"],
        requested_end=case["period_end"],
        facts_cutoff=case.get("facts_cutoff"),
        blocking_gaps=case.get("blocking_gaps", []),
        known_gaps=case.get("known_gaps", []),
        dimensions=case.get("dimensions"),
        formal_state_hash="h_" + case["case_id"],
        configuration_hash="cfg",
        election_id="tainan_mayoral_2026",
        acceptance_rules=DEFAULT_RULES,
    )


@pytest.mark.parametrize("case", SEMANTIC, ids=[c["case_id"] for c in SEMANTIC])
def test_semantic_golden_cases(case):
    result = _run(case)
    cov = result["coverage"]
    assert cov["coverage_status"] == case["expected_status"]
    if "expected_facts_cutoff" in case:
        assert cov["facts_cutoff"] == case["expected_facts_cutoff"]
    if "expected_latest_event_date" in case:
        assert cov["latest_event_date"] == case["expected_latest_event_date"]
    if "expected_event_count" in case:
        assert cov["event_count"] == case["expected_event_count"]
    if "expected_poll_cutoff" in case:
        assert cov["poll_cutoff"] == case["expected_poll_cutoff"]
    if "expected_uncovered_count" in case:
        assert len(cov["uncovered_date_ranges"]) == case["expected_uncovered_count"]
    if "expected_blocking_count" in case:
        assert len(cov["blocking_gaps"]) == case["expected_blocking_count"]
    if "expected_uncovered_contains" in case:
        assert any(
            (g.get("start") or "") <= case["expected_uncovered_contains"] <= (g.get("end") or "")
            for g in cov["uncovered_date_ranges"]
        )


def test_calibration_holdout_split():
    cal = [c for c in SEMANTIC if c["subset"] == "calibration"]
    holdout = [c for c in SEMANTIC if c["subset"] == "holdout"]
    assert len(SEMANTIC) >= 30
    assert len(cal) >= 20
    assert len(holdout) >= 10


def test_no_holdout_specific_branching():
    # The execution path must not branch on case_id or subset.
    ids = [c["case_id"] for c in SEMANTIC]
    source = Path("app/election_context/coverage_builder.py").read_text(encoding="utf-8")
    for cid in ids:
        assert cid not in source
    assert "holdout" not in source


def test_no_hardcoded_legacy_dates_in_builder():
    source = Path("app/election_context/coverage_builder.py").read_text(encoding="utf-8")
    assert "2026-07-27" not in source
    assert "2026-07-31" not in source
    assert "fact_coverage_20260801_v4" not in source


def test_status_accuracy_one_hundred_percent():
    assert all(_run(c)["coverage"]["coverage_status"] == c["expected_status"] for c in SEMANTIC)


def test_facts_cutoff_accuracy_one_hundred_percent():
    for c in SEMANTIC:
        if "expected_facts_cutoff" in c:
            assert _run(c)["coverage"]["facts_cutoff"] == c["expected_facts_cutoff"]


def test_latest_event_date_accuracy_one_hundred_percent():
    for c in SEMANTIC:
        if "expected_latest_event_date" in c:
            assert _run(c)["coverage"]["latest_event_date"] == c["expected_latest_event_date"]


def test_false_full_count_zero():
    bad = [
        c["case_id"]
        for c in SEMANTIC
        if c["expected_status"] == "partial" and _run(c)["coverage"]["coverage_status"] == "full"
    ]
    assert bad == []


def test_false_partial_count_zero():
    bad = [
        c["case_id"]
        for c in SEMANTIC
        if c["expected_status"] == "full" and _run(c)["coverage"]["coverage_status"] == "partial"
    ]
    assert bad == []


def test_no_event_day_false_gap_count_zero():
    for c in SEMANTIC:
        if c["expected_status"] == "full" and c.get("expected_uncovered_count", 0) == 0:
            assert _run(c)["coverage"]["uncovered_date_ranges"] == []


def test_assessment_gate_consistency_one_hundred_percent():
    for c in SEMANTIC:
        cov = _run(c)["coverage"]
        fc = cov["facts_cutoff"]
        pe = c["period_end"]
        fully_covered = bool(fc and fc >= pe)
        final_allowed = cov["coverage_status"] == "full"
        # coverage_status=full must imply facts_cutoff >= period_end
        if final_allowed:
            assert fully_covered, f"{c['case_id']}: false full (cutoff before period end)"
        # facts_cutoff < period_end must imply final_report_allowed=false
        if not fully_covered:
            assert not final_allowed, f"{c['case_id']}: final allowed despite gap"


def test_partial_is_production_valid(tmp_path):
    config = make_publication_config(tmp_path)
    from app.election_context.coverage_builder import build_coverage

    result = build_coverage(
        config, requested_start="2026-01-01", requested_end="2026-07-31",
        facts_cutoff="2026-07-27",
    )
    assert result["coverage"]["coverage_status"] == "partial"
    validation = validate_coverage(config, result["coverage"], result["manifest"])
    assert validation["coverage_ready"] is True
    assert validation["final_report_allowed"] is False


def test_acceptance_rules_loaded_by_validator(tmp_path):
    config = make_publication_config(tmp_path)
    rules = load_acceptance_rules(config)
    assert rules["coverage"]["full_requires"]["facts_cutoff_reaches_period_end"] is True
    assert rules["coverage"]["no_event_day_is_gap"] is False
    assert rules["coverage"]["poll"]["absence_of_new_poll_is_blocking"] is False
    assert rules_hash(rules)


def test_acceptance_rules_change_changes_version(tmp_path):
    config = make_publication_config(tmp_path)
    rules = deepcopy(DEFAULT_RULES)
    rules["coverage"]["no_event_day_is_gap"] = True
    from app.election_context.coverage_builder import build_coverage

    result = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                            facts_cutoff="2026-07-31")
    assert result["coverage"]["no_event_day_is_gap"] is False
    assert rules_hash(DEFAULT_RULES) != rules_hash(rules)


def test_rule_source_single_file():
    src = Path("app/election_context/coverage_builder.py").read_text(encoding="utf-8")
    assert "load_acceptance_rules" in src
    v = Path("app/election_context/coverage_validator.py").read_text(encoding="utf-8")
    assert "load_acceptance_rules" in v


def test_legacy_status_reference_not_hardcoded():
    # The legacy version string may only appear in historical data/fixtures, never in builder logic.
    builder = Path("app/election_context/coverage_builder.py").read_text(encoding="utf-8")
    validator = Path("app/election_context/coverage_validator.py").read_text(encoding="utf-8")
    assert "fact_coverage_20260801_v4" not in builder + validator


def test_business_hash_stable_across_two_runs():
    a = [_run(c)["business_hash"] for c in SEMANTIC]
    b = [_run(c)["business_hash"] for c in SEMANTIC]
    assert a == b
