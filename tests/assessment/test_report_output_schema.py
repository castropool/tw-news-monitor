from app.assessment.report_output_schema import is_valid_report_schema, validate_report_schema
from tests.assessment.llm.conftest import build_contract, make_report


class TestReportOutputSchema:
    def test_valid_report_passes(self):
        report = make_report(build_contract())
        assert validate_report_schema(report) == []
        assert is_valid_report_schema(report) is True

    def test_extra_top_level_field_fails(self):
        report = make_report(build_contract())
        report["extra_field"] = True
        assert validate_report_schema(report)

    def test_missing_required_field_fails(self):
        report = make_report(build_contract())
        del report["sections"]
        assert any("缺少顶层必需字段" in e for e in validate_report_schema(report))

    def test_invalid_claim_type_fails(self):
        report = make_report(build_contract())
        report["claims"][0]["claim_type"] = "invalid_type"
        assert any("claim_type 非法" in e for e in validate_report_schema(report))

    def test_invalid_confidence_fails(self):
        report = make_report(build_contract())
        report["claims"][0]["confidence"] = "certain"
        assert any("confidence 非法" in e for e in validate_report_schema(report))

    def test_claim_extra_field_fails(self):
        report = make_report(build_contract())
        report["claims"][0]["extra"] = 1
        assert any("额外字段" in e for e in validate_report_schema(report))

