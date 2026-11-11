"""Word 产物验证器：只接受已验证结构化报告的确定性渲染结果。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evidence_pack_builder import canonical_hash
from .security_scan import env_secret_values, scan_text
from .word_report_renderer import (
    DRAFT_LABEL,
    SECTION_ORDER,
    extract_word_body,
    extract_word_text,
)


def word_text_signature(path: Path) -> str:
    return canonical_hash(extract_word_text(path))


def validate_report_artifact(
    structured_report: dict,
    docx_path: Path,
    *,
    expected_mode: str | None = None,
    generation_validation: dict | None = None,
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    result: dict[str, Any] = {
        "artifact_ready": False,
        "errors": errors,
        "warnings": warnings,
        "report_mode": "",
        "docx_path": "",
        "docx_size_bytes": 0,
        "section_count": 0,
        "claim_count": 0,
        "rendered_claim_count": 0,
        "required_disclosures_complete": True,
        "sensitive_content_detected": False,
    }

    docx_path = Path(docx_path)
    if not docx_path.exists():
        errors.append("docx_exists: 文件不存在")
        return result
    if docx_path.stat().st_size == 0:
        errors.append("docx_non_empty: 文件为空")
        return result
    try:
        text = extract_word_text(docx_path)
    except Exception as exc:  # noqa: BLE001
        errors.append(f"docx_openable: 无法打开（{exc}）")
        return result
    if not text.strip():
        errors.append("docx_openable: 无可读文本")
        return result

    result["docx_path"] = str(docx_path)
    result["docx_size_bytes"] = docx_path.stat().st_size

    title = structured_report.get("title") or ""
    if title and title not in text:
        errors.append("title_matches_structured_report: 标题不一致")

    sections = structured_report.get("sections") or []
    claims = structured_report.get("claims") or []
    result["section_count"] = len(sections)
    result["claim_count"] = len(claims)
    headings = [s.get("heading") for s in sections if s.get("heading")]
    result["rendered_claim_count"] = 0
    if len([h for h in headings if h in SECTION_ORDER]) != len(SECTION_ORDER):
        errors.append("all_required_sections_present: 八个固定章节不完整")
    elif len(headings) != len(SECTION_ORDER):
        errors.append("section_count_matches: 章节数量与结构化报告不一致")

    expected_order: list[str] = []
    for section in sections:
        for cid in section.get("claim_ids") or []:
            if cid not in expected_order:
                expected_order.append(cid)
    positions: list[int] = []
    body_text = extract_word_body(docx_path)
    rendered_ids: set[str] = set()
    for cid in expected_order:
        claim = next((c for c in claims if c.get("claim_id") == cid), None)
        if not claim or not claim.get("claim_text"):
            continue
        pos = body_text.find(str(claim.get("claim_text")))
        if pos < 0:
            errors.append(f"all_claims_rendered: claim {cid} 未渲染")
        else:
            rendered_ids.add(cid)
            result["rendered_claim_count"] += 1
            positions.append(pos)
    for claim in claims:
        if claim.get("claim_id") not in rendered_ids:
            errors.append(f"all_claims_rendered: claim {claim.get('claim_id')} 未渲染")
    if positions != sorted(positions):
        errors.append("claim_order_preserved: claim 顺序与结构化报告不一致")

    claims_by_id = {c.get("claim_id"): c for c in claims}
    disclosure_texts: list[str] = []
    for cid in structured_report.get("required_disclosures") or []:
        claim = claims_by_id.get(cid)
        if not claim:
            errors.append(f"required_disclosures_rendered: disclosure claim {cid} 不存在")
        elif claim.get("claim_text"):
            disclosure_texts.append(str(claim["claim_text"]))
    for claim in claims:
        if (
            claim.get("claim_type") == "data_disclosure"
            and claim.get("claim_text")
            and str(claim["claim_text"]) not in disclosure_texts
        ):
            disclosure_texts.append(str(claim["claim_text"]))
    missing = [d for d in disclosure_texts if d not in text]
    if missing:
        result["required_disclosures_complete"] = False
        errors.append(
            "required_disclosures_rendered: " + "；".join(str(x) for x in missing)
        )

    mode = structured_report.get("generation_mode") or ""
    result["report_mode"] = mode
    if expected_mode and mode != expected_mode:
        errors.append(f"expected_mode: 期望 {expected_mode}，实际 {mode}")
    if mode == "draft_with_data_gap" and DRAFT_LABEL not in text:
        errors.append("draft_label_present_when_required: 草稿标识缺失")
    if mode != "draft_with_data_gap" and DRAFT_LABEL in text:
        errors.append("draft_label: 正式报告不应包含草稿标识")

    dc = structured_report.get("data_context") or {}
    facts_cutoff = str(dc.get("facts_cutoff") or "")
    poll_cutoff = str(dc.get("poll_cutoff") or "")
    active_snapshot_id = str(dc.get("active_snapshot_id") or "")
    coverage_version = str(dc.get("coverage_version") or "")
    if facts_cutoff and facts_cutoff not in text:
        errors.append("facts_cutoff_matches: 事实截止日未渲染")
    if poll_cutoff and poll_cutoff not in text:
        errors.append("poll_cutoff_matches: 民调截止日未渲染")
    if active_snapshot_id and active_snapshot_id not in text:
        errors.append("active_snapshot_present: 当前快照未渲染")
    if coverage_version and coverage_version not in text:
        errors.append("coverage_version_present: 覆盖版本未渲染")
    if "当前快照：未随报告携带" in text:
        errors.append("active_snapshot_present: 仍显示快照缺失占位")
    dc_values = [
        str(dc.get(k))
        for k in ("period_start", "period_end")
        if dc.get(k)
    ]
    if mode == "draft_with_data_gap":
        dc_values += [str(x) for x in dc.get("uncovered_date_range") or []]
    missing_dc = [v for v in dc_values if v and v not in text]
    if missing_dc:
        errors.append(
            "data_context_matches_structured_report: 缺失 " + "、".join(missing_dc)
        )

    if structured_report.get("report_status") == "rejected":
        errors.append("no_rejected_claims_rendered: rejected 报告不得渲染")

    scan = scan_text(text, env_secret_values=env_secret_values())
    if any(scan[k] for k in ("deepseek_api_key_exposed", "feishu_webhook_exposed", "authorization_header_exposed", "absolute_developer_path_exposed", "secret_env_value_exposed")):
        result["sensitive_content_detected"] = True
        errors.append("no_sensitive_paths / no_api_keys: 检测到敏感内容")

    if generation_validation:
        if generation_validation.get("report_generation_ready") is not True:
            errors.append("generation_validation: 报告生成校验未通过")
        if generation_validation.get("all_claims_validated") is not True:
            errors.append("generation_validation: claim 未全部通过校验")

    result["artifact_ready"] = not errors
    return result
