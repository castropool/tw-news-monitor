"""确定性 Mock Provider（无随机、无网络、无密钥）。"""

from __future__ import annotations

from copy import deepcopy
from typing import Any

from .base_provider import LLMProvider, ProviderResult
from .errors import LLMRateLimitError, LLMTimeoutError
from ..llm_input_contract import build_data_context


MOCK_FIXTURES = (
    "valid_final",
    "valid_draft_with_gap",
    "valid_v2",
    "invalid_unknown_event",
    "invalid_unknown_poll",
    "invalid_unknown_source",
    "invalid_missing_evidence",
    "invalid_numeric_claim",
    "invalid_date_claim",
    "invalid_do_not_infer",
    "invalid_missing_disclosure",
    "invalid_generation_mode",
    "invalid_schema",
    "repairable_invalid",
    "unrepairable_invalid",
    "provider_timeout",
    "provider_rate_limit",
)


class MockProvider(LLMProvider):
    def __init__(self, model: str = "mock-model", fixture: str = "valid_draft_with_gap"):
        if fixture not in MOCK_FIXTURES:
            raise ValueError(f"未知 mock fixture: {fixture}")
        self.model = model
        self.fixture = fixture

    def generate_structured_report(
        self,
        *,
        system_prompt: str,
        user_payload: dict,
        output_schema: dict,
        request_metadata: dict,
    ) -> ProviderResult:
        if self.fixture == "provider_timeout":
            raise LLMTimeoutError("mock timeout")
        if self.fixture == "provider_rate_limit":
            raise LLMRateLimitError("mock rate limit")

        attempt = int(request_metadata.get("attempt", 1) or 1)
        contract = user_payload
        if "llm_input_contract" in user_payload:
            contract = user_payload["llm_input_contract"]
        if self.fixture == "repairable_invalid" and attempt >= 2:
            report = _build_valid_report(contract, fixture="valid_draft_with_gap")
        elif self.fixture == "valid_v2":
            report = _build_valid_v2_report(contract)
        elif self.fixture in ("valid_final", "valid_draft_with_gap"):
            report = _build_valid_report(contract, fixture=self.fixture)
        else:
            report = _build_valid_report(contract, fixture="valid_draft_with_gap")
            report = _apply_invalid_mutation(report, self.fixture)

        return ProviderResult(
            provider="mock",
            model=self.model,
            structured_output=report,
            response_id=f"mock-{self.fixture}-{attempt}",
            input_token_count=123,
            output_token_count=456,
            total_token_count=579,
            finish_status="completed",
            request_duration_ms=5,
            provider_warnings=[],
        )


