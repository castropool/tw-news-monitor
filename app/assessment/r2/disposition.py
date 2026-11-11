"""Phase R2.1 production disposition layer.

Validator 负责发现问题；本层负责判断问题是否必须阻塞人工终审。
不修改任何核心 Validator 语义，原始审计结果必须原样保留。
"""

from __future__ import annotations

from typing import Any


POLICY_VERSION = "phase_r21.severity_policy.v1"

HARD_BLOCK = "HARD_BLOCK"
REVIEW_REQUIRED = "REVIEW_REQUIRED"
PASS = "PASS"

ATTRIBUTION_TERMS = (
    "称", "表示", "指出", "声称", "指控", "重申", "表述", "说法", "主张", "认为",
    "回应", "解释", "坦言", "强调", "呼吁",
)
HEDGING_TERMS = (
    "不能视作", "不代表", "不足以", "尚不能", "无法", "并非", "禁止", "不能",
    "预计", "可能", "研判", "待观察", "未必", "难以",
)


def _statement_fact_serious(text: str | None) -> bool:
    """statement_as_fact severity per frozen policy.

    Serious: 把未经证实的具体事实/指控写成已证实事实，且无归属或限定语。
    Non-serious: 仍可追溯归属/限定，人工可快速识别。
    原文不可用视为 serious（无法证明为非严重）。
    """
    if not text:
        return True
    if any(t in text for t in ATTRIBUTION_TERMS):
        return False
    if any(t in text for t in HEDGING_TERMS):
        return False
    return True


def classify_disposition(
    validation: dict,
    report: dict | None = None,
    *,
    outside_events: dict[str, dict] | None = None,
    allowed_event_ids: set[str] | None = None,
    integrity_ok: bool = True,
    period_gate_ok: bool = True,
    future_leakage_count: int = 0,
) -> dict:
    """Return production disposition for a validated report.

    ``validation`` 必须是完整原始 Validator 输出（不得改写）。
    ``outside_events``: {event_id: {"real": bool, "future": bool}} 用于证据包外引用分级。
    """
    hard: list[str] = []
    review: list[str] = []
    allowed = set(allowed_event_ids or [])
    semantic = {
        r.get("claim_id"): r for r in (validation.get("claim_semantic_results") or [])
    }
    claims = {c.get("claim_id"): c for c in (report or {}).get("claims") or []}
    sections = report.get("sections") if report else None
    report_version = str((report or {}).get("schema_version") or "")
    is_v2 = report_version == "2.0"

    def section_of(cid: str) -> str:
        if not sections:
            return ""
        for s in sections:
            if cid in (s.get("claim_ids") or []):
                return str(s.get("heading") or "")
        return ""

    # ---- HARD_BLOCK rules ----
    if validation.get("no_external_facts") is False:
        hard.append("fabricated_fact")
    if future_leakage_count > 0:
        hard.append("future_event_leakage")
    if not period_gate_ok:
        hard.append("period_gate_not_satisfied")
    if not integrity_ok:
        hard.append("report_integrity_failure")
    if is_v2:
        # v2.0 研判单元契约：结构合法性由 report_structure_valid 门禁负责；
        # 正文为 结论摘要 -> 核心研判 -> 附录（3 个 section），不得按八栏目校验。
        if validation.get("report_structure_valid") is False:
            hard.append("schema_severe_damage_missing_sections")
        if not (report or {}).get("core_assessments"):
            hard.append("schema_severe_damage_empty_body")
    else:
        if not sections or len(sections) < 8:
            hard.append("schema_severe_damage_missing_sections")
        if report is not None and not (report.get("claims")):
            hard.append("schema_severe_damage_empty_body")
    if validation.get("all_source_ids_exist") is False:
        hard.append("deterministic_mapping_error_source")
    if validation.get("all_poll_ids_exist") is False:
        hard.append("deterministic_mapping_error_poll")
    if validation.get("poll_source_relationships_valid") is False:
        hard.append("deterministic_mapping_error_poll_source")

    for cid, res in semantic.items():
        failures = set(res.get("failures") or [])
        if "claim_strength_exceeds_evidence" in failures:
            hard.append(f"serious_unsupported_factual_assertion:{cid}")
        if "statement_as_fact" in failures:
            text = (claims.get(cid) or {}).get("claim_text")
            if _statement_fact_serious(text):
                hard.append(f"serious_statement_as_fact:{cid}")
            else:
                review.append(
                    f"non_serious_statement_as_fact:{cid}|{section_of(cid)}"
                )
        if "allegation_as_fact" in failures:
            text = (claims.get(cid) or {}).get("claim_text")
            if _statement_fact_serious(text):
                hard.append(f"serious_allegation_as_fact:{cid}")
            else:
                review.append(
                    f"non_serious_allegation_as_fact:{cid}|{section_of(cid)}"
                )
        if "invalid_event_reference" in failures:
            event_ids = (claims.get(cid) or {}).get("supporting_event_ids") or []
            for eid in event_ids:
                if eid in allowed:
                    continue
                info = (outside_events or {}).get(eid, {})
                real = bool(info.get("real"))
                future = bool(info.get("future"))
                if not real or future:
                    hard.append(f"outside_pack_event_invalid_or_future:{cid}:{eid}")
                else:
                    review.append(
                        f"outside_pack_event_real_historical:{cid}:{eid}|{section_of(cid)}"
                    )
        if "claim_not_atomic" in failures:
            review.append(f"non_atomic_claim:{cid}|{section_of(cid)}")
        if "evidence_does_not_support_claim" in failures:
            review.append(f"minor_evidence_support:{cid}|{section_of(cid)}")
        if "missing_inference_basis" in failures:
            review.append(f"missing_inference_basis:{cid}|{section_of(cid)}")

    # ---- report-level review-required hints ----
    if validation.get("person_names_grounded") is False:
        review.append("parser_noise_person")
    if validation.get("organization_names_grounded") is False:
        review.append("parser_noise_org")
    if validation.get("no_unsupported_poll_claims") is False:
        review.append("poll_boundary_non_serious")
    if validation.get("claim_type_rules_valid") is False:
        review.append("claim_type_auxiliary")
    if validation.get("numeric_claims_grounded") is False:
        review.append("numeric_format")
    if validation.get("required_disclosure_ids_valid") is False:
        review.append("required_disclosures_auxiliary")
    if validation.get("date_claims_grounded") is False:
        review.append("date_format")

    if hard:
        disposition = HARD_BLOCK
    elif review:
        disposition = REVIEW_REQUIRED
    else:
        disposition = PASS

    return {
        "policy_version": POLICY_VERSION,
        "production_disposition": disposition,
        "hard_block_reasons": hard,
        "review_required_reasons": review,
        "original_machine_result": {
            "all_claims_validated": validation.get("all_claims_validated"),
            "errors": validation.get("errors") or [],
        },
    }


