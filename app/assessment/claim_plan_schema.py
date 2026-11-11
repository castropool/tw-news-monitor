"""Strict internal Schemas for the Phase 4.3 two-stage pipeline."""

from __future__ import annotations

import json
import re
from pathlib import Path


SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"
CLAIM_PLAN_SCHEMA_PATH = SCHEMA_DIR / "claim_plan_v1.schema.json"
STAGE2_SCHEMA_PATH = SCHEMA_DIR / "stage2_report_draft_v1.schema.json"
SECTION_IDS = tuple(f"S{i:02d}" for i in range(1, 9))
CLAIM_ID_RE = re.compile(r"^CP_(S0[1-8])_[0-9]{3}$")
CLAIM_TYPES = {
    "factual_synthesis", "current_assessment", "comparative_assessment",
    "forward_outlook", "limitation", "data_disclosure",
}
CLAIM_STRENGTHS = {
    "direct_fact", "attributed_statement", "bounded_inference",
    "strong_inference", "unsupported",
}
CONFIDENCE = {"high", "medium", "low", "not_applicable"}
PLAN_TOP = {
    "claim_plan_version", "claim_planner_contract_version", "election_id",
    "reporting_period", "formal_state_hash", "evidence_pack_hash", "claims",
    "data_limitations",
}
PLAN_CLAIM = {
    "claim_id", "target_section_id", "claim_type", "claim_strength",
    "claim_text", "event_ids", "source_ids", "poll_ids",
    "snapshot_dimensions", "gap_ids", "evidence_reasoning_summary",
    "confidence", "limitations", "material_for_report", "applies_to_period",
}
STAGE2_TOP = {
    "stage2_draft_version", "report_writer_stage2_contract_version",
    "validated_claim_plan_hash", "title", "title_claim_ids",
    "overall_judgment_claim_ids", "sections", "claim_renderings",
}
SECTION_FIELDS = {"section_id", "heading", "claim_ids", "section_purpose"}
RENDER_FIELDS = {"claim_id", "rendered_text"}


def load_claim_plan_schema() -> dict:
    return json.loads(CLAIM_PLAN_SCHEMA_PATH.read_text(encoding="utf-8"))


def load_stage2_draft_schema() -> dict:
    return json.loads(STAGE2_SCHEMA_PATH.read_text(encoding="utf-8"))


def _array(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def validate_claim_plan_schema(value: dict) -> list[str]:
    if not isinstance(value, dict):
        return ["Claim Plan 必须是对象"]
    errors: list[str] = []
    missing = PLAN_TOP - set(value)
    extra = set(value) - PLAN_TOP
    if missing:
        errors.append(f"缺少顶层字段: {sorted(missing)}")
    if extra:
        errors.append(f"顶层包含额外字段: {sorted(extra)}")
    if value.get("claim_plan_version") != "1.0":
        errors.append("claim_plan_version 必须为 1.0")
    if value.get("claim_planner_contract_version") != "1.0":
        errors.append("claim_planner_contract_version 必须为 1.0")
    claims = value.get("claims")
    if not isinstance(claims, list):
        errors.append("claims 必须是数组")
        claims = []
    if not _array(value.get("data_limitations")):
        errors.append("data_limitations 必须是字符串数组")
    for index, claim in enumerate(claims):
        if not isinstance(claim, dict):
            errors.append(f"claims[{index}] 必须是对象")
            continue
        missing_claim = PLAN_CLAIM - set(claim)
        extra_claim = set(claim) - PLAN_CLAIM
        if missing_claim:
            errors.append(f"claims[{index}] 缺少字段: {sorted(missing_claim)}")
        if extra_claim:
            errors.append(f"claims[{index}] 包含额外字段: {sorted(extra_claim)}")
        match = CLAIM_ID_RE.fullmatch(str(claim.get("claim_id") or ""))
        if not match:
            errors.append(f"claims[{index}].claim_id 格式非法")
        elif match.group(1) != claim.get("target_section_id"):
            errors.append(f"claims[{index}].claim_id 与 target_section_id 不一致")
        if claim.get("target_section_id") not in SECTION_IDS:
            errors.append(f"claims[{index}].target_section_id 非法")
        if claim.get("claim_type") not in CLAIM_TYPES:
            errors.append(f"claims[{index}].claim_type 非法")
        if claim.get("claim_strength") not in CLAIM_STRENGTHS:
            errors.append(f"claims[{index}].claim_strength 非法")
        if claim.get("confidence") not in CONFIDENCE:
            errors.append(f"claims[{index}].confidence 非法")
        if not isinstance(claim.get("claim_text"), str) or not claim.get("claim_text"):
            errors.append(f"claims[{index}].claim_text 为空")
        for key in ("event_ids", "source_ids", "poll_ids", "snapshot_dimensions", "gap_ids", "limitations"):
            if not _array(claim.get(key)):
                errors.append(f"claims[{index}].{key} 必须是字符串数组")
        for key in ("material_for_report", "applies_to_period"):
            if not isinstance(claim.get(key), bool):
                errors.append(f"claims[{index}].{key} 必须是布尔值")
    return errors


def validate_stage2_draft_schema(value: dict) -> list[str]:
    if not isinstance(value, dict):
        return ["Stage 2 Draft 必须是对象"]
    errors: list[str] = []
    missing = STAGE2_TOP - set(value)
    extra = set(value) - STAGE2_TOP
    if missing:
        errors.append(f"缺少顶层字段: {sorted(missing)}")
    if extra:
        errors.append(f"顶层包含额外字段: {sorted(extra)}")
    if value.get("stage2_draft_version") != "1.0":
        errors.append("stage2_draft_version 必须为 1.0")
    if value.get("report_writer_stage2_contract_version") != "1.0":
        errors.append("report_writer_stage2_contract_version 必须为 1.0")
    if not isinstance(value.get("title"), str) or not value.get("title"):
        errors.append("title 为空")
    for key in ("title_claim_ids", "overall_judgment_claim_ids"):
        if not _array(value.get(key)):
            errors.append(f"{key} 必须是字符串数组")
    sections = value.get("sections")
    if not isinstance(sections, list):
        errors.append("sections 必须是数组")
        sections = []
    section_ids = [item.get("section_id") for item in sections if isinstance(item, dict)]
    if tuple(section_ids) != SECTION_IDS:
        errors.append(f"sections.section_id 必须依次为 {list(SECTION_IDS)}")
    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            errors.append(f"sections[{index}] 必须是对象")
            continue
        if set(section) - SECTION_FIELDS:
            errors.append(f"sections[{index}] 包含额外字段")
        if SECTION_FIELDS - set(section):
            errors.append(f"sections[{index}] 缺少字段")
        if not _array(section.get("claim_ids")):
            errors.append(f"sections[{index}].claim_ids 必须是字符串数组")
    renderings = value.get("claim_renderings")
    if not isinstance(renderings, list):
        errors.append("claim_renderings 必须是数组")
        renderings = []
    for index, item in enumerate(renderings):
        if not isinstance(item, dict):
            errors.append(f"claim_renderings[{index}] 必须是对象")
            continue
        if set(item) - RENDER_FIELDS:
            errors.append(f"claim_renderings[{index}] 包含额外字段")
        if RENDER_FIELDS - set(item):
            errors.append(f"claim_renderings[{index}] 缺少字段")
        if not isinstance(item.get("rendered_text"), str) or not item.get("rendered_text"):
            errors.append(f"claim_renderings[{index}].rendered_text 为空")
    return errors
