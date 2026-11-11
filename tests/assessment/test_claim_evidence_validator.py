import pytest

from app.assessment.claim_evidence_validator import (
    build_evidence_context,
    validate_structured_report,
)
from tests.assessment.llm.conftest import build_contract, make_report


def _validate(report, contract=None, ctx=None, expected_mode="draft_with_data_gap"):
    from app.assessment.claim_evidence_validator import build_evidence_context

    contract = contract or build_contract()
    ctx = ctx or build_evidence_context(
        contract,
        evidence_pack=None,
        config={"report_generation": {"entity_whitelist": ["民进党", "国民党", "民众党", "绿营", "蓝营", "白营"]}},
    )
    return validate_structured_report(report, ctx, expected_mode=expected_mode)


def _claim(report, cid):
    return next(c for c in report["claims"] if c["claim_id"] == cid)


class TestReferenceValidation:
    def test_unknown_event_fails(self):
        r = make_report(build_contract(), "invalid_unknown_event")
        v = _validate(r)
        assert v["all_event_ids_exist"] is False
        assert v["all_claims_validated"] is False

    def test_unknown_poll_fails(self):
        r = make_report(build_contract(), "invalid_unknown_poll")
        v = _validate(r)
        assert v["all_poll_ids_exist"] is False

    def test_unknown_source_fails(self):
        r = make_report(build_contract(), "invalid_unknown_source")
        v = _validate(r)
        assert v["all_source_ids_exist"] is False

    def test_unknown_gap_fails(self):
        r = make_report(build_contract())
        _claim(r, "C011")["supporting_gap_ids"] = ["gap_unknown"]
        assert _validate(r)["all_gap_ids_exist"] is False

    def test_unknown_dimension_fails(self):
        r = make_report(build_contract())
        _claim(r, "C008")["supporting_snapshot_dimensions"] = ["dim_unknown"]
        assert _validate(r)["all_snapshot_dimensions_exist"] is False

    def test_event_source_relation_error_fails(self):
        r = make_report(build_contract())
        _claim(r, "C007")["supporting_source_ids"] = ["s3"]
        assert _validate(r)["event_source_relationships_valid"] is False

    def test_poll_source_relation_error_fails(self):
        r = make_report(build_contract())
        _claim(r, "C005")["supporting_poll_ids"] = ["p1"]
        _claim(r, "C005")["supporting_source_ids"] = ["s1"]
        assert _validate(r)["poll_source_relationships_valid"] is False


class TestClaimTypeRules:
    def test_factual_without_evidence_fails(self):
        r = make_report(build_contract())
        _claim(r, "C007")["supporting_event_ids"] = []
        v = _validate(r)
        assert v["claim_type_rules_valid"] is False

    def test_current_assessment_insufficient_evidence_fails(self):
        r = make_report(build_contract())
        _claim(r, "C008")["supporting_event_ids"] = ["e1"]
        _claim(r, "C008")["supporting_snapshot_dimensions"] = []
        assert _validate(r)["claim_type_rules_valid"] is False

    def test_comparative_without_dimension_fails(self):
        r = make_report(build_contract())
        _claim(r, "C009")["supporting_snapshot_dimensions"] = []
        assert _validate(r)["claim_type_rules_valid"] is False

    def test_forward_outlook_without_basis_fails(self):
        r = make_report(build_contract())
        _claim(r, "C010")["inference_basis"] = ""
        assert _validate(r)["forward_outlook_rules_valid"] is False

    def test_forward_outlook_high_confidence_fails(self):
        r = make_report(build_contract())
        _claim(r, "C010")["confidence"] = "high"
        assert _validate(r)["forward_outlook_rules_valid"] is False

    def test_limitation_without_gap_fails(self):
        r = make_report(build_contract())
        _claim(r, "C011")["supporting_gap_ids"] = []
        _claim(r, "C011")["claim_text"] = "天气不错。"
        assert _validate(r)["claim_type_rules_valid"] is False

    def test_disclosure_mismatch_fails(self):
        r = make_report(build_contract())
        _claim(r, "C005")["claim_text"] = "正式民调截止至1999-01-01"
        assert _validate(r)["required_disclosures_complete"] is False


class TestNumbersDatesEntities:
    def test_unfounded_percentage_fails(self):
        r = make_report(build_contract(), "invalid_numeric_claim")
        assert _validate(r)["numeric_claims_grounded"] is False

    def test_unfounded_date_fails(self):
        r = make_report(build_contract(), "invalid_date_claim")
        assert _validate(r)["date_claims_grounded"] is False

    def test_valid_dates_pass(self):
        r = make_report(build_contract())
        assert _validate(r)["date_claims_grounded"] is True

    def test_poll_date_allowed(self):
        r = make_report(build_contract())
        _claim(r, "C005")["claim_text"] = "正式民调截止至2026-03-12"
        assert _validate(r)["date_claims_grounded"] is True

    def test_uncovered_dates_not_no_events(self):
        r = make_report(build_contract())
        _claim(r, "C002")["claim_text"] = "2026-07-28至2026-07-31没有重要事件"
        v = _validate(r)
        assert v["required_disclosures_complete"] is False
        assert v["all_claims_validated"] is False

    def test_old_poll_as_current_fails(self):
        r = make_report(build_contract())
        _claim(r, "C005")["supporting_poll_ids"] = ["p1"]
        _claim(r, "C005")["claim_text"] = "当前支持率显示陈亭妃领先41.0%"
        assert _validate(r)["no_unsupported_poll_claims"] is False

    def test_win_probability_fails(self):
        r = make_report(build_contract())
        _claim(r, "C008")["claim_text"] = "陈亭妃胜选概率约60%"
        assert _validate(r)["no_unsupported_probability"] is False

    def test_unknown_person_fails(self):
        r = make_report(build_contract())
        _claim(r, "C008")["claim_text"] = "综合判断认为张三丰将加入选战。"
        assert _validate(r)["person_names_grounded"] is False

    def test_unknown_org_fails(self):
        r = make_report(build_contract())
        _claim(r, "C008")["claim_text"] = "虚构党宣布介入台南选情。"
        assert _validate(r)["organization_names_grounded"] is False

    def test_known_mention_passes(self):
        r = make_report(build_contract())
        _claim(r, "C008")["claim_text"] = "陈亭妃与谢龙介的选情格局保持稳定。"
        v = _validate(r)
        assert v["person_names_grounded"] is True

    def test_surname_shorthand_is_not_misclassified_as_person(self):
        r = make_report(build_contract())
        _claim(r, "C008")["claim_text"] = (
            "赖陈同框看板属于公开视觉动作，陈阵营简称不作为人物实体。"
        )
        assert _validate(r)["person_names_grounded"] is True

    def test_whitelist_party_passes(self):
        r = make_report(build_contract())
        _claim(r, "C008")["claim_text"] = "民进党与国民党在台南的结构对比保持稳定。"
        assert _validate(r)["organization_names_grounded"] is True