def _build_valid_report(payload: dict, fixture: str) -> dict:
    eligibility = payload.get("generation_eligibility") or {}
    mode = "final" if fixture == "valid_final" else eligibility.get("allowed_generation_mode", "draft_with_data_gap")
    period_events = payload.get("period_events") or []
    background_events = payload.get("background_events") or []
    polls = payload.get("polls") or []
    gaps = payload.get("coverage_gaps") or []
    dims = payload.get("state_diff", {}).get("dimensions") or []
    data_status = payload.get("data_status") or {}
    poll_cutoff = data_status.get("poll_cutoff") or "2026-03-12"
    poll_gap = (payload.get("evidence_statistics") or {}).get("poll_gap", True)
    facts_cutoff = str(data_status.get("facts_cutoff") or "")
    uncovered = list(data_status.get("uncovered_date_range") or [])
    pe0 = period_events[0] if period_events else {}
    pe1 = period_events[1] if len(period_events) > 1 else pe0
    bg0 = background_events[0] if background_events else pe0
    poll0 = polls[0] if polls else None
    gap_ids = [g.get("gap_id") or g.get("stable_gap_id") for g in gaps if g.get("gap_id") or g.get("stable_gap_id")]

    def event_source_ids(ev: dict) -> list[str]:
        return (ev.get("source_ids") or [])[:1]

    disclosures = list(eligibility.get("required_disclosures") or [])
    if not disclosures:
        # 完全覆盖（facts_cutoff >= period_end）时 eligibility 不再要求披露，
        # mock 补齐四类权威披露，且文本与 C005/C006 保持不同，避免重复定位。
        uncovered = list(data_status.get("uncovered_date_range") or [])
        disclosures = [
            f"正式事实底表仅覆盖至 {facts_cutoff}" if facts_cutoff else "事实截止信息缺失",
            ("本期未覆盖日期：" + "、".join(uncovered)) if uncovered else "本期无未覆盖日期。",
            f"权威民调截止日期：{poll_cutoff}",
            "民调空窗：本期未发布新的正式民调",
        ]

    claims: list[dict] = []
    if len(disclosures) > 0:
        claims.append(_claim("C001", "data_disclosure", disclosures[0], confidence="not_applicable", material=False))
    if len(disclosures) > 1:
        claims.append(_claim("C002", "data_disclosure", disclosures[1], confidence="not_applicable", material=False))
    if len(disclosures) > 2:
        claims.append(_claim("C003", "data_disclosure", disclosures[2], confidence="not_applicable", material=False))
    if len(disclosures) > 3:
        claims.append(_claim("C004", "data_disclosure", disclosures[3], confidence="not_applicable", material=False))
    claims.append(_claim("C005", "data_disclosure", f"正式民调截止至{poll_cutoff}", confidence="not_applicable", material=False))
    if poll_gap:
        claims.append(_claim("C006", "data_disclosure", "本期没有新增正式民调", confidence="not_applicable", material=False))

    if pe0.get("event_id"):
        claims.append(
            _claim(
                "C007",
                "factual_synthesis",
                f"本期正式事件：{pe0.get('title')}（{pe0.get('event_date')}）。",
                event_ids=[pe0["event_id"]],
                source_ids=event_source_ids(pe0),
                confidence="high",
                material=True,
            )
        )

    event_ids_for_assessment = [e["event_id"] for e in (period_events + background_events) if e.get("event_id")][:2]
    assessment_sources = sorted(
        {
            sid
            for event in (period_events + background_events)
            if event.get("event_id") in event_ids_for_assessment
            for sid in (event.get("source_ids") or [])
        }
    )
    claims.append(
        _claim(
            "C008",
            "current_assessment",
            "综合本期正式事件与当前快照，选情格局仍以民进党结构优势为主，但存在未收口变量。",
            event_ids=event_ids_for_assessment,
            source_ids=assessment_sources,
            snapshot_dimensions=["overall_race_structure"],
            inference_basis="本期正式事件与当前快照维度共同支持限定性研判",
            confidence="medium",
            material=True,
        )
    )
    claims.append(
        _claim(
            "C009",
            "comparative_assessment",
            "与上一快照相比，蓝白合作由提案阶段进入选区实质协调，但全市制度化尚未完成。",
            event_ids=event_ids_for_assessment,
            source_ids=assessment_sources,
            snapshot_dimensions=["kmt_tpp_cooperation"],
            inference_basis="本期正式事件与前后快照差异共同支持比较判断",
            confidence="medium",
            material=True,
        )
    )
    claims.append(
        _claim(
            "C010",
            "forward_outlook",
            "基于两项正式动作，预计未来半月仍可能以组织协调为主。",
            event_ids=event_ids_for_assessment,
            source_ids=assessment_sources,
            inference_basis="快照显示蓝白合作已进入选区实质协调阶段",
            confidence="medium",
            material=True,
        )
    )
    limitation_text = "民调空窗限制本期研判强度。"
    claims.append(
        _claim(
            "C011",
            "limitation",
            limitation_text,
            gap_ids=gap_ids[:2],
            confidence="medium",
            material=False,
        )
    )

    sections = [
        {"section_id": "S01", "heading": "一、总体判断", "claim_ids": ["C008"], "section_purpose": "overall_judgment"},
        {"section_id": "S02", "heading": "二、本期关键变化", "claim_ids": ["C007", "C010"], "section_purpose": "key_changes"},
        {"section_id": "S03", "heading": "三、陈亭妃整合进展", "claim_ids": ["C008"], "section_purpose": "chen_integration"},
        {"section_id": "S04", "heading": "四、谢龙介组织及竞选动作", "claim_ids": ["C007"], "section_purpose": "hsieh_organization"},
        {"section_id": "S05", "heading": "五、蓝白合作变化", "claim_ids": ["C009"], "section_purpose": "kmt_tpp"},
        {"section_id": "S06", "heading": "六、民调与治理议题", "claim_ids": ["C005", "C006", "C011"], "section_purpose": "polls_governance"},
        {"section_id": "S07", "heading": "七、未来半月走势", "claim_ids": ["C010"], "section_purpose": "forward_outlook"},
        {"section_id": "S08", "heading": "八、证据限制", "claim_ids": ["C001", "C002", "C003", "C004", "C011"], "section_purpose": "evidence_limitations"},
    ]
    report = {
        "schema_version": "1.1",
        "report_id": "mock-report",
        "election_id": payload.get("election_id", ""),
        "report_period": payload.get("report_period") or {},
        "data_context": build_data_context(payload),
        "generation_mode": mode,
        "report_status": "generated",
        "title": "台南蓝白合作进入实质协调但全市制度化未完成",
        "title_claim_ids": ["C009"],
        "overall_judgment_claim_ids": ["C008"],
        "sections": sections,
        "claims": claims,
        "required_disclosures": ["C001", "C002", "C003", "C004", "C005", "C006"],
        "do_not_infer_compliance": [
            {"rule_id": f"dni_{i}", "rule_text": text, "violated": False, "related_claim_ids": []}
            for i, text in enumerate(payload.get("do_not_infer") or [], 1)
        ],
        "report_statistics": {
            "claim_count": len(claims),
            "section_count": len(sections),
            "event_reference_count": sum(len(c.get("supporting_event_ids") or []) for c in claims),
            "poll_reference_count": sum(len(c.get("supporting_poll_ids") or []) for c in claims),
            "source_reference_count": sum(len(c.get("supporting_source_ids") or []) for c in claims),
            "gap_reference_count": sum(len(c.get("supporting_gap_ids") or []) for c in claims),
            "chinese_char_count": 0,
            "length_below_target": False,
        },
    }
    return report


