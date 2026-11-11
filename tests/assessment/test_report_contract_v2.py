"""v2.0 研判单元契约 golden 测试：结构校验、派生、渲染顺序、向后兼容。

用户人工审阅意见 -> 需求映射（原样保留）：
1. “文章事实基本可以过关。” -> 证据包内事实门禁保持/加强：每个判断必须
   绑定证据（evidence_refs/evidence_items 只允许证据包内 ID），旧民调必须
   带日期与局限披露，不得把旧民调写成当前支持率。
2. “文章结构与预想存在较大出入：需要先说观点、判断，再用最近的事实证据提供
   佐证，而不是只罗列一堆新闻事实。” -> 结论摘要(1-3 条可证伪判断)先行 +
   研判单元固定顺序（判断 -> 证据 2-4 条带日期+evidence_id -> 推理链 ->
   反证/限制 -> 置信度 -> 观察指标）；以新闻事实列表开头且无推理链必须失败。
3. “整体写作风格非常零散，每个部分都提到了，但是又都没说明白。” -> 全文
   最多 3 个核心研判，宁可少而讲透；同一证据不得跨单元重复堆叠；不能形成
   判断的内容进附录；判断不得是“值得关注/有待观察”零信息套话；推理链不得
   只是复述判断或证据。
"""

from __future__ import annotations

import json
from copy import deepcopy
from pathlib import Path

import pytest

from app.assessment.claim_evidence_validator import (
    build_evidence_context,
    validate_structured_report,
)
from app.assessment.llm_input_contract import build_data_context
from app.assessment.report_output_schema import (
    validate_report_schema,
    validate_report_schema_v2,
)
from app.assessment.report_renderer import render_report_markdown
from app.assessment.report_structure_validator import (
    derive_claims_and_sections,
    is_zero_info_judgment,
    validate_report_structure_v2,
)
from app.assessment.word_report_renderer import (
    V2_SECTION_ORDER,
    extract_word_body,
    extract_word_text,
    render_word_report,
)
from tests.assessment.llm.conftest import build_contract, make_report

CASES = json.loads(
    (
        Path(__file__).parent / "golden" / "report_contract_v2_cases.json"
    ).read_text(encoding="utf-8")
)


def _disclosure_compliance(contract):
    return [
        {"rule_id": f"dni_{i}", "rule_text": text, "violated": False, "related_claim_ids": []}
        for i, text in enumerate(contract.get("do_not_infer") or [], 1)
    ]


