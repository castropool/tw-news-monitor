"""Claim—Evidence 确定性校验器（不调用大模型）。

v1.1 报告走历史校验路径（旧 run 兼容）；v2.0 报告先做“研判单元”内容结构
校验，再把结构确定性派生为 claim 级结构复用同一套 Claim—Evidence 门禁。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any

from .report_output_schema import validate_report_schema, validate_report_schema_v2
from .report_structure_validator import (
    derive_claims_and_sections,
    validate_report_structure_v2,
)
from .llm_input_contract import build_data_context
from .claim_evidence_semantics import validate_claim_semantics


SURNAMES = set("陈谢林王黄蔡赖柯李苏卢蒋侯韩卓郑何邱郭张吴许罗叶廖沈曾魏江周徐杨朱胡萧游潘马赵孙")
STOP_CHARS = set("的是在与和或于为将对从向等已仍就也但并及被由把让使能会可要有无不这那其之以候前后内外中上下点条位年月份日天时个名号过期")
ORG_SUFFIXES = ("党", "部", "会", "总部", "委员会", "基金会", "公司", "署", "局", "院", "团", "社", "银行", "中心", "协会", "联盟", "政府", "议会", "法院", "机关", "议会党团", "竞选总部")
NON_PERSON_TAILS = {
    "同框", "阵营", "看板", "系统", "支持", "周期", "团队", "组织", "动态", "新事",
    "表示", "指出", "强调", "声称", "宣称", "重申", "呼吁", "主张", "认为", "透露", "回应", "解释", "坦言",
}
ORG_ACTIONS = ("宣布", "表示", "提名", "发布", "成立", "启动", "加入", "介入", "主导", "参与", "支持", "通过", "召开", "回应")
ORG_NOISE_TERMS = (
    "预计", "将", "持续", "仍", "可能", "成为", "巩固", "攻击", "借", "推动",
    "进行", "打造", "称", "强调", "呼吁", "要求", "主张", "认为", "指出",
    "宣布", "成立", "启动", "主导", "参与", "通过", "召开", "回应", "提出", "及",
    "未出现", "没有出现", "尚无", "未见",
)
ORG_LEADING_QUALIFIERS = ("本期", "同期", "本届")
# 已确认的无歧义组织简称/变体规范化（仅限 Evidence Pack 内已证明案例，不建立大型别名库）。
ORG_NAME_NORMALIZATIONS = {
    "陈亭妃观光产业后援会": "大台南观光产业后援会",
}
NEGATION_PHRASES = (
    "不足以证明", "不等于", "不得", "尚未", "未完成", "不代表", "不构成",
    "未获验证", "无法证明", "没有证据", "不能证明", "不应", "并非", "不等于已",
    "不包含", "不能视为", "不可视为", "不作为", "未达到", "无法确认", "无法用数据描绘", "无法",
    "不能代表", "尚不能", "禁止", "并非实时",
)
FORWARD_WORDS = ("预计", "可能", "值得观察", "有望", "或将", "待观察")
PROBABILITY_TERMS = ("胜选概率", "当选概率", "胜率", "赢面概率")


DNI_RULES = [
    {"rule_id": "dni_chen_full_integration", "rule_text": "陈亭妃 + 全面完成整合", "terms": [["陈亭妃"], ["全面完成整合", "完全整合", "已全面整合"]]},
    {"rule_id": "dni_hsieh_full_machine", "rule_text": "谢龙介 + 全市成熟竞选机器", "terms": [["谢龙介"], ["全市成熟竞选机器", "成熟全市竞选机器", "成熟竞选机器"]]},
    {"rule_id": "dni_tainan_bluewhite_full", "rule_text": "台南蓝白 + 全面整合", "terms": [["台南蓝白", "蓝白"], ["全面整合", "已完成整合"]]},
    {"rule_id": "dni_national_agreement_seats", "rule_text": "全国协议 + 台南全市席次分配完成", "terms": [["全国协议", "中央协议"], ["全市席次分配", "席次分配"], ["完成", "已定"]]},
    {"rule_id": "dni_district_extrapolation", "rule_text": "第一选区合作 + 全市复制", "terms": [["第一选区"], ["全市复制", "全市推广", "全市一致"]]},
    {"rule_id": "dni_tpp_support", "rule_text": "民众党正式支持谢龙介（无正式公告）", "terms": [["民众党"], ["正式支持谢龙介", "已正式支持"]]},
    {"rule_id": "dni_new_poll_after_0312", "rule_text": "2026-03-12之后 + 最新民调支持率", "terms": [["2026-03-12之后", "3月12日之后", "4月", "5月", "6月", "7月"], ["最新民调", "最新支持率", "当前支持率"]]},
    {"rule_id": "dni_resources_shared", "rule_text": "志工/数据库/募款/财务 + 已共享", "terms": [["志工", "数据库", "募款", "财务"], ["已共享", "已经共享", "全面共享"]]},
    {"rule_id": "dni_governance_swing", "rule_text": "丹娜丝/三爷溪 + 已改变胜负格局", "terms": [["丹娜丝", "三爷溪"], ["改变胜负格局", "扭转选情", "改变格局"]]},
]


@dataclass
class EvidenceContext:
    contract: dict
    data_context: dict = field(default_factory=dict)
    event_ids: set[str] = field(default_factory=set)
    poll_ids: set[str] = field(default_factory=set)
    source_ids: set[str] = field(default_factory=set)
    gap_ids: set[str] = field(default_factory=set)
    dimension_names: set[str] = field(default_factory=set)
    event_source_pairs: set[tuple[str, str]] = field(default_factory=set)
    poll_source_pairs: set[tuple[str, str]] = field(default_factory=set)
    known_names: set[str] = field(default_factory=set)
    known_orgs: set[str] = field(default_factory=set)
    whitelist_names: set[str] = field(default_factory=set)
    events: dict[str, dict] = field(default_factory=dict)
    polls: dict[str, dict] = field(default_factory=dict)
    sources: dict[str, dict] = field(default_factory=dict)
    gaps: dict[str, dict] = field(default_factory=dict)
    gap_material: dict[str, bool] = field(default_factory=dict)
    do_not_infer_items: list[str] = field(default_factory=list)
    config: dict = field(default_factory=dict)
    evidence_corpus: str = ""


def build_evidence_context(contract: dict, evidence_pack: dict | None = None, config: dict | None = None) -> EvidenceContext:
    config = config or {}
    data_context = build_data_context(contract)
    events = {e["event_id"]: e for e in (contract.get("period_events") or []) + (contract.get("background_events") or []) if e.get("event_id")}
    polls = {p["poll_id"]: p for p in (contract.get("polls") or []) if p.get("poll_id")}
    sources = {s["source_id"]: s for s in (contract.get("sources") or []) if s.get("source_id")}
    poll_source_ids = {
        sid
        for p in polls.values()
        for sid in (p.get("source_ids") or [])
    }
    for sid in poll_source_ids - set(sources):
        sources[sid] = {"source_id": sid}
    gaps = {}
    for g in contract.get("coverage_gaps") or []:
        gid = g.get("gap_id") or g.get("stable_gap_id")
        if gid:
            gaps[gid] = g
    dims = {
        d.get("dimension")
        for d in (contract.get("state_diff", {}).get("dimensions") or [])
        if d.get("dimension")
    }
    event_source_pairs = {(eid, sid) for eid, ev in events.items() for sid in (ev.get("source_ids") or [])}
    poll_source_pairs = {(pid, sid) for pid, p in polls.items() for sid in (p.get("source_ids") or [])}

    known_names: set[str] = set()
    known_orgs: set[str] = set()
    for ev in events.values():
        for m in ev.get("mentions") or []:
            name = m.get("mention_name") if isinstance(m, dict) else None
            if name:
                known_names.add(name)
        known_orgs.update(ev.get("source_ids") or [])
    snapshot = contract.get("current_snapshot", {}).get("state") or {}
    for cand in (snapshot.get("candidate_status") or {}).values():
        if isinstance(cand, dict) and cand.get("name"):
            known_names.add(cand["name"])
            if cand.get("party"):
                known_orgs.add(cand["party"])
    for p in polls.values():
        if p.get("pollster"):
            known_orgs.add(p["pollster"])
        if p.get("sponsor"):
            known_orgs.add(p["sponsor"])
    for s in sources.values():
        if s.get("publisher"):
            known_orgs.add(s["publisher"])
    whitelist = set(config.get("report_generation", {}).get("entity_whitelist") or ["民进党", "国民党", "民众党", "绿营", "蓝营", "白营"])
    known_orgs.update(whitelist)

    gap_material: dict[str, bool] = {}
    if evidence_pack:
        for g in evidence_pack.get("gap_changes") or []:
            gid = g.get("stable_gap_id")
            if gid:
                gap_material[gid] = bool(g.get("material_for_report"))

    return EvidenceContext(
        contract=contract,
        data_context=data_context,
        event_ids=set(events),
        poll_ids=set(polls),
        source_ids=set(sources),
        gap_ids=set(gaps),
        dimension_names=set(dims),
        event_source_pairs=event_source_pairs,
        poll_source_pairs=poll_source_pairs,
        known_names=known_names,
        known_orgs=known_orgs,
        whitelist_names=whitelist,
        events=events,
        polls=polls,
        sources=sources,
        gaps=gaps,
        gap_material=gap_material,
        do_not_infer_items=list(contract.get("do_not_infer") or []),
        config=config,
        evidence_corpus=json.dumps(contract, ensure_ascii=False, default=str),
    )


def _normalize(text: str) -> str:
    return re.sub(r"\s+", "", text or "")


def _negated(text: str) -> bool:
    return any(p in text for p in NEGATION_PHRASES)


def _rule_hit(text: str, rule: dict) -> bool:
    for group in rule["terms"]:
        if not any(term in text for term in group):
            return False
    return True


def _extract_numbers(text: str) -> list[str]:
    return re.findall(r"\d[\d,]*(?:\.\d+)?%?", text)


def _extract_dates(text: str) -> list[str]:
    return re.findall(r"\d{4}-\d{2}-\d{2}", text)


def _claim_grounding_text(claim: dict, ctx: EvidenceContext) -> str:
    parts: list[str] = []
    for eid in claim.get("supporting_event_ids") or []:
        ev = ctx.events.get(eid) or {}
        parts.append(json.dumps(ev, ensure_ascii=False, default=str))
    for pid in claim.get("supporting_poll_ids") or []:
        p = ctx.polls.get(pid) or {}
        parts.append(json.dumps(p, ensure_ascii=False, default=str))
    for sid in claim.get("supporting_source_ids") or []:
        s = ctx.sources.get(sid) or {}
        parts.append(json.dumps(s, ensure_ascii=False, default=str))
    for gid in claim.get("supporting_gap_ids") or []:
        g = ctx.gaps.get(gid) or {}
        parts.append(json.dumps(g, ensure_ascii=False, default=str))
    data = ctx.contract.get("data_status") or {}
    rp = ctx.contract.get("report_period") or {}
    elig = ctx.contract.get("generation_eligibility") or {}
    parts.append(json.dumps({"data_status": data, "report_period": rp, "eligibility": elig}, ensure_ascii=False, default=str))
    return "\n".join(parts)


def _allowed_dates(claim: dict, ctx: EvidenceContext) -> set[str]:
    dates: set[str] = set()
    for eid in claim.get("supporting_event_ids") or []:
        ev = ctx.events.get(eid) or {}
        if ev.get("event_date"):
            dates.add(str(ev["event_date"])[:10])
        for sub in ev.get("subevents") or []:
            if sub.get("subevent_date"):
                dates.add(str(sub["subevent_date"])[:10])
    for pid in claim.get("supporting_poll_ids") or []:
        p = ctx.polls.get(pid) or {}
        for key in ("release_date", "fieldwork_start", "fieldwork_end"):
            if p.get(key):
                dates.add(str(p[key])[:10])
    rp = ctx.contract.get("report_period") or {}
    data = ctx.contract.get("data_status") or {}
    dates.update(
        str(x)[:10]
        for x in (
            rp.get("period_start"),
            rp.get("period_end"),
            rp.get("previous_period_start"),
            rp.get("previous_period_end"),
            data.get("facts_cutoff"),
            data.get("poll_cutoff"),
        )
        if x
    )
    dates.update(str(x)[:10] for x in (data.get("uncovered_date_range") or []))
    return dates


def _candidate_persons(text: str) -> list[str]:
    out: list[str] = []
    for i, ch in enumerate(text):
        if ch not in SURNAMES or i + 1 >= len(text):
            continue
        cand = None
        for length in (3,):
            if i + length <= len(text):
                candidate = text[i : i + length]
                if candidate[1] in SURNAMES or candidate[1:] in NON_PERSON_TAILS:
                    continue
                if any(c in STOP_CHARS for c in candidate[1:]):
                    continue
                if all("\u4e00" <= c <= "\u9fff" for c in candidate):
                    cand = candidate
                    break
        if cand:
            out.append(cand)
    return out


def _candidate_orgs(text: str) -> list[str]:
    suffix_pattern = "|".join(
        re.escape(suffix) for suffix in sorted(ORG_SUFFIXES, key=len, reverse=True)
    )
    action_pattern = "|".join(re.escape(action) for action in ORG_ACTIONS)
    pattern = re.compile(
        rf"([\u4e00-\u9fff]{{2,12}}?(?:{suffix_pattern}))(?={action_pattern})"
    )
    candidates = [match.group(1) for match in pattern.finditer(text)]
    # 解析器噪声过滤：包含动作/功能词的片段不是组织名（如“亭妃预计将持续借地方后援会”）。
    return [c for c in candidates if not any(term in c for term in ORG_NOISE_TERMS)]


def _normalize_org_candidate(cand: str) -> str:
    """Remove leading qualifiers and apply proven unambiguous normalizations."""
    for prefix in ORG_LEADING_QUALIFIERS:
        if cand.startswith(prefix):
            cand = cand[len(prefix):]
            break
    return ORG_NAME_NORMALIZATIONS.get(cand, cand)


def validate_structured_report(
    report: dict,
    ctx: EvidenceContext,
    *,
    expected_mode: str,
    assembled: bool = False,
) -> dict:
    """版本感知入口：v2.0 研判单元契约 / v1.1 历史契约。

    ``assembled``：两阶段装配产物标记。历史八栏目 claim plan 天然复用同一批
    事件，因此装配路径跳过“同一证据跨单元重复”硬校验（单阶段模型生成路径
    仍严格禁止，防止为覆盖栏目而堆叠同一事实）。
    """
    version = str(report.get("schema_version") or "")
    if version == "2.0":
        return _validate_structured_report_v2(
            report, ctx, expected_mode=expected_mode, assembled=assembled
        )
    return _validate_structured_report_v1(report, ctx, expected_mode=expected_mode)


def _validate_common_meta(
    result: dict[str, Any],
    report: dict,
    ctx: EvidenceContext,
    *,
    expected_mode: str,
) -> None:
    """数据上下文与生成模式校验（两个版本共享）。"""
    data = ctx.contract.get("data_status") or {}
    rp = ctx.contract.get("report_period") or {}
    elig = ctx.contract.get("generation_eligibility") or {}
    poll_gap = (ctx.contract.get("evidence_statistics") or {}).get("poll_gap", True)

    def ok(name: str, cond: bool, message: str = "") -> None:
        result[name] = bool(cond)
        if not cond and message:
            result["errors"].append(f"{name}: {message}")

    # ---- Data context ----
    authoritative = ctx.data_context
    reported = report.get("data_context") or {}
    dc_keys = (
        "active_snapshot_id",
        "previous_snapshot_id",
        "coverage_version",
        "facts_cutoff",
        "poll_cutoff",
        "period_start",
        "period_end",
        "uncovered_date_range",
    )
    dc_complete = all(key in reported for key in dc_keys)
    dc_mismatches = []
    for key in dc_keys:
        if key == "uncovered_date_range":
            if list(reported.get(key) or []) != list(authoritative.get(key) or []):
                dc_mismatches.append(key)
        elif reported.get(key) != authoritative.get(key):
            dc_mismatches.append(key)
    ok("data_context_complete", dc_complete, "data_context 字段不完整")
    ok(
        "data_context_matches_input",
        dc_complete and not dc_mismatches,
        "data_context 与输入合同不一致: " + ", ".join(dc_mismatches),
    )

    # ---- Generation mode ----
    mode_ok = report.get("generation_mode") == expected_mode
    ok("generation_mode_valid", mode_ok, f"期望 {expected_mode}，实际 {report.get('generation_mode')!r}")


def _validate_structured_report_v1(
    report: dict,
    ctx: EvidenceContext,
    *,
    expected_mode: str,
) -> dict:
    result: dict[str, Any] = {"errors": [], "warnings": []}

    def ok(name: str, cond: bool, message: str = "") -> None:
        result[name] = bool(cond)
        if not cond and message:
            result["errors"].append(f"{name}: {message}")

    # ---- Schema ----
    schema_errors = validate_report_schema(report)
    ok("output_schema_valid", not schema_errors, "; ".join(schema_errors))
    _validate_common_meta(result, report, ctx, expected_mode=expected_mode)
    _validate_claims_level(
        result,
        report,
        report.get("claims") or [],
        report.get("sections") or [],
        ctx,
        expected_mode=expected_mode,
        derived_v2=False,
    )
    result["all_claims_validated"] = not result["errors"]
    result["claim_evidence_ready"] = not result["errors"]
    return result


def _validate_structured_report_v2(
    report: dict,
    ctx: EvidenceContext,
    *,
    expected_mode: str,
    assembled: bool = False,
) -> dict:
    """v2.0 研判单元契约校验：结构校验 -> 派生 claims -> 复用 claim 级门禁。"""
    result: dict[str, Any] = {"errors": [], "warnings": []}

    def ok(name: str, cond: bool, message: str = "") -> None:
        result[name] = bool(cond)
        if not cond and message:
            result["errors"].append(f"{name}: {message}")

    # ---- Schema ----
    schema_errors = validate_report_schema_v2(report)
    ok("output_schema_valid", not schema_errors, "; ".join(schema_errors))
    _validate_common_meta(result, report, ctx, expected_mode=expected_mode)

    # ---- 研判单元内容结构 ----
    structure = validate_report_structure_v2(
        report, ctx, allow_evidence_overlap=assembled
    )
    result["report_structure"] = structure
    result["conclusion_summary_count"] = structure["conclusion_summary_count"]
    result["core_assessment_count"] = structure["core_assessment_count"]
    result["evidence_item_count"] = structure["evidence_item_count"]
    result["appendix_item_count"] = structure["appendix_item_count"]
    result["repeated_evidence_across_assessments"] = structure[
        "repeated_evidence_across_assessments"
    ]
    if structure["errors"]:
        result["report_structure_valid"] = False
        for item in structure["errors"]:
            result["errors"].append(f"report_structure: {item}")
    else:
        result["report_structure_valid"] = True
    for item in structure["warnings"]:
        result["warnings"].append(f"report_structure_warning: {item}")

    # ---- 派生 claims/sections 并复用 claim 级门禁 ----
    claims, sections = derive_claims_and_sections(report, ctx)
    _validate_claims_level(
        result,
        report,
        claims,
        sections,
        ctx,
        expected_mode=expected_mode,
        derived_v2=True,
        assembled=assembled,
    )

    # ---- 外部事实（覆盖全部文本字段）----
    texts: list[str] = []
    for item in report.get("conclusion_summary") or []:
        texts.append(str(item.get("judgment") or ""))
    for assessment in report.get("core_assessments") or []:
        texts.append(str(assessment.get("judgment") or ""))
        texts.append(str(assessment.get("reasoning") or ""))
        texts.append(str(assessment.get("falsifiers_or_limits") or ""))
        texts.extend(str(item.get("evidence_summary") or "") for item in assessment.get("evidence_items") or [])
    for item in report.get("appendix") or []:
        texts.append(str(item.get("item_text") or ""))
    texts.extend(str(item) for item in report.get("required_disclosures") or [])
    external_ok = all(
        "http://" not in text and "https://" not in text for text in texts
    )
    ok("no_external_facts", external_ok, "报告正文包含外部链接")

    result["all_claims_validated"] = not result["errors"]
    result["claim_evidence_ready"] = not result["errors"]
    return result


def _validate_claims_level(
    result: dict[str, Any],
    report: dict,
    claims: list,
    sections: list,
    ctx: EvidenceContext,
    *,
    expected_mode: str,
    derived_v2: bool = False,
    assembled: bool = False,
) -> None:
    """claim 级校验（两个版本共享；derived_v2 时跳过历史八栏目类型规则）。"""
    data = ctx.contract.get("data_status") or {}
    rp = ctx.contract.get("report_period") or {}
    elig = ctx.contract.get("generation_eligibility") or {}
    poll_gap = (ctx.contract.get("evidence_statistics") or {}).get("poll_gap", True)

    def ok(name: str, cond: bool, message: str = "") -> None:
        result[name] = bool(cond)
        if not cond and message:
            result["errors"].append(f"{name}: {message}")

    claim_ids = [c.get("claim_id") for c in claims]

    # ---- ID 完整性 ----
    ok("all_claim_ids_unique", len(claim_ids) == len(set(claim_ids)), "claim_id 重复")
    section_ids = [s.get("claim_ids") or [] for s in sections if isinstance(s, dict)]
    flat_section = [cid for ids in section_ids for cid in ids]
    ok("all_section_claim_ids_exist", set(flat_section) <= set(claim_ids), "section 引用不存在的 claim")
    ok("all_title_claim_ids_exist", set(report.get("title_claim_ids") or []) <= set(claim_ids), "title 引用不存在 claim")
    ok("all_overall_claim_ids_exist", set(report.get("overall_judgment_claim_ids") or []) <= set(claim_ids), "overall 引用不存在 claim")
    required_disclosure_ids = report.get("required_disclosures") or []
    data_disclosure_ids = {
        c.get("claim_id") for c in claims if c.get("claim_type") == "data_disclosure"
    }
    if derived_v2:
        ok(
            "required_disclosure_ids_valid",
            bool(required_disclosure_ids)
            and all(isinstance(t, str) and t.strip() for t in required_disclosure_ids)
            and len(required_disclosure_ids) == len(set(required_disclosure_ids)),
            "required_disclosures 必须是非空去重披露文字数组",
        )
    else:
        ok(
            "required_disclosure_ids_valid",
            bool(required_disclosure_ids)
            and set(required_disclosure_ids) == data_disclosure_ids,
            "required_disclosures 必须完整且仅引用 data_disclosure claims",
        )

    # ---- 引用 ----
    event_refs = {e for c in claims for e in (c.get("supporting_event_ids") or [])}
    poll_refs = {p for c in claims for p in (c.get("supporting_poll_ids") or [])}
    source_refs = {s for c in claims for s in (c.get("supporting_source_ids") or [])}
    gap_refs = {g for c in claims for g in (c.get("supporting_gap_ids") or [])}
    dim_refs = {d for c in claims for d in (c.get("supporting_snapshot_dimensions") or [])}
    ok("all_event_ids_exist", event_refs <= ctx.event_ids, "存在未知 event_id")
    ok("all_poll_ids_exist", poll_refs <= ctx.poll_ids, "存在未知 poll_id")
    ok("all_source_ids_exist", source_refs <= ctx.source_ids, "存在未知 source_id")
    ok("all_gap_ids_exist", gap_refs <= ctx.gap_ids, "存在未知 gap_id")
    ok("all_snapshot_dimensions_exist", dim_refs <= ctx.dimension_names, "存在未知 snapshot dimension")

    event_source_ok = all(
        bool(c.get("supporting_source_ids"))
        and all(
            any((eid, sid) in ctx.event_source_pairs for sid in c.get("supporting_source_ids") or [])
            for eid in (c.get("supporting_event_ids") or [])
        )
        and all(
            any((eid, sid) in ctx.event_source_pairs for eid in c.get("supporting_event_ids") or [])
            or any((pid, sid) in ctx.poll_source_pairs for pid in c.get("supporting_poll_ids") or [])
            for sid in c.get("supporting_source_ids") or []
        )
        for c in claims
        if c.get("supporting_event_ids")
    )
    ok("event_source_relationships_valid", event_source_ok, "event-source 关系错误")
    poll_source_ok = all(
        bool(c.get("supporting_source_ids"))
        and all(
            any((pid, sid) in ctx.poll_source_pairs for sid in c.get("supporting_source_ids") or [])
            for pid in (c.get("supporting_poll_ids") or [])
        )
        and all(
            any((pid, sid) in ctx.poll_source_pairs for pid in c.get("supporting_poll_ids") or [])
            or any((eid, sid) in ctx.event_source_pairs for eid in c.get("supporting_event_ids") or [])
            for sid in c.get("supporting_source_ids") or []
        )
        for c in claims
        if c.get("supporting_poll_ids")
    )
    ok("poll_source_relationships_valid", poll_source_ok, "poll-source 关系错误")

    # ---- Claim 类型规则 ----
    type_errors: list[str] = []
    confidence_errors: list[str] = []
    forward_errors: list[str] = []
    numeric_errors: list[str] = []
    date_errors: list[str] = []
    person_errors: list[str] = []
    org_errors: list[str] = []
    dni_violations: list[dict] = []
    unsupported_poll_errors: list[str] = []
    probability_errors: list[str] = []
    background_errors: list[str] = []
    deletion_errors: list[str] = []
    gap_material_errors: list[str] = []

    background_event_ids = {e["event_id"] for e in (ctx.contract.get("background_events") or []) if e.get("event_id")}
    for claim in claims:
        cid = claim.get("claim_id", "?")
        text = claim.get("claim_text") or ""
        ctype = claim.get("claim_type")
        conf = claim.get("confidence")
        event_ids = claim.get("supporting_event_ids") or []
        poll_ids = claim.get("supporting_poll_ids") or []
        source_ids = claim.get("supporting_source_ids") or []
        gap_ids = claim.get("supporting_gap_ids") or []
        dims = claim.get("supporting_snapshot_dimensions") or []

        if not derived_v2:
            if ctype == "factual_synthesis" and not event_ids and not poll_ids:
                type_errors.append(f"{cid}: factual_synthesis 必须引用 event 或 poll")
            if ctype == "current_assessment":
                if not (len(event_ids) >= 2 or (dims and event_ids)):
                    type_errors.append(f"{cid}: current_assessment 证据不足（需2个event 或 1 dimension+1 event）")
            if ctype == "comparative_assessment" and not dims:
                type_errors.append(f"{cid}: comparative_assessment 必须引用 snapshot dimension")
            if ctype == "forward_outlook":
                if len(event_ids) + len(poll_ids) < 2:
                    forward_errors.append(f"{cid}: forward_outlook 至少引用2项正式证据")
                if not claim.get("inference_basis"):
                    forward_errors.append(f"{cid}: forward_outlook 缺少 inference_basis")
                if conf == "high":
                    forward_errors.append(f"{cid}: forward_outlook 不得使用 high confidence")
                if not any(w in text for w in FORWARD_WORDS):
                    forward_errors.append(f"{cid}: forward_outlook 缺少研判语言（预计/可能/值得观察）")
            if ctype == "limitation" and not gap_ids and not any(
                t in text for t in ("缺口", "限制", "空窗", "不足", "未覆盖", "未纳入")
            ):
                type_errors.append(f"{cid}: limitation 必须引用 gap/limitation/研究任务或披露")
            if ctype == "data_disclosure" and not text:
                type_errors.append(f"{cid}: data_disclosure 内容为空")
        elif claim.get("_derived_kind") == "assessment" and ctype == "forward_outlook":
            if conf == "high":
                confidence_errors.append(f"{cid}: 前瞻判断不得使用 high confidence")
            if not claim.get("inference_basis"):
                forward_errors.append(f"{cid}: 前瞻判断缺少推理链")

        # 数字
        corpus = _claim_grounding_text(claim, ctx)
        for num in _extract_numbers(text):
            clean = num.replace(",", "")
            if clean not in corpus and num not in corpus:
                numeric_errors.append(f"{cid}: 数字 {num} 无证据依据")
        # 日期
        allowed_dates = _allowed_dates(claim, ctx)
        for d in _extract_dates(text):
            if d not in allowed_dates:
                date_errors.append(f"{cid}: 日期 {d} 无证据依据")
        # 实体
        for cand in _candidate_persons(text):
            if (
                cand not in ctx.known_names
                and cand not in ctx.whitelist_names
                and not any(cand in name for name in ctx.known_names)
                and cand not in ctx.evidence_corpus
            ):
                person_errors.append(f"{cid}: 证据包外人物 {cand}")
        for cand in _candidate_orgs(text):
            norm_cand = _normalize_org_candidate(cand)
            if (
                norm_cand not in ctx.known_orgs
                and norm_cand not in ctx.whitelist_names
                and not any(
                    known in norm_cand or norm_cand in known
                    for known in ctx.known_orgs | ctx.whitelist_names
                )
                and norm_cand not in ctx.evidence_corpus
                and cand not in ctx.evidence_corpus
            ):
                org_errors.append(f"{cid}: 证据包外组织 {cand}")
        # 民调/概率边界
        if any(t in text for t in PROBABILITY_TERMS) and not _negated(text):
            probability_errors.append(f"{cid}: 包含禁止的胜选概率表述")
        if (
            any(t in text for t in ("支持率", "民调显示", "民调结果", "民调支持"))
            and not poll_ids
            and not _negated(text)
        ):
            unsupported_poll_errors.append(f"{cid}: 出现支持率/民调表述但未引用正式民调")
        if poll_ids:
            for pid in poll_ids:
                p = ctx.polls.get(pid) or {}
                field_end = str(p.get("fieldwork_end") or "")[:10]
                period_start = str(rp.get("period_start") or "")[:10]
                historical_poll_disclosure = ctype in ("data_disclosure", "limitation") and any(
                    term in text
                    for term in (
                        "正式民调截止", "调查截止", "正式民调结束", "缺少可比的公开追踪民调",
                        "不代表", "并非实时", "禁止据此", "不能代表", "尚不能代表",
                    )
                )
                if field_end and period_start and field_end < period_start and any(
                    t in text for t in ("当前", "目前", "实时", "最新")
                ) and not _negated(text) and not historical_poll_disclosure:
                    unsupported_poll_errors.append(f"{cid}: 旧民调 {pid} 被写成当前实时支持率")
        # 背景事件伪装
        if ctype == "factual_synthesis" and event_ids and set(event_ids) <= background_event_ids and "本期" in text:
            background_errors.append(f"{cid}: 背景事件被写成本期事实")
        # 删除表述
        if any(t in text for t in ("删除", "已删除", "移除")) and any(t in text for t in ("正式事件", "正式民调", "事件库", "民调表")):
            deletion_errors.append(f"{cid}: 出现正式数据删除表述")
        # gap material
        for gid in gap_ids:
            if gid in ctx.gap_material and ctx.gap_material[gid] is False and any(
                t in text for t in ("已解决", "已消除", "resolved", "完成补足")
            ):
                gap_material_errors.append(f"{cid}: 非实质 gap {gid} 被写成 material 变化")

        # do_not_infer
        if _negated(text):
            continue
        for rule in DNI_RULES:
            if _rule_hit(text, rule):
                dni_violations.append({"rule_id": rule["rule_id"], "rule_text": rule["rule_text"], "claim_id": cid})
        for dni_text in ctx.do_not_infer_items:
            if len(dni_text) >= 8 and _normalize(dni_text) in _normalize(text):
                dni_violations.append({"rule_id": "contract_dni", "rule_text": dni_text, "claim_id": cid})

    ok("claim_type_rules_valid", not type_errors, "; ".join(type_errors))
    ok("confidence_rules_valid", not confidence_errors, "; ".join(confidence_errors))
    ok("forward_outlook_rules_valid", not forward_errors, "; ".join(forward_errors))
    ok("numeric_claims_grounded", not numeric_errors, "; ".join(numeric_errors))
    ok("date_claims_grounded", not date_errors, "; ".join(date_errors))
    ok("person_names_grounded", not person_errors, "; ".join(person_errors))
    ok("organization_names_grounded", not org_errors, "; ".join(org_errors))
    ok("no_unsupported_poll_claims", not unsupported_poll_errors, "; ".join(unsupported_poll_errors))
    ok("no_unsupported_probability", not probability_errors, "; ".join(probability_errors))
    ok("no_background_as_period_event", not background_errors, "; ".join(background_errors))
    ok("no_reference_removal_as_deletion", not deletion_errors, "; ".join(deletion_errors))
    ok("no_nonmaterial_gap_as_material_change", not gap_material_errors, "; ".join(gap_material_errors))
    ok("do_not_infer_compliant", not dni_violations, "; ".join(f"{v['rule_id']}@{v['claim_id']}" for v in dni_violations))

    # ---- 披露 ----
    disclosure_texts = [
        _normalize(c.get("claim_text") or "")
        for c in claims
        if c.get("claim_type") == "data_disclosure"
    ]
    facts_cutoff = str(data.get("facts_cutoff") or "")
    poll_cutoff = str(data.get("poll_cutoff") or "")
    uncovered = data.get("uncovered_date_range") or []
    facts_ok = bool(facts_cutoff) and any(
        "正式事实底表仅覆盖至" in t and facts_cutoff in t for t in disclosure_texts
    )
    poll_ok = bool(poll_cutoff) and any(
        "正式民调截止至" in t and poll_cutoff in t for t in disclosure_texts
    )
    if expected_mode == "final":
        # final 模式无未覆盖日期，也不应要求“没有重要事件/草稿”等草稿专属披露。
        uncovered_ok = True
        no_events_warning_ok = True
        draft_warning_ok = True
    else:
        uncovered_ok = bool(uncovered) and any(
            all(d in t for d in uncovered) and ("未覆盖" in t or "尚未纳入" in t or "尚未覆盖" in t)
            for t in disclosure_texts
        )
        no_events_warning_ok = any("没有重要事件" in t for t in disclosure_texts)
        draft_warning_ok = any("草稿" in t for t in disclosure_texts)
    no_new_poll_ok = (not poll_gap) or any("本期没有新增正式民调" in t for t in disclosure_texts)
    required_ok = facts_ok and poll_ok and uncovered_ok and no_events_warning_ok and draft_warning_ok and no_new_poll_ok
    ok("required_disclosures_complete", required_ok, "required disclosures 不完整")
    ok("facts_cutoff_disclosed", facts_ok, "facts_cutoff 未披露")
    ok("poll_cutoff_disclosed", poll_ok, "poll_cutoff 未披露")
    ok("uncovered_dates_disclosed", uncovered_ok, "未覆盖日期未披露")

    # ---- 外部事实（claim 文本）----
    external_ok = all("http://" not in (c.get("claim_text") or "") and "https://" not in (c.get("claim_text") or "") for c in claims)
    ok("no_external_facts", external_ok, "claim 包含外部链接")

    compliance = [
        {
            "rule_id": rule["rule_id"],
            "rule_text": rule["rule_text"],
            "violated": any(v["rule_id"] == rule["rule_id"] for v in dni_violations),
            "related_claim_ids": [v["claim_id"] for v in dni_violations if v["rule_id"] == rule["rule_id"]],
        }
        for rule in DNI_RULES
    ]
    for i, dni_text in enumerate(ctx.do_not_infer_items, 1):
        related = [v["claim_id"] for v in dni_violations if v.get("rule_text") == dni_text]
        compliance.append(
            {
                "rule_id": f"contract_dni_{i}",
                "rule_text": dni_text,
                "violated": bool(related),
                "related_claim_ids": related,
            }
        )
    result["do_not_infer_compliance"] = compliance

    semantic_results = [validate_claim_semantics(claim, ctx) for claim in claims]
    semantic_errors = [
        f"{item['claim_id']}: {', '.join(item['failures'])}"
        for item in semantic_results
        if not item["accepted"]
    ]
    result["claim_semantic_results"] = semantic_results
    ok(
        "claim_semantics_valid",
        not semantic_errors,
        "; ".join(semantic_errors),
    )
    result["derived_claim_count"] = len(claims)
    result["derived_section_count"] = len(sections)
