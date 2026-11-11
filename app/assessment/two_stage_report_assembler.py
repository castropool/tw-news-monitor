"""Deterministically assemble a Stage 2 draft into final report Schema v2.0.

两阶段（Claim Planner -> Stage 2 Writer）与单阶段生产路径共享 v2.0 研判单元
契约：结论摘要 -> 核心研判（判断->证据->推理->反证/限制->置信度/观察指标）
-> 数据限制与事实附录。历史八栏目 claims 在此被确定性映射：
- overall/title 引用 -> 结论摘要；
- 分析型 claims（current/comparative/forward）按 section 分组 -> 核心研判
  （最多 3 组，其余并入附录）；
- 事实/限制/披露 claims -> 附录与 required_disclosures。
"""

from __future__ import annotations

import hashlib
import re
from copy import deepcopy

from .claim_plan_validator import adapt_plan_claim
from .llm_input_contract import build_data_context

ASSESSMENT_TYPES = {"current_assessment", "comparative_assessment", "forward_outlook"}
MAX_CORE_ASSESSMENTS = 3
FALLBACK_FALSIFIERS = "本判断依赖所引证据；若下一期出现相反方向的正式事件或民调，本判断将被推翻或削弱。"
FALLBACK_WATCH = ["下一期报告观察该研判后续进展"]


def _unique_reference_count(claims: list[dict], key: str) -> int:
    return len({value for claim in claims for value in claim.get(key) or []})


def _event_date(ev: dict) -> str:
    for key in ("event_date",):
        if ev.get(key):
            return str(ev[key])[:10]
    for sub in ev.get("subevents") or []:
        if sub.get("subevent_date"):
            return str(sub["subevent_date"])[:10]
    return ""


def _poll_date(poll: dict) -> str:
    for key in ("fieldwork_end", "release_date", "fieldwork_start"):
        if poll.get(key):
            return str(poll[key])[:10]
    return ""


def _evidence_item(claim: dict, ctx_events: dict, ctx_polls: dict) -> list[dict]:
    items: list[dict] = []
    for eid in claim.get("supporting_event_ids") or []:
        ev = ctx_events.get(eid) or {}
        items.append(
            {
                "evidence_id": eid,
                "evidence_date": _event_date(ev),
                "evidence_summary": str(ev.get("title") or ev.get("fact_summary") or "正式事件"),
            }
        )
    for pid in claim.get("supporting_poll_ids") or []:
        poll = ctx_polls.get(pid) or {}
        pollster = str(poll.get("pollster") or "")
        items.append(
            {
                "evidence_id": pid,
                "evidence_date": _poll_date(poll),
                "evidence_summary": f"{pollster}民调" if pollster else "正式民调",
            }
        )
    return items[:4]


def _refs_from_claim(claim: dict) -> dict:
    return {
        "event_ids": list(claim.get("supporting_event_ids") or []),
        "poll_ids": list(claim.get("supporting_poll_ids") or []),
        "source_ids": list(claim.get("supporting_source_ids") or []),
        "gap_ids": list(claim.get("supporting_gap_ids") or []),
        "dimension_ids": list(claim.get("supporting_snapshot_dimensions") or []),
    }