def _base_v2(contract=None):
    contract = contract or build_contract()
    return {
        "schema_version": "2.0",
        "report_id": "v2-golden",
        "election_id": contract["election_id"],
        "report_period": deepcopy(contract["report_period"]),
        "generation_mode": "draft_with_data_gap",
        "report_status": "generated",
        "title": "蓝白合作进入实质协调但全市制度化未完成",
        "conclusion_summary": [
            {
                "summary_id": "CS1",
                "judgment": "本期蓝白合作由提案阶段进入选区实质协调，但全市制度化尚未完成，选情结构仍以民进党优势为主。",
                "confidence": "medium",
                "evidence_refs": {
                    "event_ids": ["e1", "e2"],
                    "poll_ids": ["p1"],
                    "source_ids": ["s1", "s2", "s3"],
                    "dimension_ids": ["kmt_tpp_cooperation"],
                },
            }
        ],
        "core_assessments": [
            {
                "assessment_id": "CA1",
                "judgment": "蓝白合作本期进入选区实质协调阶段，但离全市制度化仍有关键距离。",
                "evidence_items": [
                    {
                        "evidence_id": "e1",
                        "evidence_date": "2026-07-20",
                        "evidence_summary": "陈亭妃与民进党议员拍摄联合宣传照",
                    },
                    {
                        "evidence_id": "e2",
                        "evidence_date": "2026-07-21",
                        "evidence_summary": "谢龙介在安南区庙口拜票",
                    },
                ],
                "evidence_refs": {
                    "event_ids": ["e1", "e2"],
                    "source_ids": ["s1", "s2"],
                    "dimension_ids": ["kmt_tpp_cooperation"],
                },
                "reasoning": "两项正式动作均发生在本期且方向一致：谢龙介持续以庙口拜票巩固基本盘，蓝白协调动作与快照维度变化方向吻合，说明合作进入操作层面；但证据只显示选区级协调，未出现全市制度化的正式安排，因此判断停留在实质协调而未完成制度化。",
                "falsifiers_or_limits": "若下期出现蓝白双方正式签署全市合作或席次分配文件，或正式民调出现相反方向的显著变化，本判断将被削弱；正式民调截止于2026-03-12，不能代表当前实时支持率。",
                "confidence": "medium",
                "watch_indicators": ["蓝白是否出现全市性联合活动", "下一期是否有新的正式民调"],
            },
            {
                "assessment_id": "CA2",
                "judgment": "民进党阵营在台南的优势仍主要建立在初选结果与既有选民结构之上，本期组织动作尚不足以改变基本盘。",
                "evidence_items": [
                    {
                        "evidence_id": "p1",
                        "evidence_date": "2026-03-16",
                        "evidence_summary": "TVBS民调显示二选一支持度陈亭妃领先",
                    },
                    {
                        "evidence_id": "bg1",
                        "evidence_date": "2026-01-15",
                        "evidence_summary": "民进党台南市长初选结果",
                    },
                ],
                "evidence_refs": {
                    "event_ids": ["bg1"],
                    "poll_ids": ["p1"],
                    "source_ids": ["s3"],
                    "dimension_ids": ["overall_race_structure"],
                },
                "reasoning": "民调显示陈亭妃领先，但该民调截止于2026-03-12且为旧数据，只能说明既有优势基础；初选结果进一步确认民进党提名结构，两者共同支持优势结构判断，但不能推出整合完成或全面领先的强结论。",
                "falsifiers_or_limits": "若民进党官方宣布陈亭妃全面整合，或新的正式民调显示格局显著变化，本判断将被推翻。",
                "confidence": "medium",
                "watch_indicators": ["民进党是否公布整合声明", "下一期正式民调结果"],
            },
        ],
        "appendix": [
            {
                "item_id": "AP1",
                "item_type": "data_limitation",
                "item_text": "民调空窗限制本期研判强度；正式民调截止至2026-03-12。",
                "evidence_refs": {
                    "poll_ids": ["p1"],
                    "source_ids": ["s3"],
                    "gap_ids": ["gap_polling"],
                },
            },
            {
                "item_id": "AP2",
                "item_type": "background_fact",
                "item_text": "民进党台南市长初选结果为陈亭妃胜出（2026-01-15 背景事实）。",
                "evidence_refs": {"event_ids": ["bg1"], "source_ids": ["s3"]},
            },
        ],
        "required_disclosures": [
            "正式事实底表仅覆盖至 2026-07-27",
            "正式民调截止至 2026-03-12",
            "本期未覆盖日期：2026-07-28、2026-07-29、2026-07-30、2026-07-31",
            "不得将未覆盖期间表述为没有重要事件",
            "报告只能作为数据不完整草稿",
            "本期没有新增正式民调",
        ],
        "do_not_infer_compliance": _disclosure_compliance(contract),
        "report_statistics": {
            "claim_count": 0,
            "section_count": 3,
            "core_assessment_count": 2,
            "conclusion_summary_count": 1,
            "evidence_item_count": 4,
            "chinese_char_count": 0,
            "length_below_target": True,
        },
        "data_context": build_data_context(contract),
    }


def _find_assessment(report, assessment_id):
    return next(
        a for a in report["core_assessments"] if a["assessment_id"] == assessment_id
    )


