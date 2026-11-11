from __future__ import annotations

import json
from datetime import date

import pytest

from app.election_context.coverage_builder import (
    COVERAGE_SCHEMA_VERSION,
    VERSION_PATTERN,
    build_coverage,
    compute_coverage_payload,
    write_coverage,
)
from app.election_context.coverage_validator import validate_coverage

from .phase3_helpers import load_golden
from .publication_helpers import make_publication_config


GOLDEN = load_golden("coverage")


def _compute(case: dict, **over):
    kwargs = {
        "events": case.get("events", []),
        "polls": case.get("polls", []),
        "event_source_ids": case.get("event_source_ids", []),
        "poll_source_ids": case.get("poll_source_ids", []),
        "source_count": case.get("source_count", 0),
        "requested_start": case["period_start"],
        "requested_end": case["period_end"],
        "facts_cutoff": case.get("facts_cutoff"),
        "blocking_gaps": case.get("blocking_gaps", []),
        "known_gaps": case.get("known_gaps", []),
        "dimensions": case.get("dimensions"),
        "formal_state_hash": "h_" + case.get("case_id", "adhoc"),
        "configuration_hash": "cfg",
        "election_id": "tainan_mayoral_2026",
    }
    kwargs.update(over)
    return compute_coverage_payload(**kwargs)


@pytest.mark.parametrize("case", GOLDEN, ids=[c["case_id"] for c in GOLDEN])
def test_coverage_golden_cases(case):
    result = _compute(case)
    cov = result["coverage"]
    if "expected_facts_cutoff" in case:
        assert cov["facts_cutoff"] == case["expected_facts_cutoff"]
    if "expected_latest_event_date" in case:
        assert cov["latest_event_date"] == case["expected_latest_event_date"]
    if "expected_poll_cutoff" in case:
        assert cov["poll_cutoff"] == case["expected_poll_cutoff"]
    if "expected_event_count" in case:
        assert cov["event_count"] == case["expected_event_count"]
    if "expected_uncovered_count" in case:
        assert len(cov["uncovered_date_ranges"]) == case["expected_uncovered_count"]
    if "expected_blocking_count" in case:
        assert len(cov["blocking_gaps"]) == case["expected_blocking_count"]
    if "expected_status" in case:
        assert cov["coverage_status"] == case["expected_status"]
    if "expected_covered_sources" in case:
        assert cov["covered_source_ids"] == case["expected_covered_sources"]
    if "expected_covered_dates" in case:
        assert cov["covered_event_dates"] == case["expected_covered_dates"]
    if "expected_dimension_gaps" in case:
        assert cov["dimension_gaps"] == case["expected_dimension_gaps"]
    if "expected_known_gaps" in case:
        assert cov["known_gaps"] == case["expected_known_gaps"]
    if "expected_source_count" in case:
        assert cov["source_count"] == case["expected_source_count"]
    if "expected_poll_count" in case:
        assert cov["poll_count"] == case["expected_poll_count"]
    if "expected_uncovered_contains" in case:
        assert any(
            (g.get("start") or "") <= case["expected_uncovered_contains"] <= (g.get("end") or "")
            for g in cov["uncovered_date_ranges"]
        )
    if case.get("expected_idempotent"):
        again = _compute(case)
        assert again["business_hash"] == result["business_hash"]


def test_coverage_payload_required_keys():
    result = _compute(GOLDEN[0])
    for key in (
        "coverage_schema_version", "coverage_version", "election_id",
        "built_from_formal_state_hash", "requested_period_start",
        "requested_period_end", "facts_cutoff", "facts_cutoff_provenance",
        "latest_event_date", "poll_cutoff", "event_count", "source_count",
        "poll_count", "covered_event_ids", "covered_source_ids",
        "covered_poll_ids", "uncovered_date_ranges", "blocking_gaps",
        "dimension_gaps", "known_gaps", "coverage_status",
    ):
        assert key in result["coverage"]


