"""大模型输入合同（schema_version=1.1, contract_version=1.0）。"""

from __future__ import annotations

import re
from datetime import date
from typing import Any


SCHEMA_VERSION = "1.1"
CONTRACT_VERSION = "1.0"
REPORT_SCHEMA_VERSION = "1.1"

ALLOWED_TOP_LEVEL_FIELDS = [
    "schema_version",
    "contract_version",
    "election_id",
    "election_name",
    "report_period",
    "data_status",
    "generation_eligibility",
    "current_snapshot",
    "previous_snapshot",
    "state_diff",
    "period_events",
    "background_events",
    "sources",
    "polls",
    "theme_status",
    "coverage_gaps",
    "active_research_tasks",
    "known_limitations",
    "do_not_infer",
    "evidence_statistics",
]

DATA_CONTEXT_KEYS = (
    "active_snapshot_id",
    "previous_snapshot_id",
    "coverage_version",
    "facts_cutoff",
    "poll_cutoff",
    "period_start",
    "period_end",
    "uncovered_date_range",
)


def build_data_context(contract: dict) -> dict:
    """从 LLM 输入合同构建权威 data_context（不允许模型自由填写）。"""
    data = contract.get("data_status") or {}
    rp = contract.get("report_period") or {}
    current = contract.get("current_snapshot") or {}
    previous = contract.get("previous_snapshot") or {}
    return {
        "active_snapshot_id": data.get("active_snapshot_id")
        or current.get("snapshot_id")
        or "",
        "previous_snapshot_id": previous.get("snapshot_id") or "",
        "coverage_version": data.get("coverage_version") or "",
        "facts_cutoff": data.get("facts_cutoff") or "",
        "poll_cutoff": data.get("poll_cutoff") or "",
        "period_start": rp.get("period_start") or "",
        "period_end": rp.get("period_end") or "",
        "uncovered_date_range": list(data.get("uncovered_date_range") or []),
    }

PROHIBITED_KEYS = {
    "run_id",
    "input_hashes",
    "output_hashes",
    "command",
    "database_path",
    "coverage_path",
    "events_seed",
    "sources_seed",
    "initial_snapshot",
    "snapshot_history",
    "poll_seeds",
    "coverage_dir",
    "database_business",
    "builder_version",
    "local_path",
    "tmp_dir",
}

ABS_PATH_RE = re.compile(r"(?i)(?:^|[\\/])([a-z]:[\\/]|\\\\|[\\/][\\/])")

FIELD_TYPES = {
    "schema_version": "string",
    "contract_version": "string",
    "election_id": "string",
    "election_name": "string",
    "report_period": "object",
    "data_status": "object",
    "generation_eligibility": "object",
    "current_snapshot": "object",
    "previous_snapshot": "object|null",
    "state_diff": "object",
    "period_events": "array",
    "background_events": "array",
    "sources": "array",
    "polls": "array",
    "theme_status": "array",
    "coverage_gaps": "array",
    "active_research_tasks": "array",
    "known_limitations": "array",
    "do_not_infer": "array",
    "evidence_statistics": "object",
}

ALLOWED_ENUMS = {
    "allowed_generation_mode": ["final", "draft_with_data_gap"],
    "state_diff.state_diff_mode": ["initial_baseline", "structured_comparison"],
    "evidence_role": ["period_event", "background", "period_poll", "context_poll"],
    "change_scope_item": [
        "business_state",
        "evidence_support",
        "limitations",
        "confidence",
        "metadata_only",
    ],
    "gap_change_type": [
        "resolved",
        "new",
        "narrowed",
        "widened",
        "reframed",
        "unchanged",
        "renamed",
    ],
    "risk_change_type": [
        "newly_emerged_risk",
        "existing_risk_reaffirmed",
        "existing_limitation_carried_forward",
        "risk_narrowed",
        "risk_reframed",
    ],
}

MAXIMUM_COUNTS = {
    "period_events": 500,
    "background_events": 50,
    "sources": 500,
    "polls": 100,
    "theme_status": 500,
    "coverage_gaps": 200,
    "active_research_tasks": 100,
    "known_limitations": 500,
    "do_not_infer": 500,
}


def _contract_definition() -> dict:
    return {
        "required_fields": list(ALLOWED_TOP_LEVEL_FIELDS),
        "optional_fields": [],
        "field_types": FIELD_TYPES,
        "allowed_enums": ALLOWED_ENUMS,
        "maximum_counts": MAXIMUM_COUNTS,
        "prohibited_fields": sorted(PROHIBITED_KEYS),
        "reference_integrity_rules": {
            "all_event_ids_must_exist_in_formal_events": True,
            "all_source_ids_must_exist_in_formal_sources": True,
            "all_poll_ids_must_exist_in_formal_polls": True,
            "all_event_source_pairs_must_exist_in_formal_links": True,
        },
        "generation_eligibility_rules": {
            "final_report_allowed_requires_full_facts_coverage": True,
            "draft_with_data_gap_requires_required_disclosures": True,
            "allowed_generation_mode_enum": ALLOWED_ENUMS["allowed_generation_mode"],
        },
    }


def build_llm_input_contract(pack: dict) -> dict:
    contract = {}
    for key in ALLOWED_TOP_LEVEL_FIELDS:
        value = pack.get(key)
        if key == "report_period" and isinstance(value, dict):
            # run_at 是运行元数据，不计入业务输入合同，避免缓存键随重建漂移
            value = {k: v for k, v in value.items() if k != "run_at"}
        contract[key] = value
    contract["schema_version"] = SCHEMA_VERSION
    contract["contract_version"] = CONTRACT_VERSION
    contract["contract_definition"] = _contract_definition()
    return contract