def _mutate(report, mutation):
    if mutation == "none":
        return
    if mutation == "news_list_start_no_reasoning":
        report["conclusion_summary"] = []
        for a in report["core_assessments"]:
            a["reasoning"] = ""
    elif mutation == "facts_only_no_reasoning":
        _find_assessment(report, "CA1")["reasoning"] = ""
    elif mutation == "eight_shallow_columns":
        template = deepcopy(report["core_assessments"][0])
        report["core_assessments"] = []
        for i in range(1, 9):
            unit = deepcopy(template)
            unit["assessment_id"] = f"CA{i}"
            unit["judgment"] = f"第{i}个栏目本期有所动作。"
            unit["reasoning"] = "该动作说明该栏目仍在推进，具体影响需要后续观察。"
            report["core_assessments"].append(unit)
    elif mutation == "vague_non_falsifiable_judgment":
        _find_assessment(report, "CA1")["judgment"] = "蓝白合作值得关注，有待观察。"
    elif mutation == "stale_evidence_as_current":
        items = _find_assessment(report, "CA1")["evidence_items"]
        items.append(
            {
                "evidence_id": "p1",
                "evidence_date": "2026-03-16",
                "evidence_summary": "最新民调显示支持率领先",
            }
        )
    elif mutation == "same_fact_repeated":
        _find_assessment(report, "CA2")["evidence_items"].append(
            {
                "evidence_id": "e1",
                "evidence_date": "2026-07-20",
                "evidence_summary": "陈亭妃与民进党议员拍摄联合宣传照",
            }
        )
    elif mutation == "missing_evidence_items":
        _find_assessment(report, "CA1")["evidence_items"] = [
            _find_assessment(report, "CA1")["evidence_items"][0]
        ]
    elif mutation == "short_reasoning":
        _find_assessment(report, "CA1")["reasoning"] = "支持判断。"
    elif mutation == "reasoning_paraphrase":
        _find_assessment(report, "CA1")["reasoning"] = (
            "蓝白合作本期进入选区实质协调阶段，但离全市制度化仍有关键距离。"
        )
    elif mutation == "judgment_duplicates_evidence":
        _find_assessment(report, "CA1")["judgment"] = "陈亭妃与民进党议员拍摄联合宣传照"
    elif mutation == "unknown_evidence_id":
        _find_assessment(report, "CA1")["evidence_items"].append(
            {
                "evidence_id": "evt_unknown_999",
                "evidence_date": "2026-07-25",
                "evidence_summary": "未知证据",
            }
        )
    elif mutation == "wrong_evidence_date":
        _find_assessment(report, "CA1")["evidence_items"][0]["evidence_date"] = "2026-07-19"
    elif mutation == "missing_watch_indicators":
        _find_assessment(report, "CA2")["watch_indicators"] = []
    elif mutation == "missing_falsifiers":
        _find_assessment(report, "CA2")["falsifiers_or_limits"] = ""
    elif mutation == "missing_required_disclosure":
        report["required_disclosures"] = [
            t for t in report["required_disclosures"] if "事实底表" not in t
        ]
    elif mutation == "empty_conclusion_judgment":
        report["conclusion_summary"][0]["judgment"] = ""
    elif mutation == "no_evidence_binding":
        _find_assessment(report, "CA1")["evidence_refs"] = {}
    else:
        raise AssertionError(mutation)


def _ctx(contract):
    return build_evidence_context(
        contract,
        evidence_pack=None,
        config={"report_generation": {"entity_whitelist": ["民进党", "国民党", "民众党", "绿营", "蓝营", "白营"]}},
    )


def _validate(report, contract, *, assembled=False):
    return validate_structured_report(
        report, _ctx(contract), expected_mode="draft_with_data_gap", assembled=assembled
    )


class TestGoldenContractV2:
    @pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
    def test_golden(self, case):
        contract = build_contract()
        report = _base_v2(contract)
        _mutate(report, case["mutation"])
        result = _validate(report, contract)
        if case["expected_valid"]:
            assert result["all_claims_validated"] is True, result["errors"]
            assert result["report_structure_valid"] is True
        else:
            assert result["all_claims_validated"] is False, case["id"]
            assert case["expected_error"] in " ".join(result["errors"]), result["errors"]

    def test_golden_inventory_is_exactly_18_with_holdouts(self):
        assert len(CASES) == 18
        assert sum(not case["expected_valid"] for case in CASES) >= 12


class TestStructureValidator:
    def test_zero_info_judgment_detector(self):
        assert is_zero_info_judgment("值得关注。")
        assert is_zero_info_judgment("有待观察")
        assert is_zero_info_judgment("需持续关注")
        assert not is_zero_info_judgment("陈亭妃整合进度落后于预期，蓝白合作空间收窄。")
        assert not is_zero_info_judgment("总体格局保持稳定。")

    def test_two_complete_units_pass_structure(self):
        contract = build_contract()
        report = _base_v2(contract)
        structure = validate_report_structure_v2(report, _ctx(contract))
        assert structure["errors"] == []
        assert structure["core_assessment_count"] == 2
        assert structure["evidence_item_count"] == 4

    def test_news_list_start_fails(self):
        contract = build_contract()
        report = _base_v2(contract)
        _mutate(report, "news_list_start_no_reasoning")
        structure = validate_report_structure_v2(report, _ctx(contract))
        assert any("conclusion_summary" in e for e in structure["errors"])
        assert any("reasoning" in e for e in structure["errors"])

    def test_more_than_three_core_assessments_fails(self):
        contract = build_contract()
        report = _base_v2(contract)
        _mutate(report, "eight_shallow_columns")
        structure = validate_report_structure_v2(report, _ctx(contract))
        assert any("超过 3 个核心研判" in e for e in structure["errors"])

    def test_derived_claims_keep_evidence_ids(self):
        contract = build_contract()
        report = _base_v2(contract)
        claims, sections = derive_claims_and_sections(report, _ctx(contract))
        by_id = {c["claim_id"]: c for c in claims}
        assert by_id["CA1"]["supporting_event_ids"] == ["e1", "e2"]
        assert by_id["CA1"]["inference_basis"] == report["core_assessments"][0]["reasoning"]
        ev_claim = by_id["EV_CA1_1"]
        assert ev_claim["supporting_event_ids"] == ["e1"]
        assert ev_claim["supporting_source_ids"] == ["s1"]
        assert [s["heading"] for s in sections] == V2_SECTION_ORDER