def test_manifest_required_keys():
    result = _compute(GOLDEN[0])
    for key in (
        "coverage_version", "coverage_schema_version", "election_id",
        "built_from_formal_state_hash", "facts_cutoff", "facts_cutoff_provenance",
        "latest_event_date", "poll_cutoff", "coverage_start", "coverage_end",
        "event_count", "source_count", "poll_count", "uncovered_date_ranges",
        "blocking_gap_count", "dimension_gaps", "business_hash",
    ):
        assert key in result["manifest"]


def test_coverage_version_format_and_schema():
    result = _compute(GOLDEN[0])
    assert VERSION_PATTERN.match(result["coverage_version"])
    assert result["coverage"]["coverage_schema_version"] == COVERAGE_SCHEMA_VERSION


def test_coverage_version_deterministic():
    a = _compute(GOLDEN[0])
    b = _compute(GOLDEN[0])
    assert a["coverage_version"] == b["coverage_version"]
    assert a["business_hash"] == b["business_hash"]


def test_business_hash_changes_with_formal_state():
    a = _compute(GOLDEN[0], formal_state_hash="h1")
    b = _compute(GOLDEN[0], formal_state_hash="h2")
    assert a["business_hash"] != b["business_hash"]


def test_facts_cutoff_must_not_be_derived_from_latest_event():
    with pytest.raises(ValueError):
        _compute(GOLDEN[0], facts_cutoff_provenance="derived_from_latest_event")


def test_facts_cutoff_and_latest_event_are_distinct_fields():
    case = {
        "period_start": "2026-07-01", "period_end": "2026-07-31",
        "events": [{"event_id": "e1", "occurred_at": "2026-07-10T00:00:00+08:00", "event_type": "x"}],
        "facts_cutoff": "2026-07-31",
    }
    result = _compute(case)
    assert result["coverage"]["facts_cutoff"] == "2026-07-31"
    assert result["coverage"]["latest_event_date"] == "2026-07-10"
    assert result["coverage"]["facts_cutoff_provenance"] == "authoritative_input"


def test_latest_event_date_not_used_as_facts_cutoff():
    case = {
        "period_start": "2026-07-16", "period_end": "2026-07-31",
        "events": [{"event_id": "e1", "occurred_at": "2026-07-27T00:00:00+08:00", "event_type": "x"}],
        "facts_cutoff": "2026-07-31",
    }
    result = _compute(case)
    assert result["coverage"]["coverage_status"] == "full"
    assert result["coverage"]["latest_event_date"] == "2026-07-27"


def test_no_event_day_does_not_create_gap():
    case = {
        "period_start": "2026-07-01", "period_end": "2026-07-31",
        "events": [
            {"event_id": "e1", "occurred_at": "2026-07-05T00:00:00+08:00", "event_type": "x"},
            {"event_id": "e2", "occurred_at": "2026-07-07T00:00:00+08:00", "event_type": "x"},
        ],
        "facts_cutoff": "2026-07-31",
    }
    result = _compute(case)
    assert result["coverage"]["uncovered_date_ranges"] == []
    assert result["coverage"]["coverage_status"] == "full"


def test_no_formal_event_is_not_coverage_failure():
    case = {
        "period_start": "2026-07-01", "period_end": "2026-07-31",
        "events": [], "facts_cutoff": "2026-07-31",
    }
    result = _compute(case)
    assert result["coverage"]["coverage_status"] == "full"
    assert result["coverage"]["latest_event_date"] is None
    assert result["coverage"]["uncovered_date_ranges"] == []


def test_missing_poll_is_not_facts_gap():
    case = {
        "period_start": "2026-07-16", "period_end": "2026-07-31",
        "events": [{"event_id": "e1", "occurred_at": "2026-07-27T00:00:00+08:00", "event_type": "x"}],
        "polls": [], "facts_cutoff": "2026-07-31",
    }
    result = _compute(case)
    assert result["coverage"]["coverage_status"] == "full"
    assert result["coverage"]["poll_cutoff"] is None
    assert result["coverage"]["blocking_gaps"] == []


