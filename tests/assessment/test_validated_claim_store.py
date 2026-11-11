import json

from app.assessment.claim_plan_validator import validate_claim_plan
from app.assessment.claim_planner import build_planner_envelope
from app.assessment.validated_claim_store import build_validated_claim_store, write_validated_claim_store
from tests.assessment.two_stage_fixtures import contract_fixture, valid_plan


def _store(plan=None):
    contract = contract_fixture()
    plan = plan or valid_plan()
    envelope = build_planner_envelope(contract, formal_state_hash="formal-hash", evidence_pack_hash="pack-hash")
    validation = validate_claim_plan(plan, contract=contract, planner_envelope=envelope, config={})
    return build_validated_claim_store(
        plan, validation, input_hashes={"contract": "c"},
        prompt_hash="p", schema_hash="s", provider_metadata={"client_request_id": "client-1"},
    )


def test_store_is_stable_and_auditable(tmp_path):
    store = _store()
    path = write_validated_claim_store(tmp_path / "validated_claim_plan.json", store)
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert loaded["claim_plan_business_hash"] == store["claim_plan_business_hash"]
    assert loaded["semantic_repair_performed"] is False
    assert loaded["llm_repair_call_count"] == 0
    assert loaded["provider_metadata"]["client_request_id"] == "client-1"


def test_optional_empty_section_gets_only_deterministic_limitation():
    plan = valid_plan()
    plan["claims"] = [item for item in plan["claims"] if item["target_section_id"] != "S05"]
    store = _store(plan)
    generated = [item for item in store["accepted_claims"] if item["claim_id"] == "CP_S05_900"]
    assert len(generated) == 1
    assert generated[0]["claim_type"] == "limitation"
    assert generated[0]["material_for_report"] is False
    assert store["deterministic_disclosure_count"] == 1