class TestV2SchemaValidation:
    def test_valid_v2_passes(self):
        assert validate_report_schema_v2(_base_v2(build_contract())) == []

    def test_v1_schema_still_validates_v11(self):
        # 旧 run 兼容：v1.1 结构校验保持不变。
        assert validate_report_schema(make_report(build_contract())) == []

    def test_v2_extra_top_field_fails(self):
        report = _base_v2(build_contract())
        report["invented"] = True
        assert any("多余字段" in e for e in validate_report_schema_v2(report))

    def test_v2_derived_fields_tolerated(self):
        report = _base_v2(build_contract())
        report["claims"] = []
        report["sections"] = []
        assert validate_report_schema_v2(report) == []

    def test_v2_four_assessments_fail_schema(self):
        report = _base_v2(build_contract())
        report["core_assessments"] = report["core_assessments"] * 2
        assert any("1-3" in e for e in validate_report_schema_v2(report))

    def test_v2_empty_conclusion_fails_schema(self):
        report = _base_v2(build_contract())
        report["conclusion_summary"] = []
        assert any("conclusion_summary" in e for e in validate_report_schema_v2(report))

    def test_v2_evidence_items_out_of_range_fails(self):
        report = _base_v2(build_contract())
        report["core_assessments"][0]["evidence_items"] = [
            report["core_assessments"][0]["evidence_items"][0]
        ]
        assert any("2-4" in e for e in validate_report_schema_v2(report))


class TestV2Rendering:
    def test_markdown_first_body_block_is_conclusion_summary(self):
        contract = build_contract()
        report = _base_v2(contract)
        markdown = render_report_markdown(report, contract)["markdown"]
        summary_pos = markdown.index("一、结论摘要")
        core_pos = markdown.index("二、核心研判")
        appendix_pos = markdown.index("三、数据限制与事实附录")
        assert summary_pos < core_pos < appendix_pos
        # 正文第一块是结论摘要，而不是事件/新闻列表。
        assert "一、结论摘要" in markdown
        assert markdown.index("一、结论摘要") < markdown.index("陈亭妃与民进党议员拍摄联合宣传照")

    def test_markdown_unit_order_judgment_evidence_reasoning_falsifier_confidence(self):
        report = _base_v2(build_contract())
        markdown = render_report_markdown(report, build_contract())["markdown"]
        # 限定在第一个研判单元内校验 判断->证据->推理->反证/限制->置信度 顺序。
        unit = markdown[markdown.index("研判1") : markdown.index("研判2")]
        order = [
            "研判1",
            "最近事实证据",
            "推理链",
            "反证/限制条件",
            "置信度",
        ]
        positions = [unit.index(token) for token in order]
        assert positions == sorted(positions)

    def test_markdown_contains_evidence_ids_and_dates(self):
        report = _base_v2(build_contract())
        markdown = render_report_markdown(report, build_contract())["markdown"]
        assert "2026-07-20" in markdown
        assert "（e1）" in markdown
        assert "（e2）" in markdown

    def test_word_first_section_is_conclusion_summary(self, tmp_path):
        contract = build_contract()
        report = _base_v2(contract)
        report["report_status"] = "generated"
        info = render_word_report(report, output_dir=tmp_path, mode="development")
        text = extract_word_text(Path(info["docx_path"]))
        positions = [text.index(h) for h in V2_SECTION_ORDER]
        assert positions == sorted(positions)
        # 旧的八栏目标题不得出现在 v2 报告正文。
        for legacy in ("三、陈亭妃整合进展", "四、谢龙介组织及竞选动作", "八、证据限制"):
            assert legacy not in extract_word_body(Path(info["docx_path"]))

    def test_word_unit_renders_all_five_parts(self, tmp_path):
        report = _base_v2(build_contract())
        report["report_status"] = "generated"
        info = render_word_report(report, output_dir=tmp_path, mode="development")
        text = extract_word_text(Path(info["docx_path"]))
        for label in ("判断：", "证据：", "推理：", "反证/限制：", "置信度与观察指标："):
            assert label in text
        assert "蓝白合作本期进入选区实质协调阶段" in text

    def test_legacy_v11_word_rendering_unchanged(self, tmp_path):
        # 旧 run 展示兼容：v1.1 报告仍按原八栏目渲染。
        report = make_report(build_contract())
        info = render_word_report(report, output_dir=tmp_path, mode="development")
        text = extract_word_text(Path(info["docx_path"]))
        assert "一、总体判断" in text
        assert "八、证据限制" in text

    def test_legacy_v11_validation_unchanged(self):
        report = make_report(build_contract())
        result = _validate(report, build_contract())
        assert result["all_claims_validated"] is True
        assert result["output_schema_valid"] is True


