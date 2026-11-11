"""Markdown 草稿渲染器：只使用已验证的 claim / 研判单元。

v1.1（历史契约）与 v2.0（研判单元契约）都支持：
v2.0 渲染顺序固定为 结论摘要 -> 核心研判（判断->证据->推理->反证/限制->置信度/观察指标）
-> 数据限制与事实附录 -> 证据映射。
"""

from __future__ import annotations

import re
from typing import Any

from .report_structure_validator import derive_claims_and_sections


def _chinese_char_count(text: str) -> int:
    return len(re.findall(r"[\u4e00-\u9fff]", text))


def render_report_markdown(report: dict, contract: dict) -> dict:
    version = str(report.get("schema_version") or "")
    if version == "2.0":
        return _render_v2_markdown(report, contract)
    return _render_v1_markdown(report, contract)


def _render_v1_markdown(report: dict, contract: dict) -> dict:
    claims = {c.get("claim_id"): c for c in (report.get("claims") or [])}
    sections = report.get("sections") or []
    data = contract.get("data_status") or {}
    rp = contract.get("report_period") or {}
    dc = report.get("data_context") or {}
    lines: list[str] = []
    add = lines.append

    add(report.get("title") or "")
    add("")
    add("【报告状态】")
    add(f"- 报告周期：{rp.get('period_start')} 至 {rp.get('period_end')}")
    add(f"- 生成模式：{report.get('generation_mode')}")
    add(f"- 事实截止日：{data.get('facts_cutoff') or '未披露'}")
    add(f"- 民调截止日：{data.get('poll_cutoff') or '未披露'}")
    add(f"- 当前快照：{dc.get('active_snapshot_id') or data.get('active_snapshot_id') or '未披露'}")
    add(f"- 覆盖版本：{dc.get('coverage_version') or data.get('coverage_version') or '未披露'}")
    add(f"- 数据缺口：{'、'.join(data.get('uncovered_date_range') or []) or '无'}")
    add("")

    for section in sections:
        add(section.get("heading") or "")
        add("")
        for cid in section.get("claim_ids") or []:
            claim = claims.get(cid)
            if not claim:
                continue
            add(claim.get("claim_text") or "")
            add("")

    add("【证据映射】")
    for claim in report.get("claims") or []:
        refs = _claim_refs_text(claim)
        add(f"- {claim.get('claim_id')} [{claim.get('claim_type')}] -> {'; '.join(refs) if refs else '无'}")

    text = "\n".join(lines)
    chinese_chars = _chinese_char_count(text)
    length_below = chinese_chars < 1800
    return {
        "markdown": text + "\n",
        "chinese_char_count": chinese_chars,
        "length_below_target": length_below,
    }


def _render_v2_markdown(report: dict, contract: dict) -> dict:
    rp = contract.get("report_period") or {}
    data = contract.get("data_status") or {}
    dc = report.get("data_context") or {}
    lines: list[str] = []
    add = lines.append

    add(report.get("title") or "")
    add("")
    add("【报告状态】")
    add(f"- 报告周期：{rp.get('period_start')} 至 {rp.get('period_end')}")
    add(f"- 生成模式：{report.get('generation_mode')}")
    add(f"- 事实截止日：{data.get('facts_cutoff') or '未披露'}")
    add(f"- 民调截止日：{data.get('poll_cutoff') or '未披露'}")
    add(f"- 当前快照：{dc.get('active_snapshot_id') or data.get('active_snapshot_id') or '未披露'}")
    add(f"- 覆盖版本：{dc.get('coverage_version') or data.get('coverage_version') or '未披露'}")
    add(f"- 数据缺口：{'、'.join(data.get('uncovered_date_range') or []) or '无'}")
    add("")

    # 1) 结论摘要（首屏）
    add("一、结论摘要")
    add("")
    for item in report.get("conclusion_summary") or []:
        add(f"- {item.get('judgment')}（置信度：{item.get('confidence')}）")
        refs = _refs_text(item.get("evidence_refs") or {})
        if refs:
            add(f"  - 证据：{refs}")
        add("")

    # 2) 核心研判（判断 -> 证据 -> 推理 -> 反证/限制 -> 置信度/观察指标）
    add("二、核心研判")
    add("")
    for index, assessment in enumerate(report.get("core_assessments") or [], 1):
        add(f"研判{index}：{assessment.get('judgment')}")
        add("")
        add("最近事实证据：")
        for ev in assessment.get("evidence_items") or []:
            add(
                f"- {ev.get('evidence_date')} {ev.get('evidence_summary')}"
                f"（{ev.get('evidence_id')}）"
            )
        add("")
        add(f"推理链：{assessment.get('reasoning')}")
        add("")
        add(f"反证/限制条件：{assessment.get('falsifiers_or_limits')}")
        add("")
        indicators = "；".join(assessment.get("watch_indicators") or [])
        add(
            f"置信度：{assessment.get('confidence')}；下一期观察指标：{indicators}"
        )
        add("")

    # 3) 数据限制与事实附录
    add("三、数据限制与事实附录")
    add("")
    for item in report.get("appendix") or []:
        add(f"- [{item.get('item_type')}] {item.get('item_text')}")
        refs = _refs_text(item.get("evidence_refs") or {})
        if refs:
            add(f"  - 证据：{refs}")
    if report.get("required_disclosures"):
        add("")
        add("必需披露：")
        for text in report.get("required_disclosures") or []:
            add(f"- {text}")
    add("")

    # 4) 证据映射（派生 claim 级）
    add("【证据映射】")
    ctx = _structure_ctx(contract)
    claims, _ = derive_claims_and_sections(report, ctx)
    for claim in claims:
        refs = _claim_refs_text(claim)
        add(f"- {claim.get('claim_id')} [{claim.get('claim_type')}] -> {'; '.join(refs) if refs else '无'}")

    text = "\n".join(lines)
    chinese_chars = _chinese_char_count(text)
    length_below = chinese_chars < 1800
    return {
        "markdown": text + "\n",
        "chinese_char_count": chinese_chars,
        "length_below_target": length_below,
    }


def _structure_ctx(contract: dict):
    from .claim_evidence_validator import build_evidence_context

    return build_evidence_context(contract, evidence_pack=None, config={})


def _claim_refs_text(claim: dict) -> list[str]:
    refs = []
    if claim.get("supporting_event_ids"):
        refs.append("event:" + ",".join(claim["supporting_event_ids"]))
    if claim.get("supporting_poll_ids"):
        refs.append("poll:" + ",".join(claim["supporting_poll_ids"]))
    if claim.get("supporting_source_ids"):
        refs.append("source:" + ",".join(claim["supporting_source_ids"]))
    if claim.get("supporting_gap_ids"):
        refs.append("gap:" + ",".join(claim["supporting_gap_ids"]))
    if claim.get("supporting_snapshot_dimensions"):
        refs.append("dim:" + ",".join(claim["supporting_snapshot_dimensions"]))
    return refs


def _refs_text(refs: dict) -> str:
    parts = []
    mapping = (
        ("event_ids", "event"),
        ("poll_ids", "poll"),
        ("source_ids", "source"),
        ("gap_ids", "gap"),
        ("dimension_ids", "dim"),
    )
    for key, label in mapping:
        values = refs.get(key) or []
        if values:
            parts.append(f"{label}:{','.join(values)}")
    return "；".join(parts)