class TestDoNotInferAndEdges:
    def test_chen_full_integration_fails(self):
        r = make_report(build_contract())
        _claim(r, "C008")["claim_text"] = "陈亭妃已完成全面整合。"
        v = _validate(r)
        assert v["do_not_infer_compliant"] is False

    def test_hsieh_full_machine_fails(self):
        r = make_report(build_contract())
        _claim(r, "C008")["claim_text"] = "谢龙介已建立全市成熟竞选机器。"
        assert _validate(r)["do_not_infer_compliant"] is False

    def test_bluewhite_full_integration_fails(self):
        r = make_report(build_contract())
        _claim(r, "C009")["claim_text"] = "台南蓝白已完成全面整合。"
        assert _validate(r)["do_not_infer_compliant"] is False

    def test_national_agreement_seats_fails(self):
        r = make_report(build_contract())
        _claim(r, "C009")["claim_text"] = "全国协议代表台南全市席次分配已完成。"
        assert _validate(r)["do_not_infer_compliant"] is False

    def test_district_extrapolation_fails(self):
        r = make_report(build_contract())
        _claim(r, "C009")["claim_text"] = "第一选区合作可在全市复制推广。"
        assert _validate(r)["do_not_infer_compliant"] is False

    def test_resources_shared_fails(self):
        r = make_report(build_contract())
        _claim(r, "C009")["claim_text"] = "蓝白志工与数据库已经共享。"
        assert _validate(r)["do_not_infer_compliant"] is False

    def test_negated_conclusion_not_violation(self):
        r = make_report(build_contract())
        _claim(r, "C009")["claim_text"] = "现有证据不足以证明台南蓝白已完成全市整合。"
        v = _validate(r)
        assert v["do_not_infer_compliant"] is True

    def test_background_as_period_event_fails(self):
        r = make_report(build_contract())
        _claim(r, "C007")["supporting_event_ids"] = ["bg1"]
        _claim(r, "C007")["claim_text"] = "本期发生民进党初选结果公布。"
        assert _validate(r)["no_background_as_period_event"] is False

    def test_reference_removal_as_deletion_fails(self):
        r = make_report(build_contract())
        _claim(r, "C007")["claim_text"] = "正式事件库已删除旧事件。"
        assert _validate(r)["no_reference_removal_as_deletion"] is False

    def test_nonmaterial_gap_as_material_fails(self):
        r = make_report(build_contract())
        _claim(r, "C011")["supporting_gap_ids"] = ["rt07_feb_mar_gap"]
        _claim(r, "C011")["claim_text"] = "该缺口已解决。"
        ctx = build_evidence_context(
            build_contract(),
            evidence_pack={"gap_changes": [{"stable_gap_id": "rt07_feb_mar_gap", "material_for_report": False}]},
            config={},
        )
        assert _validate(r, ctx=ctx)["no_nonmaterial_gap_as_material_change"] is False


class TestSchemaAndIds:
    def test_schema_extra_field_fails(self):
        r = make_report(build_contract(), "invalid_schema")
        assert _validate(r)["output_schema_valid"] is False

    def test_generation_mode_mismatch_fails(self):
        r = make_report(build_contract(), "invalid_generation_mode")
        assert _validate(r)["generation_mode_valid"] is False

    def test_duplicate_claim_id_fails(self):
        r = make_report(build_contract())
        r["claims"][1]["claim_id"] = r["claims"][0]["claim_id"]
        assert _validate(r)["all_claim_ids_unique"] is False

    def test_section_missing_claim_fails(self):
        r = make_report(build_contract())
        r["sections"][0]["claim_ids"] = ["C999"]
        assert _validate(r)["all_section_claim_ids_exist"] is False

    def test_title_missing_claim_fails(self):
        r = make_report(build_contract())
        r["title_claim_ids"] = ["C999"]
        assert _validate(r)["all_title_claim_ids_exist"] is False

    def test_missing_disclosure_fails(self):
        r = make_report(build_contract(), "invalid_missing_disclosure")
        assert _validate(r)["required_disclosures_complete"] is False

    def test_valid_report_passes(self):
        v = _validate(make_report(build_contract()))
        assert v["all_claims_validated"] is True
        assert v["errors"] == []