class TestV2FactGates:
    def test_old_poll_as_current_fails(self):
        contract = build_contract()
        report = _base_v2(contract)
        _mutate(report, "stale_evidence_as_current")
        result = _validate(report, contract)
        assert result["all_claims_validated"] is False
        assert any("旧民调" in e for e in result["errors"])

    def test_old_poll_date_must_match_pack(self):
        contract = build_contract()
        report = _base_v2(contract)
        _mutate(report, "wrong_evidence_date")
        result = _validate(report, contract)
        assert any("与证据包记录不一致" in e for e in result["errors"])

    def test_evidence_outside_pack_fails(self):
        contract = build_contract()
        report = _base_v2(contract)
        _mutate(report, "unknown_evidence_id")
        result = _validate(report, contract)
        assert any("不在证据包内" in e for e in result["errors"])

    def test_no_new_facts_outside_pack(self):
        # 证据包外人物/数字在派生 claim 级被拦截。
        contract = build_contract()
        report = _base_v2(contract)
        _find_assessment(report, "CA1")["judgment"] = (
            "蓝白合作本期进入选区实质协调阶段，但离全市制度化仍有关键距离，"
            "张三丰宣布参选改变格局。"
        )
        result = _validate(report, contract)
        assert result["all_claims_validated"] is False
        assert any("证据包外人物" in e for e in result["errors"])

    def test_repeated_evidence_across_units_fails(self):
        contract = build_contract()
        report = _base_v2(contract)
        _mutate(report, "same_fact_repeated")
        result = _validate(report, contract)
        assert result["all_claims_validated"] is False
        assert any("重复堆叠" in e for e in result["errors"])


class TestV2ProductionGate:
    def test_disposition_does_not_hard_block_v2_three_sections(self):
        """v2 正文为 3 个 section（结论摘要/核心研判/附录），八栏目硬规则不得误伤。"""
        from app.assessment.r2.disposition import classify_disposition

        contract = build_contract()
        report = _base_v2(contract)
        from app.assessment.generate_llm_report import _enrich_v2_report

        _enrich_v2_report(report, contract)
        validation = _validate(report, contract)
        assert validation["all_claims_validated"] is True
        disposition = classify_disposition(
            validation,
            report,
            outside_events={},
            allowed_event_ids={"e1", "e2", "bg1"},
            integrity_ok=True,
            period_gate_ok=True,
        )
        assert disposition["production_disposition"] == "PASS", disposition["hard_block_reasons"]

    def test_disposition_blocks_v2_when_structure_invalid(self):
        from app.assessment.r2.disposition import classify_disposition

        contract = build_contract()
        report = _base_v2(contract)
        _mutate(report, "news_list_start_no_reasoning")
        validation = _validate(report, contract)
        disposition = classify_disposition(
            validation,
            report,
            outside_events={},
            allowed_event_ids={"e1", "e2", "bg1"},
            integrity_ok=True,
            period_gate_ok=True,
        )
        assert disposition["production_disposition"] == "HARD_BLOCK"
        assert any("schema_severe_damage" in r for r in disposition["hard_block_reasons"])

    def test_legacy_v11_disposition_still_requires_eight_sections(self):
        from app.assessment.r2.disposition import classify_disposition

        contract = build_contract()
        report = make_report(contract)
        validation = _validate(report, contract)
        disposition = classify_disposition(
            validation,
            report,
            outside_events={},
            allowed_event_ids={"e1", "e2"},
            integrity_ok=True,
            period_gate_ok=True,
        )
        # v1.1 mock 报告有 8 个 section，不应被结构硬规则误伤。
        assert "schema_severe_damage_missing_sections" not in disposition["hard_block_reasons"]
        assert disposition["production_disposition"] in ("PASS", "REVIEW_REQUIRED")


