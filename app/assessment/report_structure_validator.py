"""v2.0 研判单元内容结构校验 + 确定性派生 Claims/Sections。

本模块是“观点/判断优先”契约的结构化校验器（不调用大模型）：
- 结论摘要 1-3 条可证伪判断，禁止“值得关注/有待观察”零信息套话；
- 核心研判 1-3 个，每个单元固定顺序：判断 -> 证据(2-4条带日期) -> 推理链 ->
  反证/限制 -> 置信度 -> 观察指标；
- 同一 evidence_id 不得在多个研判单元重复堆叠；
- 证据必须引用证据包内 ID，旧民调必须带日期且不得写成当前支持率；
- 只有事实没有推理链 / 推理链只是复述判断 / 判断只是复述证据 -> 拒绝。

同时提供 derive_claims_and_sections()：把 v2 结构确定性地派生为
claim 级（supporting_*_ids）与 section 级结构，供既有 Claim—Evidence
校验器与渲染器复用（旧 run 读取路径不受影响）。
"""

from __future__ import annotations

import re
from typing import Any

from .llm_input_contract import build_data_context

CONFIDENCE_V2 = {"high", "medium", "low"}
APPENDIX_TYPES = {"background_fact", "data_limitation", "disclosure"}
EVIDENCE_KIND = ("event", "poll")

# 零信息套话中的空词：仅由这些词（+标点）构成的判断不可证伪。
HEDGE_WORDS = (
    "预计", "可能", "或将", "有望", "值得", "有待", "需要", "还需",
    "观察", "关注", "重视", "留意", "持续", "继续", "仍", "还", "尚",
    "待", "进一步",
)
# 前瞻判断词：用于派生 claim_type（current_assessment / forward_outlook）。
FORWARD_WORDS = ("预计", "可能", "或将", "有望", "值得观察", "待观察", "仍将", "仍需观察")
CURRENT_TERMS = ("当前", "目前", "最新", "实时")

MIN_REASONING_LEN = 8  # 规范化后最短推理链长度（防止“支持判断”式空转）
JUDGMENT_MIN_CORE_LEN = 6  # 去掉零信息空词后，判断必须剩余的最少实质内容
PARAPHRASE_OVERLAP_LIMIT = 0.85  # 推理链与判断/证据的大字重叠上限