def build_review_notes(disposition: dict, report: dict | None = None, *, top: int = 5) -> list[str]:
    """Top human-readable review notes (no internal claim IDs)."""
    if disposition.get("production_disposition") == PASS:
        return []
    notes: list[str] = []
    reasons = disposition.get("review_required_reasons") or []
    for reason in reasons:
        kind = reason.split(":", 1)[0]
        section = ""
        if "|" in reason:
            section = reason.split("|", 1)[1]
        prefix = f"【{section}】" if section else ""
        if kind == "non_atomic_claim":
            notes.append(f"{prefix}存在复合判断句（一个 Claim 含多个独立断言），请确认拆分后语义不变。")
        elif kind == "minor_evidence_support":
            notes.append(f"{prefix}个别判断与证据表述距离略远（有限推断），请确认未超出证据边界。")
        elif kind == "non_serious_statement_as_fact":
            notes.append(f"{prefix}某句人物表态写得略强，正文仍有归属/限定可追溯，请确认是否需要降调。")
        elif kind == "outside_pack_event_real_historical":
            notes.append(f"{prefix}引用了正式库中真实存在、但未列入本期证据包的历史事件，请确认引用边界可接受。")
        elif kind == "poll_boundary_non_serious":
            notes.append(f"{prefix}旧民调提示：请确认正文未把过期民调写成当前支持率。")
        elif kind == "parser_noise_person":
            notes.append("人名解析提示：疑似文本截取噪声（并非真实人物），请扫一眼确认正文无人名错认。")
        elif kind == "parser_noise_org":
            notes.append("组织名解析提示：疑似简称/噪声，请确认无组织错认。")
        elif kind == "claim_type_auxiliary":
            notes.append("个别 Claim 类型/证据数量辅助字段不合规，不影响正文事实，请确认。")
        elif kind == "numeric_format":
            notes.append("个别数字/日期格式提示（如快照 ID 中的数字串），请确认无虚构数字。")
        elif kind == "required_disclosures_auxiliary":
            notes.append("required_disclosures 结构字段遗漏，不影响正文，请确认。")
        elif kind == "missing_inference_basis":
            notes.append(f"{prefix}个别分析判断缺推理依据说明，请确认判断有据。")
        elif kind == "date_format":
            notes.append("日期格式提示，请确认无日期错误。")
        else:
            notes.append(f"{prefix}机器提示需重点复核（{reason}）。")
    return notes[:top]