def _payload(contract: dict) -> dict:
    return {key: contract.get(key) for key in ALLOWED_TOP_LEVEL_FIELDS}


def _scan_prohibited(obj: Any, path: str = "", errors: list[str] | None = None) -> list[str]:
    errors = errors if errors is not None else []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if key in PROHIBITED_KEYS:
                errors.append(f"{path}.{key}: 禁止字段")
            if isinstance(value, str) and ABS_PATH_RE.search(value):
                errors.append(f"{path}.{key}: 包含本地绝对路径")
            _scan_prohibited(value, f"{path}.{key}", errors)
    elif isinstance(obj, list):
        for i, item in enumerate(obj):
            _scan_prohibited(item, f"{path}[{i}]", errors)
    elif isinstance(obj, str) and ABS_PATH_RE.search(obj):
        errors.append(f"{path}: 包含本地绝对路径")
    return errors


def validate_llm_input_contract(
    contract: dict,
    *,
    formal_event_ids: set[str],
    formal_source_ids: set[str],
    formal_poll_ids: set[str],
    formal_link_pairs: set[tuple[str, str]],
    authoritative_active_task_ids: list[str],
    facts_cutoff: str | None,
    period_end: date,
) -> dict:
    from .evidence_pack_builder import parse_date

    errors: list[str] = []
    warnings: list[str] = []
    payload = _payload(contract)

    if contract.get("schema_version") != SCHEMA_VERSION:
        errors.append(f"schema_version 必须为 {SCHEMA_VERSION}")
    if contract.get("contract_version") != CONTRACT_VERSION:
        errors.append(f"contract_version 必须为 {CONTRACT_VERSION}")
    missing = [k for k in ALLOWED_TOP_LEVEL_FIELDS if k not in payload or payload.get(k) is None]
    if missing:
        errors.append(f"缺少必需字段: {missing}")

    scan_target = {
        key: value for key, value in contract.items() if key != "contract_definition"
    }
    errors.extend(_scan_prohibited(scan_target))

    eligibility = payload.get("generation_eligibility") or {}
    mode = eligibility.get("allowed_generation_mode")
    if mode not in ALLOWED_ENUMS["allowed_generation_mode"]:
        errors.append(f"generation_eligibility.allowed_generation_mode 非法: {mode!r}")
    if eligibility.get("final_report_allowed") is True and mode != "final":
        errors.append("final_report_allowed=true 但 allowed_generation_mode 不是 final")
    if mode == "draft_with_data_gap":
        disclosures = eligibility.get("required_disclosures") or []
        if len(disclosures) < 3:
            errors.append("draft_with_data_gap 缺少 required_disclosures")

    fully_covered = (
        parse_date(facts_cutoff) is not None
        and period_end <= parse_date(facts_cutoff)
    )
    if eligibility.get("final_report_allowed") is True and not fully_covered:
        errors.append("facts_cutoff 未覆盖 period_end 但 final_report_allowed=true")

    event_ids = {
        e.get("event_id")
        for e in (payload.get("period_events") or []) + (payload.get("background_events") or [])
    }
    source_ids = {s.get("source_id") for s in (payload.get("sources") or [])}
    poll_ids = {p.get("poll_id") for p in (payload.get("polls") or [])}
    if not event_ids <= formal_event_ids:
        errors.append("存在正式事件集合之外的 event_id")
    if not source_ids <= formal_source_ids:
        errors.append("存在正式来源集合之外的 source_id")
    if not poll_ids <= formal_poll_ids:
        errors.append("存在正式民调集合之外的 poll_id")
    for e in (payload.get("period_events") or []) + (payload.get("background_events") or []):
        for sid in e.get("source_ids") or []:
            if (e.get("event_id"), sid) not in formal_link_pairs:
                errors.append(f"缺少正式事件来源关系 {e.get('event_id')} -> {sid}")

    task_ids = [
        t.get("research_task_id")
        for t in (payload.get("active_research_tasks") or [])
        if t.get("research_task_id")
    ]
    if sorted(task_ids) != sorted(authoritative_active_task_ids):
        errors.append(
            f"active_research_tasks 与权威任务列表不一致: {sorted(task_ids)} != "
            f"{sorted(authoritative_active_task_ids)}"
        )

    for key, limit in MAXIMUM_COUNTS.items():
        value = payload.get(key)
        if isinstance(value, list) and len(value) > limit:
            errors.append(f"{key} 数量 {len(value)} 超过上限 {limit}")

    return {
        "llm_input_contract_ready": not errors,
        "errors": errors,
        "warnings": warnings,
        "schema_version": contract.get("schema_version"),
        "contract_version": contract.get("contract_version"),
        "allowed_top_level_field_count": len(ALLOWED_TOP_LEVEL_FIELDS),
        "prohibited_field_scan": "passed" if not any("禁止字段" in e for e in errors) else "failed",
        "absolute_path_scan": "passed" if not any("绝对路径" in e for e in errors) else "failed",
        "reference_integrity_check": "passed"
        if not any(
            ("正式" in e and ("event_id" in e or "source_id" in e or "poll_id" in e or "关系" in e))
            for e in errors
        )
        else "failed",
        "generation_eligibility_check": "passed"
        if not any(
            "generation_eligibility" in e or "final_report_allowed" in e for e in errors
        )
        else "failed",
    }
