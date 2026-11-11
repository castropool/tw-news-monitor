from app.assessment.claim_plan_validator import validate_claim_plan
from app.assessment.claim_planner import build_planner_envelope
from app.assessment.llm.base_provider import ProviderResult
from app.assessment.two_stage_pipeline import run_two_stage_generation
from app.assessment.report_writer_stage2 import load_stage2_system_prompt
from app.assessment.validated_claim_store import build_validated_claim_store
from tests.assessment.test_final_claim_coverage_validator import _draft
from tests.assessment.two_stage_fixtures import contract_fixture, valid_plan


class SequenceProvider:
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def generate_structured_report(self, **kwargs):
        self.calls.append(kwargs)
        output = self.outputs.pop(0)
        return ProviderResult(
            provider="sequence", model="fixture", structured_output=output,
            client_request_id=kwargs["request_metadata"]["client_request_id"],
            request_audit={"fixture": True},
        )


def test_invalid_stage1_stops_before_stage2(tmp_path):
    bad = valid_plan()
    bad["claims"] = []
    provider = SequenceProvider([bad])
    result = run_two_stage_generation(
        contract=contract_fixture(), evidence_pack=None, provider=provider,
        output_dir=tmp_path, formal_state_hash="formal-hash",
        evidence_pack_hash="pack-hash", config={}, run_id="run-stop",
    )
    assert result["two_stage_status"] == "stage1_rejected"
    assert result["stage2_call_count"] == 0
    assert len(provider.calls) == 1
    assert not (tmp_path / "stage2_raw_output.json").exists()


def test_valid_two_stage_run_emits_final_schema_v2(tmp_path):
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
    provider = SequenceProvider([plan, _draft(store)])
    result = run_two_stage_generation(
        contract=contract, evidence_pack=None, provider=provider,
        output_dir=tmp_path, formal_state_hash="formal-hash",
        evidence_pack_hash="pack-hash", config={}, run_id="run-pass",
    )
    assert result["two_stage_status"] == "passed"
    assert result["stage1_call_count"] == 1
    assert result["stage2_call_count"] == 1
    assert result["llm_repair_call_count"] == 0
    assert result["final_report"]["schema_version"] == "2.0"
    assert result["final_report"]["conclusion_summary"]
    assert 1 <= len(result["final_report"]["core_assessments"]) <= 3
    assert (tmp_path / "structured_report_final.json").exists()
    assert provider.calls[1]["system_prompt"] == load_stage2_system_prompt()
    assert provider.calls[1]["request_metadata"]["effective_system_prompt_hash"] == result["stage2_effective_system_prompt_hash"]
    assert provider.calls[1]["request_metadata"]["output_schema_business_hash"] == result["stage2_output_schema_business_hash"]
