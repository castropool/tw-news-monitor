"""严格结构化报告输出 Schema 校验（不使用 jsonschema 依赖）。

v1.1 为历史契约（旧 run 读取/展示兼容，保持不变）。
v2.0 为研判单元契约（观点/判断优先）：结论摘要(1-3) -> 核心研判(1-3) -> 附录。
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any


SCHEMA_PATH = Path(__file__).resolve().parent / "schemas" / "tainan_assessment_report_v1.schema.json"
SCHEMA_PATH_V2 = Path(__file__).resolve().parent / "schemas" / "tainan_assessment_report_v2.schema.json"

REPORT_REQUIRED = [
    "schema_version",
    "report_id",
    "election_id",
    "report_period",
    "generation_mode",
    "report_status",
    "title",
    "title_claim_ids",
    "overall_judgment_claim_ids",
    "sections",
    "claims",
    "required_disclosures",
    "do_not_infer_compliance",
    "report_statistics",
    "data_context",
]

SCHEMA_VERSION = "1.1"

DATA_CONTEXT_REQUIRED = [
    "active_snapshot_id",
    "previous_snapshot_id",
    "coverage_version",
    "facts_cutoff",
    "poll_cutoff",
    "period_start",
    "period_end",
    "uncovered_date_range",
]

CLAIM_REQUIRED = [
    "claim_id",
    "claim_type",
    "claim_text",
    "confidence",
    "material_for_report",
    "supporting_event_ids",
    "supporting_poll_ids",
    "supporting_source_ids",
    "supporting_snapshot_dimensions",
    "supporting_gap_ids",
    "inference_basis",
    "limitations",
    "applies_to_period",
]

CLAIM_TYPES = {
    "factual_synthesis",
    "current_assessment",
    "comparative_assessment",
    "forward_outlook",
    "limitation",
    "data_disclosure",
}
CONFIDENCE_LEVELS = {"high", "medium", "low", "not_applicable"}
GENERATION_MODES = {"final", "draft_with_data_gap"}
REPORT_STATUSES = {"generated", "repaired", "rejected"}
REQUIRED_SECTION_IDS = tuple(f"S{i:02d}" for i in range(1, 9))


def load_schema() -> dict:
    with open(SCHEMA_PATH, encoding="utf-8") as f:
        return json.load(f)


def load_schema_v2() -> dict:
    with open(SCHEMA_PATH_V2, encoding="utf-8") as f:
        return json.load(f)


def validate_report_schema(report: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["输出不是 JSON 对象"]
    allowed_top = set(REPORT_REQUIRED) | {"report_statistics"}
    extra = set(report) - allowed_top
    if extra:
        errors.append(f"顶层多余字段: {sorted(extra)}")
    missing = [k for k in REPORT_REQUIRED if k not in report]
    if missing:
        errors.append(f"缺少顶层必需字段: {missing}")
    if report.get("schema_version") != SCHEMA_VERSION:
        errors.append(
            f"schema_version 必须为 {SCHEMA_VERSION}，实际 {report.get('schema_version')!r}"
        )
    if report.get("generation_mode") not in GENERATION_MODES:
        errors.append(f"generation_mode 非法: {report.get('generation_mode')!r}")
    if report.get("report_status") not in REPORT_STATUSES:
        errors.append(f"report_status 非法: {report.get('report_status')!r}")

    sections = report.get("sections")
    if not isinstance(sections, list):
        errors.append("sections 必须是数组")
    else:
        if len(sections) != 8:
            errors.append(f"sections 必须恰好包含八个章节，实际 {len(sections)}")
        section_ids = [
            section.get("section_id")
            for section in sections
            if isinstance(section, dict)
        ]
        if tuple(section_ids) != REQUIRED_SECTION_IDS:
            errors.append(
                f"sections.section_id 必须依次为 {list(REQUIRED_SECTION_IDS)}"
            )
        for i, section in enumerate(sections):
            if not isinstance(section, dict):
                errors.append(f"sections[{i}] 不是对象")
                continue
            for key in ("section_id", "heading", "claim_ids", "section_purpose"):
                if key not in section:
                    errors.append(f"sections[{i}] 缺少 {key}")
            if set(section) - {"section_id", "heading", "claim_ids", "section_purpose"}:
                errors.append(f"sections[{i}] 包含额外字段")

    claims = report.get("claims")
    if not isinstance(claims, list):
        errors.append("claims 必须是数组")
    else:
        for i, claim in enumerate(claims):
            if not isinstance(claim, dict):
                errors.append(f"claims[{i}] 不是对象")
                continue
            missing_claim = [k for k in CLAIM_REQUIRED if k not in claim]
            if missing_claim:
                errors.append(f"claims[{i}] 缺少字段: {missing_claim}")
            if set(claim) - set(CLAIM_REQUIRED):
                errors.append(f"claims[{i}] 包含额外字段")
            if claim.get("claim_type") not in CLAIM_TYPES:
                errors.append(f"claims[{i}] claim_type 非法: {claim.get('claim_type')!r}")
            if claim.get("confidence") not in CONFIDENCE_LEVELS:
                errors.append(f"claims[{i}] confidence 非法: {claim.get('confidence')!r}")
            if not isinstance(claim.get("claim_text"), str) or not claim.get("claim_text"):
                errors.append(f"claims[{i}] claim_text 为空")

    for key in ("title_claim_ids", "overall_judgment_claim_ids", "required_disclosures"):
        if not isinstance(report.get(key), list):
            errors.append(f"{key} 必须是数组")
    dni = report.get("do_not_infer_compliance")
    if not isinstance(dni, list):
        errors.append("do_not_infer_compliance 必须是数组")
    else:
        for i, item in enumerate(dni):
            if not isinstance(item, dict) or not {"rule_id", "rule_text", "violated", "related_claim_ids"} <= set(item):
                errors.append(f"do_not_infer_compliance[{i}] 结构非法")
    stats = report.get("report_statistics")
    if not isinstance(stats, dict):
        errors.append("report_statistics 必须是对象")
    data_context = report.get("data_context")
    if not isinstance(data_context, dict):
        errors.append("data_context 必须是对象")
    else:
        missing_dc = [k for k in DATA_CONTEXT_REQUIRED if k not in data_context]
        if missing_dc:
            errors.append(f"data_context 缺少字段: {missing_dc}")
        extra_dc = set(data_context) - set(DATA_CONTEXT_REQUIRED)
        if extra_dc:
            errors.append(f"data_context 包含额外字段: {sorted(extra_dc)}")
        for key in (
            "active_snapshot_id",
            "coverage_version",
            "facts_cutoff",
            "poll_cutoff",
            "period_start",
            "period_end",
        ):
            if key in data_context and not isinstance(data_context[key], str):
                errors.append(f"data_context.{key} 必须是字符串")
        if not isinstance(data_context.get("uncovered_date_range"), list):
            errors.append("data_context.uncovered_date_range 必须是数组")
    return errors


def is_valid_report_schema(report: dict) -> bool:
    return not validate_report_schema(report)


# ---------------------------------------------------------------------------
# v2.0 研判单元契约（schema_version = "2.0"）
# ---------------------------------------------------------------------------

SCHEMA_VERSION_V2 = "2.0"

REPORT_V2_REQUIRED = [
    "schema_version",
    "report_id",
    "election_id",
    "report_period",
    "generation_mode",
    "report_status",
    "title",
    "conclusion_summary",
    "core_assessments",
    "appendix",
    "required_disclosures",
    "do_not_infer_compliance",
    "report_statistics",
    "data_context",
]

REFS_KEYS = ("event_ids", "poll_ids", "source_ids", "gap_ids", "dimension_ids")
REFS_KEYS_SET = set(REFS_KEYS)
CONFIDENCE_V2 = {"high", "medium", "low"}
APPENDIX_TYPES = {"background_fact", "data_limitation", "disclosure"}
DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 程序确定性派生字段（_enrich_v2_report 回填，供门禁/展示复用；模型不得依赖）。
V2_DERIVED_FIELDS = {"claims", "sections", "title_claim_ids", "overall_judgment_claim_ids"}


def _refs_errors(refs: Any, where: str) -> list[str]:
    errors: list[str] = []
    if not isinstance(refs, dict):
        return [f"{where}.evidence_refs 必须是对象"]
    extra = set(refs) - REFS_KEYS_SET
    if extra:
        errors.append(f"{where}.evidence_refs 包含额外字段: {sorted(extra)}")
    for key in REFS_KEYS:
        value = refs.get(key)
        if value is None:
            value = refs[key] = []  # 允许缺省空数组，程序侧容错
        if not isinstance(value, list) or not all(
            isinstance(item, str) for item in value
        ):
            errors.append(f"{where}.evidence_refs.{key} 必须是字符串数组")
    return errors


def validate_report_schema_v2(report: dict) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["输出不是 JSON 对象"]
    extra = set(report) - set(REPORT_V2_REQUIRED) - V2_DERIVED_FIELDS
    if extra:
        errors.append(f"顶层多余字段: {sorted(extra)}")
    missing = [k for k in REPORT_V2_REQUIRED if k not in report]
    if missing:
        errors.append(f"缺少顶层必需字段: {missing}")
    if report.get("schema_version") != SCHEMA_VERSION_V2:
        errors.append(
            f"schema_version 必须为 {SCHEMA_VERSION_V2}，实际 {report.get('schema_version')!r}"
        )
    if report.get("generation_mode") not in GENERATION_MODES:
        errors.append(f"generation_mode 非法: {report.get('generation_mode')!r}")
    if report.get("report_status") not in REPORT_STATUSES:
        errors.append(f"report_status 非法: {report.get('report_status')!r}")

    conclusion = report.get("conclusion_summary")
    if not isinstance(conclusion, list):
        errors.append("conclusion_summary 必须是数组")
    else:
        if not 1 <= len(conclusion) <= 3:
            errors.append(
                f"conclusion_summary 必须包含 1-3 条结论摘要，实际 {len(conclusion)}"
            )
        for i, item in enumerate(conclusion):
            _check_v2_conclusion_item(item, i, errors)

    assessments = report.get("core_assessments")
    if not isinstance(assessments, list):
        errors.append("core_assessments 必须是数组")
    else:
        if not 1 <= len(assessments) <= 3:
            errors.append(
                f"core_assessments 必须包含 1-3 个核心研判，实际 {len(assessments)}"
            )
        ids: list[str] = []
        for i, item in enumerate(assessments):
            if isinstance(item, dict):
                ids.append(str(item.get("assessment_id") or ""))
            _check_v2_assessment_item(item, i, errors)
        if len(ids) != len(set(ids)):
            errors.append("core_assessments 存在重复 assessment_id")

    appendix = report.get("appendix")
    if not isinstance(appendix, list):
        errors.append("appendix 必须是数组")
    else:
        for i, item in enumerate(appendix):
            if not isinstance(item, dict):
                errors.append(f"appendix[{i}] 不是对象")
                continue
            for key in ("item_id", "item_type", "item_text", "evidence_refs"):
                if key not in item:
                    errors.append(f"appendix[{i}] 缺少 {key}")
            if set(item) - {"item_id", "item_type", "item_text", "evidence_refs"}:
                errors.append(f"appendix[{i}] 包含额外字段")
            if item.get("item_type") not in APPENDIX_TYPES:
                errors.append(
                    f"appendix[{i}].item_type 非法: {item.get('item_type')!r}"
                )
            if not isinstance(item.get("item_text"), str) or not item.get("item_text"):
                errors.append(f"appendix[{i}].item_text 为空")
            errors.extend(_refs_errors(item.get("evidence_refs"), f"appendix[{i}]"))

    disclosures = report.get("required_disclosures")
    if not isinstance(disclosures, list) or not all(
        isinstance(text, str) and text.strip() for text in disclosures
    ):
        errors.append("required_disclosures 必须是非空字符串数组")
    if not isinstance(report.get("title"), str) or not report.get("title"):
        errors.append("title 为空")
    dni = report.get("do_not_infer_compliance")
    if not isinstance(dni, list):
        errors.append("do_not_infer_compliance 必须是数组")
    else:
        for i, item in enumerate(dni):
            if not isinstance(item, dict) or not {"rule_id", "rule_text", "violated", "related_claim_ids"} <= set(item):
                errors.append(f"do_not_infer_compliance[{i}] 结构非法")
    stats = report.get("report_statistics")
    if not isinstance(stats, dict):
        errors.append("report_statistics 必须是对象")
    else:
        for key in (
            "claim_count",
            "section_count",
            "core_assessment_count",
            "conclusion_summary_count",
            "evidence_item_count",
            "chinese_char_count",
        ):
            if not isinstance(stats.get(key), int):
                errors.append(f"report_statistics.{key} 必须是整数")
        if not isinstance(stats.get("length_below_target"), bool):
            errors.append("report_statistics.length_below_target 必须是布尔值")
    data_context = report.get("data_context")
    if not isinstance(data_context, dict):
        errors.append("data_context 必须是对象")
    else:
        missing_dc = [k for k in DATA_CONTEXT_REQUIRED if k not in data_context]
        if missing_dc:
            errors.append(f"data_context 缺少字段: {missing_dc}")
        extra_dc = set(data_context) - set(DATA_CONTEXT_REQUIRED)
        if extra_dc:
            errors.append(f"data_context 包含额外字段: {sorted(extra_dc)}")
        for key in (
            "active_snapshot_id",
            "coverage_version",
            "facts_cutoff",
            "poll_cutoff",
            "period_start",
            "period_end",
        ):
            if key in data_context and not isinstance(data_context[key], str):
                errors.append(f"data_context.{key} 必须是字符串")
        if not isinstance(data_context.get("uncovered_date_range"), list):
            errors.append("data_context.uncovered_date_range 必须是数组")
    return errors


def _check_v2_conclusion_item(item: Any, index: int, errors: list[str]) -> None:
    where = f"conclusion_summary[{index}]"
    if not isinstance(item, dict):
        errors.append(f"{where} 不是对象")
        return
    for key in ("summary_id", "judgment", "confidence", "evidence_refs"):
        if key not in item:
            errors.append(f"{where} 缺少 {key}")
    if set(item) - {"summary_id", "judgment", "confidence", "evidence_refs"}:
        errors.append(f"{where} 包含额外字段")
    if not isinstance(item.get("judgment"), str) or not item.get("judgment"):
        errors.append(f"{where}.judgment 为空")
    if item.get("confidence") not in CONFIDENCE_V2:
        errors.append(f"{where}.confidence 非法: {item.get('confidence')!r}")
    errors.extend(_refs_errors(item.get("evidence_refs"), where))


def _check_v2_assessment_item(item: Any, index: int, errors: list[str]) -> None:
    where = f"core_assessments[{index}]"
    if not isinstance(item, dict):
        errors.append(f"{where} 不是对象")
        return
    required = (
        "assessment_id",
        "judgment",
        "evidence_items",
        "evidence_refs",
        "reasoning",
        "falsifiers_or_limits",
        "confidence",
        "watch_indicators",
    )
    for key in required:
        if key not in item:
            errors.append(f"{where} 缺少 {key}")
    if set(item) - set(required):
        errors.append(f"{where} 包含额外字段")
    if not isinstance(item.get("judgment"), str) or not item.get("judgment"):
        errors.append(f"{where}.judgment 为空")
    if not isinstance(item.get("reasoning"), str) or not item.get("reasoning"):
        errors.append(f"{where}.reasoning 为空")
    if not isinstance(item.get("falsifiers_or_limits"), str) or not item.get(
        "falsifiers_or_limits"
    ):
        errors.append(f"{where}.falsifiers_or_limits 为空")
    if item.get("confidence") not in CONFIDENCE_V2:
        errors.append(f"{where}.confidence 非法: {item.get('confidence')!r}")
    watch = item.get("watch_indicators")
    if not isinstance(watch, list) or not watch or not all(
        isinstance(text, str) and text.strip() for text in watch
    ):
        errors.append(f"{where}.watch_indicators 必须是非空字符串数组")
    errors.extend(_refs_errors(item.get("evidence_refs"), where))

    items = item.get("evidence_items")
    if not isinstance(items, list):
        errors.append(f"{where}.evidence_items 必须是数组")
        return
    if not 2 <= len(items) <= 4:
        errors.append(
            f"{where}.evidence_items 必须包含 2-4 条最近事实证据，实际 {len(items)}"
        )
    seen: list[str] = []
    for j, ev in enumerate(items):
        if not isinstance(ev, dict):
            errors.append(f"{where}.evidence_items[{j}] 不是对象")
            continue
        for key in ("evidence_id", "evidence_date", "evidence_summary"):
            if key not in ev:
                errors.append(f"{where}.evidence_items[{j}] 缺少 {key}")
        if set(ev) - {"evidence_id", "evidence_date", "evidence_summary"}:
            errors.append(f"{where}.evidence_items[{j}] 包含额外字段")
        if not isinstance(ev.get("evidence_id"), str) or not ev.get("evidence_id"):
            errors.append(f"{where}.evidence_items[{j}].evidence_id 为空")
        if not isinstance(ev.get("evidence_summary"), str) or not ev.get(
            "evidence_summary"
        ):
            errors.append(f"{where}.evidence_items[{j}].evidence_summary 为空")
        date_value = ev.get("evidence_date")
        if not isinstance(date_value, str) or not DATE_RE.match(date_value):
            errors.append(
                f"{where}.evidence_items[{j}].evidence_date 必须是 YYYY-MM-DD 日期"
            )
        eid = str(ev.get("evidence_id") or "")
        if eid in seen:
            errors.append(f"{where}.evidence_items 存在重复 evidence_id: {eid}")
        seen.append(eid)


def is_valid_report_schema_v2(report: dict) -> bool:
    return not validate_report_schema_v2(report)
