from app.assessment.report_repair import (
    build_repair_messages,
    is_repairable,
)


class TestReportRepair:
    def test_repairable_unknown_reference(self):
        v = {"errors": ["all_source_ids_exist: 存在未知 source_id"]}
        assert is_repairable(v) is True

    def test_repairable_missing_disclosure(self):
        v = {"errors": ["required_disclosures_complete: required disclosures 不完整"]}
        assert is_repairable(v) is True

    def test_unrepairable_provider_error(self):
        assert is_repairable({}, provider_error="认证失败") is False

    def test_unrepairable_generation_mode(self):
        v = {"errors": ["generation_mode_valid: 期望 draft_with_data_gap，实际 final"]}
        assert is_repairable(v) is False

    def test_repair_payload_contains_only_allowed(self):
        system, payload = build_repair_messages(
            original_report={"claims": []},
            validation={"errors": ["x"]},
            contract={"election_id": "e"},
            output_schema={"type": "object"},
        )
        assert set(payload) == {
            "original_structured_output",
            "structured_validation_errors",
            "llm_input_contract",
            "output_schema",
        }
        assert "API_KEY" not in system