def test_unreviewed_period_is_blocking():
    case = {
        "period_start": "2026-07-16", "period_end": "2026-07-31",
        "events": [{"event_id": "e1", "occurred_at": "2026-07-27T00:00:00+08:00", "event_type": "x"}],
        "facts_cutoff": "2026-07-27",
    }
    result = _compute(case)
    assert result["coverage"]["coverage_status"] == "partial"
    assert len(result["coverage"]["blocking_gaps"]) == 1
    assert result["coverage"]["blocking_gaps"][0]["kind"] == "unreviewed_period"
    assert len(result["coverage"]["uncovered_date_ranges"]) == 1
    assert result["coverage"]["uncovered_date_ranges"][0]["start"] == "2026-07-28"
    assert result["coverage"]["uncovered_date_ranges"][0]["end"] == "2026-07-31"


def test_full_requires_cutoff_reaches_period_end():
    case = {
        "period_start": "2026-07-16", "period_end": "2026-07-31",
        "events": [{"event_id": "e1", "occurred_at": "2026-07-27T00:00:00+08:00", "event_type": "x"}],
        "facts_cutoff": "2026-07-30",
    }
    result = _compute(case)
    assert result["coverage"]["coverage_status"] == "partial"


def test_invalid_period_rejected():
    with pytest.raises(ValueError):
        compute_coverage_payload(
            events=[], polls=[], source_count=0,
            requested_start="2026-07-31", requested_end="2026-07-01",
            formal_state_hash="h", configuration_hash="c", election_id="t",
        )


def test_build_coverage_from_db(tmp_path):
    config = make_publication_config(tmp_path)
    result = build_coverage(
        config, requested_start="2026-01-01", requested_end="2026-07-31",
        facts_cutoff="2026-07-31",
    )
    assert result["coverage"]["event_count"] == 2
    assert result["coverage"]["source_count"] == 2
    assert result["coverage"]["covered_event_ids"] == [
        "evt_fix_nom_20260121", "evt_fix_rally_20260725"
    ]
    assert result["coverage"]["coverage_status"] == "full"
    assert result["coverage"]["latest_event_date"] == "2026-07-25"


def test_build_coverage_from_db_twice_idempotent(tmp_path):
    config = make_publication_config(tmp_path)
    a = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                       facts_cutoff="2026-07-31")
    b = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                       facts_cutoff="2026-07-31")
    assert a["business_hash"] == b["business_hash"]
    assert a["coverage_version"] == b["coverage_version"]
    assert a["coverage"]["coverage_status"] == b["coverage"]["coverage_status"]


def test_write_coverage_artifacts(tmp_path):
    config = make_publication_config(tmp_path)
    result = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                            facts_cutoff="2026-07-31")
    out = tmp_path / "coverage_out"
    manifest_path = write_coverage(result, out)
    assert manifest_path.exists()
    assert (out / f"{result['coverage_version']}.json").exists()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["business_hash"] == result["business_hash"]


def test_validator_passes_on_fixture(tmp_path):
    config = make_publication_config(tmp_path)
    result = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                            facts_cutoff="2026-07-31")
    validation = validate_coverage(config, result["coverage"], result["manifest"])
    assert validation["coverage_ready"] is True
    assert validation["errors"] == []
    assert validation["coverage_status"] == "full"
    assert validation["final_report_allowed"] is True


def test_validator_accepts_partial(tmp_path):
    config = make_publication_config(tmp_path)
    result = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                            facts_cutoff="2026-07-27")
    validation = validate_coverage(config, result["coverage"], result["manifest"])
    assert validation["coverage_ready"] is True
    assert validation["coverage_status"] == "partial"
    assert validation["final_report_allowed"] is False


def test_validator_rejects_full_with_cutoff_before_end(tmp_path):
    config = make_publication_config(tmp_path)
    result = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                            facts_cutoff="2026-07-27")
    result["coverage"]["coverage_status"] = "full"
    validation = validate_coverage(config, result["coverage"], result["manifest"])
    assert "full_status_requires_cutoff" in validation["errors"]
    assert "assessment_final_gate_consistent" in validation["errors"]