def assemble_final_report(draft: dict, *, store: dict, contract: dict) -> dict:
    """Join rendered prose to authoritative Claim metadata without model inference."""
    rendered = {
        item["claim_id"]: item["rendered_text"]
        for item in draft.get("claim_renderings") or []
    }
    adapted: list[dict] = []
    for plan_claim in store.get("accepted_claims") or []:
        claim = adapt_plan_claim(plan_claim)
        claim["claim_text"] = rendered.get(plan_claim.get("claim_id"), claim["claim_text"])
        claim["target_section_id"] = plan_claim.get("target_section_id")
        adapted.append(claim)

    ctx_events = {
        e.get("event_id"): e
        for e in (contract.get("period_events") or []) + (contract.get("background_events") or [])
        if e.get("event_id")
    }
    ctx_polls = {p.get("poll_id"): p for p in (contract.get("polls") or []) if p.get("poll_id")}
    by_id = {c.get("claim_id"): c for c in adapted}

    # ---- 结论摘要（overall/title 引用，最多 3 条）----
    conclusion_ids: list[str] = []
    for cid in (draft.get("overall_judgment_claim_ids") or []) + (
        draft.get("title_claim_ids") or []
    ):
        if cid in by_id and cid not in conclusion_ids:
            conclusion_ids.append(cid)
    if not conclusion_ids:
        # 兜底：取第一个分析型 claim（确定性）。
        for claim in adapted:
            if claim.get("claim_type") in ASSESSMENT_TYPES:
                conclusion_ids.append(claim.get("claim_id"))
                break
    conclusion_summary = []
    for index, cid in enumerate(conclusion_ids[:3], 1):
        claim = by_id[cid]
        conclusion_summary.append(
            {
                "summary_id": f"CS{index}",
                "judgment": claim.get("claim_text") or "",
                "confidence": (
                    claim.get("confidence")
                    if claim.get("confidence") in {"high", "medium", "low"}
                    else "medium"
                ),
                "evidence_refs": _refs_from_claim(claim),
            }
        )

    # ---- 核心研判（分析型 claim 按 section 分组，最多 3 组）----
    conclusion_set = set(conclusion_ids)
    groups: dict[str, list[dict]] = {}
    for claim in adapted:
        if claim.get("claim_id") in conclusion_set:
            continue
        if claim.get("claim_type") not in ASSESSMENT_TYPES:
            continue
        if claim.get("material_for_report") is False:
            continue
        groups.setdefault(str(claim.get("target_section_id") or "S00"), []).append(claim)

    core_assessments = []
    for section_id in sorted(groups):
        if len(core_assessments) >= MAX_CORE_ASSESSMENTS:
            break
        group = groups[section_id]
        lead = group[0]
        basis = str(lead.get("inference_basis") or "").strip()
        if len(re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", basis)) < 8:
            basis = "基于所引正式事件/民调与快照差异综合研判"
        limitations = [
            str(item) for item in (lead.get("limitations") or []) if str(item).strip()
        ]
        items = _evidence_item(lead, ctx_events, ctx_polls)
        if len(items) < 2:
            # 证据不足时补充同组事实 claims 的引用（确定性去重）。
            for other in group[1:]:
                for extra in _evidence_item(other, ctx_events, ctx_polls):
                    if all(extra["evidence_id"] != it["evidence_id"] for it in items):
                        items.append(extra)
        core_assessments.append(
            {
                "assessment_id": str(lead.get("claim_id") or f"CA{len(core_assessments) + 1}"),
                "judgment": lead.get("claim_text") or "",
                "evidence_items": items[:4],
                "evidence_refs": _refs_from_claim(lead),
                "reasoning": basis,
                "falsifiers_or_limits": (
                    "；".join(limitations) if limitations else FALLBACK_FALSIFIERS
                ),
                "confidence": (
                    lead.get("confidence")
                    if lead.get("confidence") in {"high", "medium", "low"}
                    else "medium"
                ),
                "watch_indicators": (
                    limitations[:3] if limitations else list(FALLBACK_WATCH)
                ),
            }
        )

    # ---- 附录与必需披露 ----
    in_assessment_ids = {
        claim.get("claim_id")
        for group in groups.values()
        for claim in group[:1]
    }
    appendix: list[dict] = []
    required_disclosures: list[str] = []
    next_ap = 1
    for claim in adapted:
        if claim.get("claim_id") in conclusion_set:
            continue
        if claim.get("claim_id") in in_assessment_ids:
            continue
        if claim.get("claim_type") == "data_disclosure":
            text = claim.get("claim_text") or ""
            if text and text not in required_disclosures:
                required_disclosures.append(text)
            appendix.append(
                {
                    "item_id": f"AP{next_ap}",
                    "item_type": "disclosure",
                    "item_text": text,
                    "evidence_refs": _refs_from_claim(claim),
                }
            )
            next_ap += 1
        elif claim.get("claim_type") == "limitation":
            appendix.append(
                {
                    "item_id": f"AP{next_ap}",
                    "item_type": "data_limitation",
                    "item_text": claim.get("claim_text") or "",
                    "evidence_refs": _refs_from_claim(claim),
                }
            )
            next_ap += 1
        else:
            appendix.append(
                {
                    "item_id": f"AP{next_ap}",
                    "item_type": "background_fact",
                    "item_text": claim.get("claim_text") or "",
                    "evidence_refs": _refs_from_claim(claim),
                }
            )
            next_ap += 1

    do_not_infer = [
        {
            "rule_id": f"DNI_{index:03d}",
            "rule_text": str(rule),
            "violated": False,
            "related_claim_ids": [],
        }
        for index, rule in enumerate(contract.get("do_not_infer") or [], 1)
    ]
    chinese_count = len(re.findall(r"[\u4e00-\u9fff]", "".join(c["claim_text"] for c in adapted)))
    report_key = "|".join(
        [str(contract.get("election_id") or ""), store.get("claim_plan_business_hash") or ""]
    )
    report_id = "two-stage-" + hashlib.sha256(report_key.encode("utf-8")).hexdigest()[:16]
    return {
        "schema_version": "2.0",
        "report_id": report_id,
        "election_id": contract.get("election_id") or "",
        "report_period": deepcopy(contract.get("report_period") or {}),
        "generation_mode": (contract.get("generation_eligibility") or {}).get(
            "allowed_generation_mode", "draft_with_data_gap"
        ),
        "report_status": "generated",
        "title": draft.get("title") or "",
        "conclusion_summary": conclusion_summary,
        "core_assessments": core_assessments,
        "appendix": appendix,
        "required_disclosures": required_disclosures,
        "do_not_infer_compliance": do_not_infer,
        "report_statistics": {
            "claim_count": len(adapted),
            "section_count": 3,
            "core_assessment_count": len(core_assessments),
            "conclusion_summary_count": len(conclusion_summary),
            "evidence_item_count": sum(
                len(a.get("evidence_items") or []) for a in core_assessments
            ),
            "event_reference_count": _unique_reference_count(adapted, "supporting_event_ids"),
            "poll_reference_count": _unique_reference_count(adapted, "supporting_poll_ids"),
            "source_reference_count": _unique_reference_count(adapted, "supporting_source_ids"),
            "gap_reference_count": _unique_reference_count(adapted, "supporting_gap_ids"),
            "chinese_char_count": chinese_count,
            "length_below_target": chinese_count < 1800,
        },
        "data_context": build_data_context(contract),
    }