def _norm(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", str(text or ""))


def _bigrams(text: str) -> set[str]:
    value = _norm(text)
    return {value[i : i + 2] for i in range(max(0, len(value) - 1))}


def _overlap(left: str, right: str) -> float:
    a, b = _bigrams(left), _bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _negated(text: str) -> bool:
    return any(
        term in text
        for term in (
            "不足以证明", "不代表", "不得", "尚未", "未完成", "无法", "不能",
            "并非", "不等于", "禁止", "不能代表", "尚不能", "不构成", "并不",
        )
    )


def is_zero_info_judgment(text: str) -> bool:
    """判断是否为零信息套话（'值得关注/有待观察'等）。"""
    norm = _norm(text)
    if not norm:
        return True
    stripped = norm
    for word in sorted(HEDGE_WORDS, key=len, reverse=True):
        stripped = stripped.replace(word, "")
    return len(stripped) < JUDGMENT_MIN_CORE_LEN


def _refs_flat(refs: dict) -> list[str]:
    return [
        item
        for key in ("event_ids", "poll_ids", "source_ids", "gap_ids", "dimension_ids")
        for item in (refs.get(key) or [])
        if item
    ]


def _refs_to_supporting(refs: dict) -> dict:
    return {
        "supporting_event_ids": list(refs.get("event_ids") or []),
        "supporting_poll_ids": list(refs.get("poll_ids") or []),
        "supporting_source_ids": list(refs.get("source_ids") or []),
        "supporting_snapshot_dimensions": list(refs.get("dimension_ids") or []),
        "supporting_gap_ids": list(refs.get("gap_ids") or []),
    }


def _event_date(ev: dict, ctx: Any) -> str:
    for key in ("event_date",):
        if ev.get(key):
            return str(ev[key])[:10]
    for sub in ev.get("subevents") or []:
        if sub.get("subevent_date"):
            return str(sub["subevent_date"])[:10]
    return ""


def _poll_dates(poll: dict) -> set[str]:
    return {
        str(poll[key])[:10]
        for key in ("release_date", "fieldwork_start", "fieldwork_end")
        if poll.get(key)
    }


def forward_claim_type(text: str) -> str:
    return "forward_outlook" if any(word in text for word in FORWARD_WORDS) else "current_assessment"


def derive_claims_and_sections(report: dict, ctx: Any) -> tuple[list[dict], list[dict]]:
    """把 v2 结构确定性派生为 claim 列表与 3 个渲染 section（不依赖模型）。"""
    claims: list[dict] = []

    for index, item in enumerate(report.get("conclusion_summary") or [], 1):
        refs = item.get("evidence_refs") or {}
        claims.append(
            {
                "claim_id": f"CS{index}",
                "claim_type": forward_claim_type(item.get("judgment") or ""),
                "claim_text": item.get("judgment") or "",
                "confidence": item.get("confidence") or "medium",
                "material_for_report": True,
                **_refs_to_supporting(refs),
                "inference_basis": "结论摘要条目（由核心研判支持）",
                "limitations": [],
                "applies_to_period": True,
                "_derived_kind": "conclusion",
            }
        )

    assessments = report.get("core_assessments") or []
    for index, assessment in enumerate(assessments, 1):
        aid = assessment.get("assessment_id") or f"CA{index}"
        refs = assessment.get("evidence_refs") or {}
        claims.append(
            {
                "claim_id": aid,
                "claim_type": forward_claim_type(assessment.get("judgment") or ""),
                "claim_text": assessment.get("judgment") or "",
                "confidence": assessment.get("confidence") or "medium",
                "material_for_report": True,
                **_refs_to_supporting(refs),
                "inference_basis": assessment.get("reasoning") or "",
                "limitations": [assessment.get("falsifiers_or_limits") or ""]
                if assessment.get("falsifiers_or_limits")
                else [],
                "applies_to_period": True,
                "_derived_kind": "assessment",
            }
        )
        for j, ev in enumerate(assessment.get("evidence_items") or [], 1):
            claims.append(_evidence_claim(f"EV_{aid}_{j}", ev, ctx))

    for index, item in enumerate(report.get("appendix") or [], 1):
        claims.append(_appendix_claim(f"AP{index}", item, ctx))

    for index, text in enumerate(report.get("required_disclosures") or [], 1):
        claims.append(
            {
                "claim_id": f"RD{index}",
                "claim_type": "data_disclosure",
                "claim_text": str(text),
                "confidence": "not_applicable",
                "material_for_report": False,
                "supporting_event_ids": [],
                "supporting_poll_ids": [],
                "supporting_source_ids": [],
                "supporting_snapshot_dimensions": [],
                "supporting_gap_ids": [],
                "inference_basis": "权威 required_disclosures 披露",
                "limitations": [str(text)],
                "applies_to_period": True,
                "_derived_kind": "disclosure_text",
            }
        )

    conclusion_ids = [c["claim_id"] for c in claims if c["_derived_kind"] == "conclusion"]
    assessment_ids = [
        c["claim_id"] for c in claims if c["_derived_kind"] in ("assessment", "evidence")
    ]
    appendix_ids = [
        c["claim_id"]
        for c in claims
        if c["_derived_kind"] in ("appendix", "disclosure_text")
    ]
    sections = [
        {
            "section_id": "S01",
            "heading": "一、结论摘要",
            "claim_ids": conclusion_ids,
            "section_purpose": "conclusion_summary",
        },
        {
            "section_id": "S02",
            "heading": "二、核心研判",
            "claim_ids": assessment_ids,
            "section_purpose": "core_assessments",
        },
        {
            "section_id": "S03",
            "heading": "三、数据限制与事实附录",
            "claim_ids": appendix_ids,
            "section_purpose": "appendix",
        },
    ]
    return claims, sections


def _evidence_claim(cid: str, ev: dict, ctx: Any) -> dict:
    eid = str(ev.get("evidence_id") or "")
    date_text = str(ev.get("evidence_date") or "")[:10]
    summary = ev.get("evidence_summary") or ""
    text = f"{summary}（{date_text}）" if date_text else summary
    if eid in ctx.event_ids:
        supporting = {
            "supporting_event_ids": [eid],
            "supporting_poll_ids": [],
            "supporting_source_ids": sorted(set(ctx.events[eid].get("source_ids") or [])),
        }
        ctype = "factual_synthesis"
    elif eid in ctx.poll_ids:
        supporting = {
            "supporting_event_ids": [],
            "supporting_poll_ids": [eid],
            "supporting_source_ids": sorted(set(ctx.polls[eid].get("source_ids") or [])),
        }
        ctype = "factual_synthesis"
    else:
        # 未知 ID 由结构校验先行拒绝；此处保持可调用。
        supporting = {"supporting_event_ids": [], "supporting_poll_ids": [], "supporting_source_ids": []}
        ctype = "limitation"
    return {
        "claim_id": cid,
        "claim_type": ctype,
        "claim_text": text,
        "confidence": "high",
        "material_for_report": True,
        **supporting,
        "supporting_snapshot_dimensions": [],
        "supporting_gap_ids": [],
        "inference_basis": "证据条目（事实佐证）",
        "limitations": [],
        "applies_to_period": True,
        "_derived_kind": "evidence",
    }


def _appendix_claim(cid: str, item: dict, ctx: Any) -> dict:
    item_type = item.get("item_type") or "background_fact"
    refs = item.get("evidence_refs") or {}
    text = item.get("item_text") or ""
    supporting = _refs_to_supporting(refs)
    if item_type == "disclosure":
        ctype = "data_disclosure"
        confidence = "not_applicable"
    elif item_type == "data_limitation":
        ctype = "limitation"
        confidence = "not_applicable"
    elif supporting["supporting_event_ids"] or supporting["supporting_poll_ids"]:
        ctype = "factual_synthesis"
        confidence = "high"
    else:
        ctype = "limitation"
        confidence = "not_applicable"
    return {
        "claim_id": cid,
        "claim_type": ctype,
        "claim_text": text,
        "confidence": confidence,
        "material_for_report": False,
        **supporting,
        "inference_basis": "附录条目",
        "limitations": [text],
        "applies_to_period": False,
        "_derived_kind": "appendix",
    }


# ---------------------------------------------------------------------------
# 内容结构校验
# ---------------------------------------------------------------------------


def validate_report_structure_v2(
    report: dict, ctx: Any, *, allow_evidence_overlap: bool = False
) -> dict:
    """返回结构化校验结果：errors 为硬失败，warnings 供人工复核。

    ``allow_evidence_overlap``：两阶段装配产物允许跨单元复用同一证据
    （历史八栏目 plan 天然如此）；单阶段模型生成路径必须禁止。
    """
    errors: list[str] = []
    warnings: list[str] = []
    period = ctx.contract.get("report_period") or {}
    period_start = str(period.get("period_start") or "")[:10]
    period_end = str(period.get("period_end") or "")[:10]
    facts_cutoff = str((ctx.contract.get("data_status") or {}).get("facts_cutoff") or "")[:10]

    conclusion = report.get("conclusion_summary") or []
    if not conclusion:
        errors.append("conclusion_summary 为空：报告必须以结论摘要开头，禁止从新闻事实清单开始")
    for index, item in enumerate(conclusion, 1):
        where = f"conclusion_summary[{index}]"
        judgment = str(item.get("judgment") or "")
        if is_zero_info_judgment(judgment):
            errors.append(f"{where}.judgment 是不可证伪的零信息判断（值得关注/有待观察类套话）")
        refs_errors = _refs_exist_errors(item.get("evidence_refs") or {}, ctx, where)
        errors.extend(refs_errors)
        if not _refs_flat(item.get("evidence_refs") or {}):
            errors.append(f"{where}.evidence_refs 必须至少引用一项证据")
        if judgment and any(
            _overlap(judgment, str(other.get("judgment") or "")) >= PARAPHRASE_OVERLAP_LIMIT
            for other in conclusion
            if other is not item
        ):
            errors.append(f"{where}.judgment 与另一条结论摘要重复（同义反复）")

    assessments = report.get("core_assessments") or []
    if not assessments:
        errors.append("core_assessments 为空：至少需要一个核心研判单元")
    if len(assessments) > 3:
        errors.append(f"core_assessments 超过 3 个核心研判（实际 {len(assessments)}），宁可少而讲透")

    seen_evidence: dict[str, str] = {}
    repeated_evidence_ids: list[str] = []
    for index, assessment in enumerate(assessments, 1):
        where = f"core_assessments[{index}]"
        judgment = str(assessment.get("judgment") or "")
        reasoning = str(assessment.get("reasoning") or "")
        falsifiers = str(assessment.get("falsifiers_or_limits") or "")
        watch = assessment.get("watch_indicators") or []
        items = assessment.get("evidence_items") or []

        if is_zero_info_judgment(judgment):
            errors.append(f"{where}.judgment 是不可证伪的零信息判断（值得关注/有待观察类套话）")
        if not reasoning:
            errors.append(f"{where}.reasoning 为空：禁止只有事实没有推理链")
        elif len(_norm(reasoning)) < MIN_REASONING_LEN:
            errors.append(f"{where}.reasoning 过短，未形成有效推理链")
        elif judgment and _overlap(reasoning, judgment) >= PARAPHRASE_OVERLAP_LIMIT:
            errors.append(f"{where}.reasoning 只是复述判断（同义反复），没有解释事实为何支持判断")
        if not falsifiers:
            errors.append(f"{where}.falsifiers_or_limits 为空：必须说明什么新事实会推翻或削弱判断")
        if assessment.get("confidence") not in CONFIDENCE_V2:
            errors.append(f"{where}.confidence 非法: {assessment.get('confidence')!r}")
        if not watch or not all(str(item).strip() for item in watch):
            errors.append(f"{where}.watch_indicators 必须给出下一期可观察指标")
        errors.extend(_refs_exist_errors(assessment.get("evidence_refs") or {}, ctx, where))
        if not _refs_flat(assessment.get("evidence_refs") or {}):
            errors.append(f"{where}.evidence_refs 必须至少引用一项证据")

        if not items:
            errors.append(f"{where}.evidence_items 为空：每个研判必须绑定 2-4 条最近事实证据")
        for j, ev in enumerate(items, 1):
            eid = str(ev.get("evidence_id") or "")
            date_text = str(ev.get("evidence_date") or "")[:10]
            summary = str(ev.get("evidence_summary") or "")
            if not eid:
                errors.append(f"{where}.evidence_items[{j}].evidence_id 为空")
                continue
            if eid in seen_evidence and seen_evidence[eid] != where:
                if not allow_evidence_overlap:
                    errors.append(
                        f"{where}.evidence_items[{j}]: evidence_id {eid} 已在 "
                        f"{seen_evidence[eid]} 使用（同一事实在多节重复堆叠）"
                    )
                if eid not in repeated_evidence_ids:
                    repeated_evidence_ids.append(eid)
            seen_evidence[eid] = where
            if eid in ctx.event_ids:
                ev_obj = ctx.events[eid]
                allowed_dates = {_event_date(ev_obj, ctx)}
                kind = "event"
            elif eid in ctx.poll_ids:
                ev_obj = ctx.polls[eid]
                allowed_dates = _poll_dates(ev_obj)
                kind = "poll"
            else:
                errors.append(f"{where}.evidence_items[{j}]: evidence_id {eid} 不在证据包内")
                continue
            if not date_text:
                errors.append(f"{where}.evidence_items[{j}]: evidence_date 为空，每条事实必须带日期")
            elif date_text not in allowed_dates:
                errors.append(
                    f"{where}.evidence_items[{j}]: evidence_date {date_text} 与证据包记录不一致"
                )
            if not summary:
                errors.append(f"{where}.evidence_items[{j}].evidence_summary 为空")
            elif judgment and _overlap(summary, judgment) >= PARAPHRASE_OVERLAP_LIMIT:
                errors.append(
                    f"{where}.evidence_items[{j}]: 事实摘要与判断措辞重复，事实与判断必须区分"
                )
            if kind == "poll":
                fieldwork_end = str(ev_obj.get("fieldwork_end") or "")[:10]
                if fieldwork_end and period_start and fieldwork_end < period_start:
                    # 旧民调：日期已由 evidence_date 显式披露；不得写成当前实时数据。
                    combined = judgment + reasoning + summary
                    if any(term in combined for term in CURRENT_TERMS) and not _negated(combined):
                        errors.append(
                            f"{where}.evidence_items[{j}]: 旧民调 {eid} 被写成当前/最新实时数据"
                        )
                    if "民调" not in falsifiers and "民调" not in reasoning:
                        warnings.append(
                            f"{where}: 旧民调 {eid} 局限说明建议写入 falsifiers_or_limits"
                        )
                if date_text and facts_cutoff and date_text > facts_cutoff:
                    errors.append(
                        f"{where}.evidence_items[{j}]: 民调日期 {date_text} 晚于事实截止日 {facts_cutoff}"
                    )
            else:
                # 报告期内事件优先：背景事件若写成“本期”将由 claim 级校验拦截。
                if date_text and period_start and period_end and not (
                    period_start <= date_text <= period_end
                ) and "本期" in summary:
                    warnings.append(
                        f"{where}.evidence_items[{j}]: 报告期外事件 {eid} 被描述为本期事实，请复核"
                    )

    appendix = report.get("appendix") or []
    for index, item in enumerate(appendix, 1):
        where = f"appendix[{index}]"
        if item.get("item_type") not in APPENDIX_TYPES:
            errors.append(f"{where}.item_type 非法: {item.get('item_type')!r}")
        if not str(item.get("item_text") or "").strip():
            errors.append(f"{where}.item_text 为空")
        errors.extend(_refs_exist_errors(item.get("evidence_refs") or {}, ctx, where))

    disclosures = report.get("required_disclosures") or []
    if not disclosures:
        errors.append("required_disclosures 为空：必须披露事实/民调截止日与数据缺口")
    for index, text in enumerate(disclosures, 1):
        if not str(text).strip():
            errors.append(f"required_disclosures[{index}] 为空字符串")

    return {
        "errors": errors,
        "warnings": warnings,
        "conclusion_summary_count": len(conclusion),
        "core_assessment_count": len(assessments),
        "evidence_item_count": sum(len(a.get("evidence_items") or []) for a in assessments),
        "appendix_item_count": len(appendix),
        "repeated_evidence_across_assessments": repeated_evidence_ids,
    }


def _refs_exist_errors(refs: dict, ctx: Any, where: str) -> list[str]:
    errors: list[str] = []
    for eid in refs.get("event_ids") or []:
        if eid not in ctx.event_ids:
            errors.append(f"{where}.evidence_refs.event_ids: 未知 event_id {eid}")
    for pid in refs.get("poll_ids") or []:
        if pid not in ctx.poll_ids:
            errors.append(f"{where}.evidence_refs.poll_ids: 未知 poll_id {pid}")
    for sid in refs.get("source_ids") or []:
        if sid not in ctx.source_ids:
            errors.append(f"{where}.evidence_refs.source_ids: 未知 source_id {sid}")
    for gid in refs.get("gap_ids") or []:
        if gid not in ctx.gap_ids:
            errors.append(f"{where}.evidence_refs.gap_ids: 未知 gap_id {gid}")
    for dim in refs.get("dimension_ids") or []:
        if dim not in ctx.dimension_names:
            errors.append(f"{where}.evidence_refs.dimension_ids: 未知维度 {dim}")
    return errors