def _build_valid_v2_report(payload: dict) -> dict:
    """v2.0 研判单元契约 mock 报告（观点/判断优先结构，确定性、可过校验）。"""
    eligibility = payload.get("generation_eligibility") or {}
    mode = eligibility.get("allowed_generation_mode", "draft_with_data_gap")
    period_events = payload.get("period_events") or []
    background_events = payload.get("background_events") or []
    polls = payload.get("polls") or []
    data_status = payload.get("data_status") or {}
    rp = payload.get("report_period") or {}
    period_start = str(rp.get("period_start") or "")[:10]
    period_end = str(rp.get("period_end") or "")[:10]
    poll_cutoff = str(data_status.get("poll_cutoff") or "")
    dims = [
        d.get("dimension")
        for d in (payload.get("state_diff", {}).get("dimensions") or [])
        if d.get("dimension")
    ]

    def in_period_events():
        return [
            e
            for e in period_events
            if e.get("event_date")
            and period_start
            and period_start <= str(e["event_date"])[:10] <= period_end
        ]

    pool = in_period_events() or period_events
    ev1 = pool[0] if pool else {}
    ev2 = pool[1] if len(pool) > 1 else ev1
    bg = background_events[0] if background_events else ev1
    poll = polls[-1] if polls else None

    def ev_item(ev: dict) -> dict:
        return {
            "evidence_id": ev.get("event_id") or "",
            "evidence_date": str(ev.get("event_date") or "")[:10],
            "evidence_summary": str(ev.get("title") or "正式事件"),
        }

    def poll_item(p: dict) -> dict:
        date_text = str(
            p.get("release_date") or p.get("fieldwork_end") or ""
        )[:10]
        return {
            "evidence_id": p.get("poll_id") or "",
            "evidence_date": date_text,
            "evidence_summary": f"{p.get('pollster') or '正式'}民调记录",
        }

    ev_sources = sorted(
        {
            sid
            for e in (ev1, ev2)
            for sid in (e.get("source_ids") or [])
        }
    )
    poll_sources = sorted(set((poll or {}).get("source_ids") or []))
    all_sources = sorted(set(ev_sources) | set(poll_sources))

    disclosures = list(eligibility.get("required_disclosures") or [])
    if poll_cutoff:
        disclosures.append(f"正式民调截止至 {poll_cutoff}")
    disclosures.append("本期没有新增正式民调")
    seen: set[str] = set()
    unique_disclosures: list[str] = []
    for text in disclosures:
        if text not in seen:
            seen.add(text)
            unique_disclosures.append(text)

    assessments: list[dict] = []
    if ev1.get("event_id") or ev2.get("event_id"):
        items = [item for item in (ev_item(ev1), ev_item(ev2)) if item["evidence_id"]]
        if poll and len(items) < 2:
            items.append(poll_item(poll))
        assessments.append(
            {
                "assessment_id": "CA1",
                "judgment": "蓝白合作本期进入选区实质协调阶段，但离全市制度化仍有关键距离。",
                "evidence_items": items[:4],
                "evidence_refs": {
                    "event_ids": [ev1["event_id"], ev2["event_id"]]
                    if ev1.get("event_id") and ev2.get("event_id")
                    else [ev1.get("event_id") or ev2.get("event_id")],
                    "poll_ids": [poll["poll_id"]] if poll else [],
                    "source_ids": all_sources,
                    "dimension_ids": dims[:1],
                },
                "reasoning": "两项正式事件均发生在本期且方向一致，说明相关动作进入操作层面；但证据只显示事件层面的动作，未出现制度化的正式安排，因此判断停留在实质推进而未完成制度化。",
                "falsifiers_or_limits": (
                    f"若下期出现双方正式签署全市合作文件，或新的正式民调出现相反方向的显著变化，"
                    f"本判断将被削弱；正式民调截止于{poll_cutoff or '报告期前'}，不能代表当前实时支持率。"
                ),
                "confidence": "medium",
                "watch_indicators": ["是否出现全市性联合活动", "下一期是否有新的正式民调"],
            }
        )
    if poll and bg.get("event_id"):
        assessments.append(
            {
                "assessment_id": "CA2",
                "judgment": "民进党阵营优势仍建立在既有选民结构与初选结果之上，本期组织动作尚不足以改变基本盘。",
                "evidence_items": [
                    poll_item(poll),
                    ev_item(bg),
                ][:4],
                "evidence_refs": {
                    "event_ids": [bg["event_id"]],
                    "poll_ids": [poll["poll_id"]],
                    "source_ids": poll_sources
                    + [sid for sid in (bg.get("source_ids") or [])],
                    "dimension_ids": [d for d in dims if d == "overall_race_structure"]
                    or dims[:1],
                },
                "reasoning": (
                    f"正式民调记录止于{poll_cutoff or '报告期前'}，只能说明既有优势基础，"
                    "不能代表当前实时格局；初选结果进一步确认提名结构，两者共同支持优势结构判断，"
                    "但不能推出整合完成或全面领先的强结论。"
                ),
                "falsifiers_or_limits": "若民进党官方宣布全面整合，或新的正式民调显示格局显著变化，本判断将被推翻。",
                "confidence": "medium",
                "watch_indicators": ["民进党是否公布整合声明", "下一期正式民调结果"],
            }
        )

    appendix: list[dict] = []
    gap_ids = [
        g.get("gap_id") or g.get("stable_gap_id")
        for g in (payload.get("coverage_gaps") or [])
        if g.get("gap_id") or g.get("stable_gap_id")
    ]
    if gap_ids:
        appendix.append(
            {
                "item_id": "AP1",
                "item_type": "data_limitation",
                "item_text": (
                    f"民调空窗限制本期研判强度；正式民调截止至{poll_cutoff or '报告期前'}。"
                ),
                "evidence_refs": {
                    "poll_ids": [poll["poll_id"]] if poll else [],
                    "source_ids": poll_sources,
                    "gap_ids": gap_ids[:1],
                },
            }
        )
    if bg.get("event_id"):
        appendix.append(
            {
                "item_id": "AP2",
                "item_type": "background_fact",
                "item_text": f"背景事实：{bg.get('title')}（{str(bg.get('event_date') or '')[:10]}）。",
                "evidence_refs": {
                    "event_ids": [bg["event_id"]],
                    "source_ids": list((bg.get("source_ids") or [])[:1]),
                },
            }
        )

    conclusion: list[dict] = []
    if assessments:
        first = assessments[0]
        conclusion.append(
            {
                "summary_id": "CS1",
                "judgment": "本期蓝白合作进入选区实质协调阶段，但全市制度化尚未完成，选情结构仍以民进党优势为主。",
                "confidence": "medium",
                "evidence_refs": {
                    "event_ids": list(first["evidence_refs"].get("event_ids") or []),
                    "poll_ids": list(first["evidence_refs"].get("poll_ids") or []),
                    "source_ids": list(first["evidence_refs"].get("source_ids") or []),
                    "dimension_ids": dims[:1],
                },
            }
        )

    return {
        "schema_version": "2.0",
        "report_id": "mock-v2-report",
        "election_id": payload.get("election_id", ""),
        "report_period": deepcopy(rp),
        "data_context": build_data_context(payload),
        "generation_mode": mode,
        "report_status": "generated",
        "title": "蓝白合作进入实质协调但全市制度化未完成",
        "conclusion_summary": conclusion,
        "core_assessments": assessments,
        "appendix": appendix,
        "required_disclosures": unique_disclosures,
        "do_not_infer_compliance": [
            {"rule_id": f"dni_{i}", "rule_text": text, "violated": False, "related_claim_ids": []}
            for i, text in enumerate(payload.get("do_not_infer") or [], 1)
        ],
        "report_statistics": {
            "claim_count": 0,
            "section_count": 3,
            "core_assessment_count": len(assessments),
            "conclusion_summary_count": len(conclusion),
            "evidence_item_count": sum(
                len(a.get("evidence_items") or []) for a in assessments
            ),
            "chinese_char_count": 0,
            "length_below_target": True,
        },
    }


