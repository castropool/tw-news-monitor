"""Phase R2.1 disposition layer regression (HARD_BLOCK / REVIEW_REQUIRED / PASS)."""

from __future__ import annotations

import pytest

from app.assessment.r2.disposition import (
    HARD_BLOCK,
    PASS,
    REVIEW_REQUIRED,
    classify_disposition,
)


def _validation(**overrides):
    base = {
        "no_external_facts": True,
        "all_source_ids_exist": True,
        "all_poll_ids_exist": True,
        "poll_source_relationships_valid": True,
        "person_names_grounded": True,
        "organization_names_grounded": True,
        "no_unsupported_poll_claims": True,
        "claim_type_rules_valid": True,
        "numeric_claims_grounded": True,
        "date_claims_grounded": True,
        "required_disclosure_ids_valid": True,
        "claim_semantic_results": [],
        "errors": [],
    }
    base.update(overrides)
    return base


def _report(sections=8, claims=None):
    claims = claims or [
        {"claim_id": "C1", "claim_text": "陈亭妃表示将深入37个行政区。", "claim_type": "factual_synthesis"}
    ]
    return {
        "claims": claims,
        "sections": [
            {"section_id": f"S{i:02d}", "heading": f"第{i}节", "claim_ids": ["C1"]}
            for i in range(1, sections + 1)
        ],
    }


def _semantic(cid, *failures):
    return {"claim_id": cid, "failures": list(failures)}


# ---------- HARD_BLOCK ----------


@pytest.mark.parametrize(
    ("validation", "report", "kwargs", "expected_reason"),
    [
        (_validation(no_external_facts=False), None, {}, "fabricated_fact"),
        (_validation(), None, {"future_leakage_count": 1}, "future_event_leakage"),
        (
            _validation(claim_semantic_results=[_semantic("C1", "claim_strength_exceeds_evidence")]),
            None,
            {},
            "serious_unsupported_factual_assertion",
        ),
        (
            _validation(claim_semantic_results=[_semantic("C1", "statement_as_fact")]),
            _report(claims=[{"claim_id": "C1", "claim_text": "蓝白合作已经正式成形。"}]),
            {},
            "serious_statement_as_fact",
        ),
        (
            _validation(claim_semantic_results=[_semantic("C1", "allegation_as_fact")]),
            _report(claims=[{"claim_id": "C1", "claim_text": "郭信良已受贿并确定被起诉。"}]),
            {},
            "serious_allegation_as_fact",
        ),
        (_validation(all_source_ids_exist=False), None, {}, "deterministic_mapping_error"),
        (
            _validation(claim_semantic_results=[_semantic("C1", "invalid_event_reference")]),
            _report(claims=[{"claim_id": "C1", "claim_text": "x", "supporting_event_ids": ["evt_x"]}]),
            {"outside_events": {"evt_x": {"real": False, "future": False}}},
            "outside_pack_event_invalid_or_future",
        ),
        (
            _validation(claim_semantic_results=[_semantic("C1", "invalid_event_reference")]),
            _report(claims=[{"claim_id": "C1", "claim_text": "x", "supporting_event_ids": ["evt_future"]}]),
            {"outside_events": {"evt_future": {"real": True, "future": True}}},
            "outside_pack_event_invalid_or_future",
        ),
        (_validation(), _report(sections=7), {}, "schema_severe_damage"),
        (_validation(), None, {"integrity_ok": False}, "report_integrity_failure"),
        (_validation(), None, {"period_gate_ok": False}, "period_gate_not_satisfied"),
    ],
)
def test_hard_block_cases(validation, report, kwargs, expected_reason):
    result = classify_disposition(validation, report, **kwargs)
    assert result["production_disposition"] == HARD_BLOCK
    assert any(expected_reason in r for r in result["hard_block_reasons"])


def test_statement_as_fact_unverifiable_is_hard_block():
    validation = _validation(claim_semantic_results=[_semantic("C1", "statement_as_fact")])
    result = classify_disposition(validation, report=None)
    assert result["production_disposition"] == HARD_BLOCK
    assert any("serious_statement_as_fact" in r for r in result["hard_block_reasons"])


# ---------- REVIEW_REQUIRED ----------


@pytest.mark.parametrize(
    ("validation", "report", "kwargs", "expected_reason"),
    [
        (
            _validation(claim_semantic_results=[_semantic("C1", "claim_not_atomic")]),
            _report(),
            {},
            "non_atomic_claim",
        ),
        (
            _validation(claim_semantic_results=[_semantic("C1", "evidence_does_not_support_claim")]),
            _report(),
            {},
            "minor_evidence_support",
        ),
        (
            _validation(claim_semantic_results=[_semantic("C1", "statement_as_fact")]),
            _report(claims=[{"claim_id": "C1", "claim_text": "谢龙介公开表示43.6%是起跳点，不能视作民调证据。"}]),
            {},
            "non_serious_statement_as_fact",
        ),
        (
            _validation(claim_semantic_results=[_semantic("C1", "invalid_event_reference")]),
            _report(claims=[{"claim_id": "C1", "claim_text": "x", "supporting_event_ids": ["evt_old"]}]),
            {"outside_events": {"evt_old": {"real": True, "future": False}}},
            "outside_pack_event_real_historical",
        ),
        (_validation(no_unsupported_poll_claims=False), _report(), {}, "poll_boundary_non_serious"),
        (_validation(person_names_grounded=False), _report(), {}, "parser_noise_person"),
        (_validation(claim_type_rules_valid=False), _report(), {}, "claim_type_auxiliary"),
        (_validation(numeric_claims_grounded=False), _report(), {}, "numeric_format"),
        (_validation(required_disclosure_ids_valid=False), _report(), {}, "required_disclosures_auxiliary"),
    ],
)
def test_review_required_cases(validation, report, kwargs, expected_reason):
    result = classify_disposition(validation, report, **kwargs)
    assert result["production_disposition"] == REVIEW_REQUIRED
    assert any(expected_reason in r for r in result["review_required_reasons"])


def test_mixed_allowed_and_outside_events_only_outside_classified():
    validation = _validation(
        claim_semantic_results=[_semantic("C1", "invalid_event_reference")]
    )
    report = _report(
        claims=[
            {
                "claim_id": "C1",
                "claim_text": "x",
                "supporting_event_ids": ["evt_allowed", "evt_old"],
            }
        ]
    )
    result = classify_disposition(
        validation,
        report,
        outside_events={"evt_old": {"real": True, "future": False}},
        allowed_event_ids={"evt_allowed"},
    )
    assert result["production_disposition"] == REVIEW_REQUIRED
    assert any("outside_pack_event_real_historical" in r for r in result["review_required_reasons"])
    assert result["hard_block_reasons"] == []


# ---------- PASS ----------


def test_clean_report_is_pass():
    validation = _validation()
    result = classify_disposition(validation, _report())
    assert result["production_disposition"] == PASS
    assert result["hard_block_reasons"] == []
    assert result["review_required_reasons"] == []
