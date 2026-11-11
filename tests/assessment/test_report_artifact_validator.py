from pathlib import Path

from app.assessment.report_artifact_validator import (
    validate_report_artifact,
    word_text_signature,
)
from app.assessment.word_report_renderer import extract_word_text, render_word_report
from tests.assessment.llm.conftest import build_contract, make_report


def _render(report, tmp_path) -> Path:
    info = render_word_report(report, output_dir=tmp_path, mode="development")
    return Path(info["docx_path"])


class TestReportArtifactValidator:
    def test_valid_artifact_passes(self, tmp_path):
        report = make_report(build_contract())
        docx = _render(report, tmp_path)
        result = validate_report_artifact(
            report,
            docx,
            expected_mode="draft_with_data_gap",
        )
        assert result["artifact_ready"] is True
        assert result["errors"] == []
        assert result["rendered_claim_count"] == len(report["claims"])

    def test_missing_file_fails(self, tmp_path):
        report = make_report(build_contract())
        result = validate_report_artifact(report, tmp_path / "missing.docx")
        assert result["artifact_ready"] is False
        assert "docx_exists" in result["errors"][0]

    def test_rejected_report_fails(self, tmp_path):
        report = make_report(build_contract())
        docx = _render(report, tmp_path)
        report["report_status"] = "rejected"
        result = validate_report_artifact(report, docx)
        assert result["artifact_ready"] is False
        assert any("rejected" in e for e in result["errors"])

    def test_missing_draft_label_fails(self, tmp_path):
        report = make_report(build_contract(), fixture="valid_final")
        docx = _render(report, tmp_path)
        report["generation_mode"] = "draft_with_data_gap"
        result = validate_report_artifact(
            report,
            docx,
            expected_mode="draft_with_data_gap",
        )
        assert result["artifact_ready"] is False
        assert any("draft_label_present_when_required" in e for e in result["errors"])

    def test_draft_label_on_final_fails(self, tmp_path):
        report = make_report(build_contract())
        docx = _render(report, tmp_path)
        report["generation_mode"] = "final"
        result = validate_report_artifact(report, docx)
        assert result["artifact_ready"] is False
        assert any("正式报告不应包含草稿标识" in e for e in result["errors"])

    def test_title_mismatch_fails(self, tmp_path):
        report = make_report(build_contract())
        docx = _render(report, tmp_path)
        report["title"] = "完全不同的标题"
        result = validate_report_artifact(report, docx)
        assert result["artifact_ready"] is False
        assert any("title_matches" in e for e in result["errors"])

    def test_missing_section_fails(self, tmp_path):
        report = make_report(build_contract())
        docx = _render(report, tmp_path)
        report["sections"] = report["sections"][:-1]
        result = validate_report_artifact(report, docx)
        assert result["artifact_ready"] is False
        assert any("all_required_sections_present" in e for e in result["errors"])

    def test_claim_order_mismatch_fails(self, tmp_path):
        report = make_report(build_contract())
        docx = _render(report, tmp_path)
        report["sections"] = list(reversed(report["sections"]))
        result = validate_report_artifact(report, docx)
        assert result["artifact_ready"] is False
        assert any("claim_order_preserved" in e for e in result["errors"])

    def test_missing_disclosure_fails(self, tmp_path):
        report = make_report(build_contract())
        report["claims"] = [
            c for c in report["claims"] if c["claim_id"] != "C001"
        ]
        docx = _render(report, tmp_path)
        result = validate_report_artifact(report, docx)
        assert result["artifact_ready"] is False
        assert any("required_disclosures_rendered" in e for e in result["errors"])

    def test_facts_and_poll_cutoff_required(self, tmp_path):
        report = make_report(build_contract())
        docx = _render(report, tmp_path)
        result = validate_report_artifact(report, docx)
        text = extract_word_text(docx)
        assert "2026-07-27" in text
        assert "2026-03-12" in text
        assert result["required_disclosures_complete"] is True

    def test_word_signature_deterministic(self, tmp_path):
        report = make_report(build_contract())
        docx = _render(report, tmp_path)
        assert word_text_signature(docx) == word_text_signature(docx)

    def test_generation_validation_gate(self, tmp_path):
        report = make_report(build_contract())
        docx = _render(report, tmp_path)
        result = validate_report_artifact(
            report,
            docx,
            generation_validation={"report_generation_ready": False, "all_claims_validated": False},
        )
        assert result["artifact_ready"] is False