def _claim(
    claim_id: str,
    claim_type: str,
    text: str,
    *,
    event_ids: list[str] | None = None,
    poll_ids: list[str] | None = None,
    source_ids: list[str] | None = None,
    snapshot_dimensions: list[str] | None = None,
    gap_ids: list[str] | None = None,
    inference_basis: str = "",
    confidence: str = "high",
    material: bool = True,
) -> dict:
    return {
        "claim_id": claim_id,
        "claim_type": claim_type,
        "claim_text": text,
        "confidence": confidence,
        "material_for_report": material,
        "supporting_event_ids": event_ids or [],
        "supporting_poll_ids": poll_ids or [],
        "supporting_source_ids": source_ids or [],
        "supporting_snapshot_dimensions": snapshot_dimensions or [],
        "supporting_gap_ids": gap_ids or [],
        "inference_basis": inference_basis,
        "limitations": [],
        "applies_to_period": True,
    }


def _apply_invalid_mutation(report: dict, fixture: str) -> dict:
    claims = report["claims"]
    if fixture == "invalid_unknown_event":
        claims[6]["supporting_event_ids"] = ["evt_unknown_999"]
    elif fixture == "invalid_unknown_poll":
        claims[4]["supporting_poll_ids"] = ["poll_unknown_999"]
    elif fixture in ("invalid_unknown_source", "repairable_invalid"):
        claims[6]["supporting_source_ids"] = ["src_unknown_999"]
    elif fixture == "invalid_missing_evidence":
        claims[6]["supporting_event_ids"] = []
    elif fixture == "invalid_numeric_claim":
        claims[6]["claim_text"] += " 支持率约73.5%。"
    elif fixture == "invalid_date_claim":
        claims[6]["claim_text"] += " 该变化发生于2026-08-15。"
    elif fixture == "invalid_do_not_infer":
        claims.append(
            _claim(
                "C099",
                "factual_synthesis",
                "陈亭妃已完成全面整合。",
                event_ids=(claims[6].get("supporting_event_ids") or ["evt_x"])[:1],
            )
        )
    elif fixture == "invalid_missing_disclosure":
        report["required_disclosures"] = [c for c in report["required_disclosures"] if c != "C003"]
        claims = [c for c in claims if c["claim_id"] != "C003"]
    elif fixture == "invalid_generation_mode":
        report["generation_mode"] = "final"
    elif fixture == "invalid_schema":
        report["extra_top_level_field"] = True
    elif fixture == "unrepairable_invalid":
        claims[6]["supporting_event_ids"] = ["evt_unknown_999"]
    report["claims"] = claims
    return report