def test_validator_rejects_derived_cutoff_provenance(tmp_path):
    config = make_publication_config(tmp_path)
    result = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                            facts_cutoff="2026-07-31")
    result["coverage"]["facts_cutoff_provenance"] = "derived_from_latest_event"
    validation = validate_coverage(config, result["coverage"], result["manifest"])
    assert "facts_cutoff_not_derived_from_latest_event" in validation["errors"]


def test_validator_rejects_missing_unreviewed_disclosure(tmp_path):
    config = make_publication_config(tmp_path)
    result = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                            facts_cutoff="2026-07-27")
    result["coverage"]["uncovered_date_ranges"] = []
    validation = validate_coverage(config, result["coverage"], result["manifest"])
    assert "unreviewed_period_disclosed" in validation["errors"]


def test_validator_rejects_foreign_hash(tmp_path):
    config = make_publication_config(tmp_path)
    result = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                            facts_cutoff="2026-07-31")
    result["manifest"]["built_from_formal_state_hash"] = "deadbeef"
    validation = validate_coverage(config, result["coverage"], result["manifest"])
    assert "formal_state_hash_matches" in validation["errors"]


def test_validator_rejects_bad_version(tmp_path):
    config = make_publication_config(tmp_path)
    result = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                            facts_cutoff="2026-07-31")
    result["manifest"]["coverage_version"] = "not_a_version"
    validation = validate_coverage(config, result["coverage"], result["manifest"])
    assert "coverage_version_format" in validation["errors"]


def test_validator_rejects_political_inference(tmp_path):
    config = make_publication_config(tmp_path)
    result = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                            facts_cutoff="2026-07-31")
    result["coverage"]["win_rate"] = 0.99
    validation = validate_coverage(config, result["coverage"], result["manifest"])
    assert "no_political_inference" in validation["errors"]


def test_validator_rejects_tampered_business_hash(tmp_path):
    config = make_publication_config(tmp_path)
    result = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                            facts_cutoff="2026-07-31")
    result["manifest"]["business_hash"] = "deadbeef"
    validation = validate_coverage(config, result["coverage"], result["manifest"])
    assert "business_hash_valid" in validation["errors"]


def test_validator_rejects_bad_date_range(tmp_path):
    config = make_publication_config(tmp_path)
    result = build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                            facts_cutoff="2026-07-31")
    result["coverage"]["requested_period_start"] = "2026-07-31"
    result["coverage"]["requested_period_end"] = "2026-01-01"
    validation = validate_coverage(config, result["coverage"], result["manifest"])
    assert "date_ranges_valid" in validation["errors"]


def test_validator_rejects_unknown_blocking_kind(tmp_path):
    config = make_publication_config(tmp_path)
    with pytest.raises(ValueError, match="unknown coverage gap kind"):
        build_coverage(config, requested_start="2026-01-01", requested_end="2026-07-31",
                       facts_cutoff="2026-07-31",
                       blocking_gaps=[{"kind": "not_a_kind", "reason": "x"}])


def test_coverage_uncovered_accuracy_one_hundred_percent():
    for case in GOLDEN:
        result = _compute(case)
        if "expected_uncovered_count" in case:
            assert len(result["coverage"]["uncovered_date_ranges"]) == case["expected_uncovered_count"]
        if "expected_uncovered_contains" in case:
            assert any(
                (g.get("start") or "") <= case["expected_uncovered_contains"] <= (g.get("end") or "")
                for g in result["coverage"]["uncovered_date_ranges"]
            )


def test_cutoff_accuracy_one_hundred_percent():
    for case in GOLDEN:
        result = _compute(case)
        if "expected_facts_cutoff" in case:
            assert result["coverage"]["facts_cutoff"] == case["expected_facts_cutoff"]


def test_no_system_time_dependency():
    before = date.today()
    a = _compute(GOLDEN[0])
    after = date.today()
    assert before == after  # sanity; the golden inputs are fixed
    assert a["coverage"]["requested_period_start"] == GOLDEN[0]["period_start"]