class TestV2EndToEndPipeline:
    def test_single_stage_pipeline_emits_v2_contract(self, tmp_path, monkeypatch):
        """生产单阶段链路：v2 模型输出 -> 确定性修复 -> v2 校验 -> 富化 -> 产物。"""
        import app.assessment.generate_llm_report as cli
        from app.assessment.llm.mock_provider import MockProvider

        project_root = Path(__file__).resolve().parent.parent.parent
        evidence_dir = (
            project_root
            / "data/reports/tainan_2026/evidence_packages/2026-07-16_2026-07-31"
        )
        monkeypatch.setattr(
            cli,
            "create_provider",
            lambda provider, config=None, model=None, **kwargs: MockProvider(
                fixture="valid_v2"
            ),
        )
        code = cli.run(
            config_path=project_root / "config" / "election_assessment.yaml",
            evidence_dir=evidence_dir,
            provider="mock",
            model=None,
            allow_draft_with_gap=True,
            validate_only=False,
            force_model_call=True,
            no_repair=True,
            output_root=tmp_path,
            deepseek_thinking="disabled",
        )
        assert code == 0
        out = tmp_path / "2026-07-16_2026-07-31"
        final = json.loads(
            (out / "structured_report_final.json").read_text(encoding="utf-8")
        )
        assert final["schema_version"] == "2.0"
        assert final["conclusion_summary"]
        assert 1 <= len(final["core_assessments"]) <= 3
        # 富化：attempt 文件同时含派生 claims，供机器门禁/展示复用。
        attempt = json.loads(
            (out / "structured_report_attempt_1.json").read_text(encoding="utf-8")
        )
        assert attempt["claims"]
        assert attempt["sections"]
        validation = json.loads(
            (out / "claim_evidence_validation_attempt_1.json").read_text(encoding="utf-8")
        )
        assert validation["all_claims_validated"] is True, validation["errors"]
        assert validation["report_structure_valid"] is True
        assert validation["core_assessment_count"] == 2
        # 渲染首块为结论摘要。
        draft = (out / "report_draft.md").read_text(encoding="utf-8")
        assert draft.index("一、结论摘要") < draft.index("二、核心研判")
        # 契约版本：schema 2.0 + prompt 2.0。
        schema = json.loads((out / "report_output_schema.json").read_text(encoding="utf-8"))
        assert schema["properties"]["schema_version"]["const"] == "2.0"
        manifest = json.loads(
            (out / "report_generation_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["output_schema_version"] == "2.0"
        assert manifest["prompt_versions"] == {
            "system": "2.0",
            "writer": "2.0",
            "repair": "2.0",
        }
        generation = json.loads(
            (out / "report_generation_validation.json").read_text(encoding="utf-8")
        )
        assert generation["report_generation_ready"] is True

    def test_v2_report_roundtrip_machine_gate_reads_derived_claims(self):
        """机器门禁读取 attempt 文件：报告结构 -> 派生 claims 语义校验。"""
        from app.assessment.generate_llm_report import _enrich_v2_report

        contract = build_contract()
        report = _base_v2(contract)
        _enrich_v2_report(report, contract)
        validation = _validate(report, contract)
        assert validation["all_claims_validated"] is True
        semantic = {
            r.get("claim_id"): r for r in validation["claim_semantic_results"]
        }
        assert "CA1" in semantic
        assert "EV_CA1_1" in semantic
        assert all(r["accepted"] for r in semantic.values()), [
            f"{r['claim_id']}: {r['failures']}" for r in semantic.values() if not r["accepted"]
        ]
        # 幂等：重复富化不改变内容。
        before = json.dumps(report, ensure_ascii=False, sort_keys=True)
        _enrich_v2_report(report, contract)
        after = json.dumps(report, ensure_ascii=False, sort_keys=True)
        assert before == after
