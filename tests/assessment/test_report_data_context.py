import json
from pathlib import Path

from app.assessment.claim_evidence_validator import (
    build_evidence_context,
    validate_structured_report,
)
from app.assessment.llm_input_contract import build_data_context
from app.assessment.report_artifact_validator import validate_report_artifact
from app.assessment.report_output_schema import (
    SCHEMA_VERSION,
    is_valid_report_schema,
    validate_report_schema,
)
from app.assessment.word_report_renderer import extract_word_text, render_word_report
from tests.assessment.llm.conftest import build_contract, make_report


def _contract_with_context():
    contract = build_contract()
    contract["data_status"]["active_snapshot_id"] = "tn_state_20260801_v1"
    contract["data_status"]["coverage_version"] = "fact_coverage_20260801_v4"
    contract["current_snapshot"]["snapshot_id"] = "tn_state_20260801_v1"
    contract["previous_snapshot"]["snapshot_id"] = "tn_state_20260727_v2"
    return contract


class TestReportDataContext:
    def test_report_contains_data_context(self):
        report = make_report(_contract_with_context())
        assert "data_context" in report
        assert report["schema_version"] == SCHEMA_VERSION

    def test_schema_version_1_1(self):
        report = make_report(_contract_with_context())
        assert is_valid_report_schema(report) is True
        assert report["schema_version"] == "1.1"

    def test_missing_data_context_fails_schema(self):
        report = make_report(_contract_with_context())
        del report["data_context"]
        assert is_valid_report_schema(report) is False
        assert any("data_context" in e for e in validate_report_schema(report))

    def test_active_snapshot_matches_contract(self):
        contract = _contract_with_context()
        report = make_report(contract)
        ctx = build_evidence_context(contract)
        assert report["data_context"]["active_snapshot_id"] == ctx.data_context["active_snapshot_id"]
        assert report["data_context"]["active_snapshot_id"] == "tn_state_20260801_v1"

    def test_coverage_version_matches_contract(self):
        contract = _contract_with_context()
        report = make_report(contract)
        assert report["data_context"]["coverage_version"] == "fact_coverage_20260801_v4"

    def test_facts_cutoff_matches_contract(self):
        report = make_report(_contract_with_context())
        assert report["data_context"]["facts_cutoff"] == "2026-07-27"

    def test_poll_cutoff_matches_contract(self):
        report = make_report(_contract_with_context())
        assert report["data_context"]["poll_cutoff"] == "2026-03-12"

    def test_uncovered_dates_match_contract(self):
        report = make_report(_contract_with_context())
        assert report["data_context"]["uncovered_date_range"] == [
            "2026-07-28",
            "2026-07-29",
            "2026-07-30",
            "2026-07-31",
        ]

    def test_wrong_context_rejected_by_validator(self):
        contract = _contract_with_context()
        report = make_report(contract)
        report["data_context"]["active_snapshot_id"] = "wrong_snapshot"
        ctx = build_evidence_context(contract)
        validation = validate_structured_report(
            report, ctx, expected_mode="draft_with_data_gap"
        )
        assert validation["data_context_matches_input"] is False
        assert validation["all_claims_validated"] is False

    def test_build_data_context_authoritative(self):
        contract = _contract_with_context()
        dc = build_data_context(contract)
        assert dc["period_start"] == "2026-07-16"
        assert dc["period_end"] == "2026-07-31"
        assert dc["previous_snapshot_id"] == "tn_state_20260727_v2"

    def test_word_shows_snapshot_and_coverage(self, tmp_path):
        report = make_report(_contract_with_context())
        info = render_word_report(report, output_dir=tmp_path, mode="development")
        text = extract_word_text(Path(info["docx_path"]))
        assert "当前快照：tn_state_20260801_v1" in text
        assert "覆盖版本：fact_coverage_20260801_v4" in text
        assert "未随报告携带" not in text

    def test_artifact_validator_data_context_checks(self, tmp_path):
        report = make_report(_contract_with_context())
        info = render_word_report(report, output_dir=tmp_path, mode="development")
        result = validate_report_artifact(
            report, Path(info["docx_path"]), expected_mode="draft_with_data_gap"
        )
        assert result["artifact_ready"] is True

    def test_artifact_validator_detects_missing_snapshot(self, tmp_path):
        report = make_report(_contract_with_context())
        info = render_word_report(report, output_dir=tmp_path, mode="development")
        report["data_context"]["active_snapshot_id"] = "other_snapshot_999"
        result = validate_report_artifact(
            report, Path(info["docx_path"]), expected_mode="draft_with_data_gap"
        )
        assert result["artifact_ready"] is False
        assert any("active_snapshot_present" in e for e in result["errors"])

    def test_artifact_validator_detects_missing_coverage(self, tmp_path):
        report = make_report(_contract_with_context())
        info = render_word_report(report, output_dir=tmp_path, mode="development")
        report["data_context"]["coverage_version"] = "fact_coverage_other"
        result = validate_report_artifact(
            report, Path(info["docx_path"]), expected_mode="draft_with_data_gap"
        )
        assert result["artifact_ready"] is False
        assert any("coverage_version_present" in e for e in result["errors"])

    def test_data_context_in_request_payload(self):
        from app.assessment.report_prompt_builder import build_request_payload

        payload = build_request_payload(_contract_with_context())
        assert payload["data_context"]["active_snapshot_id"] == "tn_state_20260801_v1"

    def test_model_wrong_context_overridden_by_injection(self):
        from app.assessment.generate_llm_report import _inject_data_context

        contract = _contract_with_context()
        report = make_report(contract)
        report["data_context"]["active_snapshot_id"] = "wrong_snapshot"
        report["data_context"]["coverage_version"] = "wrong_coverage"
        report["data_context"]["facts_cutoff"] = "2020-01-01"
        changed = _inject_data_context(report, contract)
        assert changed is True
        assert report["data_context"]["active_snapshot_id"] == "tn_state_20260801_v1"
        assert report["data_context"]["coverage_version"] == "fact_coverage_20260801_v4"
        assert report["data_context"]["facts_cutoff"] == "2026-07-27"
        assert report["data_context"]["poll_cutoff"] == "2026-03-12"
