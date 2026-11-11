from app.assessment.report_renderer import render_report_markdown
from tests.assessment.llm.conftest import build_contract, make_report


class TestReportRenderer:
    def test_renders_validated_claims_only(self):
        report = make_report(build_contract())
        contract = build_contract()
        rendered = render_report_markdown(report, contract)
        text = rendered["markdown"]
        claim = next(c for c in report["claims"] if c["claim_id"] == "C007")
        assert claim["claim_text"] in text
        assert "C999" not in text

    def test_section_order(self):
        report = make_report(build_contract())
        text = render_report_markdown(report, build_contract())["markdown"]
        headings = ["一、总体判断", "二、本期关键变化", "八、证据限制"]
        positions = [text.index(h) for h in headings]
        assert positions == sorted(positions)

    def test_evidence_mapping_complete(self):
        text = render_report_markdown(make_report(build_contract()), build_contract())["markdown"]
        assert "【证据映射】" in text
        assert "event:" in text

    def test_required_disclosures_in_body(self):
        text = render_report_markdown(make_report(build_contract()), build_contract())["markdown"]
        assert "正式事实底表仅覆盖至" in text
        assert "正式民调截止至" in text

    def test_no_model_call(self):
        # 渲染器是纯函数，不接收 provider
        import inspect

        assert "provider" not in inspect.signature(render_report_markdown).parameters

    def test_short_output_not_padded(self):
        report = make_report(build_contract())
        report["claims"] = report["claims"][:2]
        rendered = render_report_markdown(report, build_contract())
        assert rendered["length_below_target"] is True
        assert rendered["chinese_char_count"] < 1800

