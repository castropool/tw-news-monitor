from app.assessment.claim_evidence_validator import build_evidence_context, validate_structured_report
from app.assessment.claim_plan_validator import validate_claim_plan
from app.assessment.claim_planner import build_planner_envelope
from app.assessment.report_output_schema import validate_report_schema_v2
from app.assessment.two_stage_report_assembler import assemble_final_report
from app.assessment.validated_claim_store import build_validated_claim_store
from tests.assessment.test_final_claim_coverage_validator import _draft
from tests.assessment.two_stage_fixtures import contract_fixture, valid_plan


def test_assembler_produces_schema_v2_and_preserves_authoritative_evidence():
    contract = contract_fixture()
    plan = valid_plan()
    envelope = build_planner_envelope(
        contract, formal_state_hash="formal-hash", evidence_pack_hash="pack-hash"
    )
    validation = validate_claim_plan(plan, contract=contract, planner_envelope=envelope, config={})
    store = build_validated_claim_store(
        plan, validation, input_hashes={}, prompt_hash="p", schema_hash="s",
        provider_metadata={}, contract=contract,
    )
    report = assemble_final_report(_draft(store), store=store, contract=contract)

    assert report["schema_version"] == "2.0"
    assert validate_report_schema_v2(report) == []
    assert report["conclusion_summary"]
    assert 1 <= len(report["core_assessments"]) <= 3
    assert report["required_disclosures"]
    # 结论摘要引用证据包内事件。
    refs = report["conclusion_summary"][0]["evidence_refs"]
    assert set(refs.get("event_ids") or []) <= {"e1", "e2", "bg1"}

    ctx = build_evidence_context(contract, evidence_pack=None, config={})
    result = validate_structured_report(
        report,
        ctx,
        expected_mode=contract["generation_eligibility"]["allowed_generation_mode"],
        assembled=True,
    )
    assert result["output_schema_valid"] is True
    assert result["report_structure_valid"] is True
    assert result["data_context_matches_input"] is True
    assert result["all_claims_validated"] is True, result["errors"]
