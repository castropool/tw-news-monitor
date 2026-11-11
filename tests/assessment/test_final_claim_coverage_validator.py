from app.assessment.claim_plan_validator import validate_claim_plan
from app.assessment.claim_planner import build_planner_envelope
from app.assessment.final_claim_coverage_validator import validate_final_claim_coverage
from app.assessment.validated_claim_store import build_validated_claim_store
from tests.assessment.two_stage_fixtures import contract_fixture, valid_plan


def _store():
    contract = contract_fixture()
    plan = valid_plan()
    envelope = build_planner_envelope(contract, formal_state_hash="formal-hash", evidence_pack_hash="pack-hash")
    validation = validate_claim_plan(plan, contract=contract, planner_envelope=envelope, config={})
    return build_validated_claim_store(plan, validation, input_hashes={}, prompt_hash="p", schema_hash="s", provider_metadata={}, contract=contract)


def _draft(store):
    claims = store["accepted_claims"]
    return {
        "stage2_draft_version": "1.0",
        "report_writer_stage2_contract_version": "1.0",
        "validated_claim_plan_hash": store["claim_plan_business_hash"],
        "title": "台南选情仍待观察",
        "title_claim_ids": ["CP_S01_001"],
        "overall_judgment_claim_ids": ["CP_S01_001"],
        "sections": [
            {"section_id": f"S{i:02d}", "heading": f"S{i:02d}",
             "claim_ids": [c["claim_id"] for c in claims if c["target_section_id"] == f"S{i:02d}"],
             "section_purpose": f"purpose_{i}"}
            for i in range(1, 9)
        ],
        "claim_renderings": [{"claim_id": c["claim_id"], "rendered_text": c["claim_text"]} for c in claims],
    }


def test_exact_one_to_one_rendering_passes():
    store = _store()
    result = validate_final_claim_coverage(_draft(store), store)
    assert result["final_claim_coverage_ready"] is True
    assert result["final_claim_coverage_rate"] == 1.0
    assert result["unauthorized_new_claim_count"] == 0


def test_unauthorized_new_claim_fails_closed():
    store = _store()
    draft = _draft(store)
    draft["claim_renderings"].append({"claim_id": "CP_S01_999", "rendered_text": "新增判断。"})
    result = validate_final_claim_coverage(draft, store)
    assert result["final_claim_coverage_ready"] is False
    assert result["unauthorized_new_claim_count"] == 1


def test_strength_escalation_is_rejected():
    store = _store()
    draft = _draft(store)
    draft["claim_renderings"][0]["rendered_text"] = "双方确定已经全面完成整合。"
    result = validate_final_claim_coverage(draft, store)
    assert result["final_claim_coverage_ready"] is False
    assert any("strength" in item for item in result["errors"])
