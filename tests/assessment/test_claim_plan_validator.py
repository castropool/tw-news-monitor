from copy import deepcopy

from app.assessment.claim_plan_validator import validate_claim_plan
from app.assessment.claim_planner import build_planner_envelope
from tests.assessment.two_stage_fixtures import contract_fixture, valid_plan


def _validate(plan):
    contract = contract_fixture()
    envelope = build_planner_envelope(contract, formal_state_hash="formal-hash", evidence_pack_hash="pack-hash")
    return validate_claim_plan(plan, contract=contract, planner_envelope=envelope, config={})


def test_valid_claim_plan_is_accepted():
    result = _validate(valid_plan())
    assert result["claim_plan_status"] == "accepted"
    assert result["claim_validation_status"] == "passed"
    assert len(result["accepted_claims"]) == 8
    assert result["rejected_claims"] == []


def test_invalid_source_is_rejected_but_optional_section_can_continue():
    plan = valid_plan()
    claim = next(item for item in plan["claims"] if item["target_section_id"] == "S05")
    claim["source_ids"] = ["s3"]
    result = _validate(plan)
    assert result["claim_plan_status"] == "accepted_with_rejections"
    assert result["rejected_claims"][0]["validation_reasons"]
    assert result["section_coverage"]["S05"]["deterministic_disclosure_required"] is True


def test_missing_required_section_rejects_whole_plan():
    plan = valid_plan()
    plan["claims"] = [item for item in plan["claims"] if item["target_section_id"] != "S03"]
    result = _validate(plan)
    assert result["claim_plan_status"] == "rejected"
    assert result["report_generation_not_started"] is True


def test_validation_never_mutates_the_raw_plan():
    plan = valid_plan()
    before = deepcopy(plan)
    _validate(plan)
    assert plan == before
