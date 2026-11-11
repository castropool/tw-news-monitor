"""Immutable run boundary between Claim Planner and Stage 2 Writer."""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from pathlib import Path

from .claim_plan_validator import claim_business_hash


DISCLOSURES = {
    "S05": "现有正式证据不足以支持本期蓝白合作变化的进一步判断。",
    "S06": "本期新增正式民调或治理事实不足以支持进一步判断。",
}


def _hash(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


def _disclosure(section_id: str) -> dict:
    return {
        "claim_id": f"CP_{section_id}_900",
        "target_section_id": section_id,
        "claim_type": "limitation",
        "claim_strength": "direct_fact",
        "claim_text": DISCLOSURES[section_id],
        "event_ids": [], "source_ids": [], "poll_ids": [],
        "snapshot_dimensions": [], "gap_ids": [],
        "evidence_reasoning_summary": "权威输入的数据限制要求披露证据不足",
        "confidence": "not_applicable",
        "limitations": [DISCLOSURES[section_id]],
        "material_for_report": False,
        "applies_to_period": True,
    }


def build_validated_claim_store(
    raw_plan: dict,
    validation: dict,
    *,
    input_hashes: dict,
    prompt_hash: str,
    schema_hash: str,
    provider_metadata: dict,
    contract: dict | None = None,
) -> dict:
    accepted = deepcopy(validation.get("accepted_claims") or [])
    deterministic: list[dict] = []
    if validation.get("claim_plan_status") in {"accepted", "accepted_with_rejections"}:
        for sid, coverage in (validation.get("section_coverage") or {}).items():
            if coverage.get("deterministic_disclosure_required") is True:
                item = _disclosure(sid)
                accepted.append(item)
                deterministic.append(item)
        existing_texts = {str(item.get("claim_text") or "") for item in accepted}
        required = list(((contract or {}).get("generation_eligibility") or {}).get("required_disclosures") or [])
        for offset, text in enumerate(required, 910):
            if text in existing_texts:
                continue
            item = {
                "claim_id": f"CP_S08_{offset}",
                "target_section_id": "S08",
                "claim_type": "data_disclosure",
                "claim_strength": "direct_fact",
                "claim_text": str(text),
                "event_ids": [], "source_ids": [], "poll_ids": [],
                "snapshot_dimensions": [], "gap_ids": [],
                "evidence_reasoning_summary": "权威 generation_eligibility 披露",
                "confidence": "not_applicable", "limitations": [str(text)],
                "material_for_report": False, "applies_to_period": True,
            }
            accepted.append(item)
            deterministic.append(item)
            existing_texts.add(str(text))
        data_status = (contract or {}).get("data_status") or {}
        if ((contract or {}).get("evidence_statistics") or {}).get("poll_gap") is True:
            poll_cutoff = str(data_status.get("poll_cutoff") or "").strip()
            poll_disclosure = (
                f"本期没有新增正式民调；正式民调截止至 {poll_cutoff}。"
                if poll_cutoff else "本期没有新增正式民调。"
            )
            if poll_disclosure not in existing_texts:
                item = {
                    "claim_id": "CP_S08_950",
                    "target_section_id": "S08",
                    "claim_type": "data_disclosure",
                    "claim_strength": "direct_fact",
                    "claim_text": poll_disclosure,
                    "event_ids": [], "source_ids": [], "poll_ids": [],
                    "snapshot_dimensions": [], "gap_ids": [],
                    "evidence_reasoning_summary": "权威 data_status 与 evidence_statistics 披露",
                    "confidence": "not_applicable", "limitations": [poll_disclosure],
                    "material_for_report": False, "applies_to_period": True,
                }
                accepted.append(item)
                deterministic.append(item)
                existing_texts.add(poll_disclosure)
    accepted_records = [
        {**item, "claim_business_hash": claim_business_hash(item)} for item in accepted
    ]
    rejected = deepcopy(validation.get("rejected_claims") or [])
    business = {
        "raw_plan": raw_plan,
        "accepted_claims": accepted_records,
        "rejected_claims": rejected,
        "section_coverage": validation.get("section_coverage") or {},
        "claim_plan_status": validation.get("claim_plan_status"),
    }
    return {
        "validated_claim_store_version": "1.0",
        "claim_plan_status": validation.get("claim_plan_status"),
        "claim_validation_status": validation.get("claim_validation_status"),
        "raw_claim_plan": deepcopy(raw_plan),
        "all_generated_claims": deepcopy(raw_plan.get("claims") or []),
        "accepted_claims": accepted_records,
        "rejected_claims": rejected,
        "claim_results": deepcopy(validation.get("claim_results") or []),
        "section_coverage": deepcopy(validation.get("section_coverage") or {}),
        "coverage_errors": deepcopy(validation.get("coverage_errors") or []),
        "deterministic_disclosures": deterministic,
        "deterministic_disclosure_count": len(deterministic),
        "input_hashes": deepcopy(input_hashes),
        "prompt_hash": prompt_hash,
        "schema_hash": schema_hash,
        "provider_metadata": deepcopy(provider_metadata),
        "claim_plan_business_hash": _hash(business),
        "semantic_repair_performed": False,
        "claim_split_performed": False,
        "llm_repair_call_count": 0,
    }


def write_validated_claim_store(path: Path, store: dict) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(store, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    temporary.replace(path)
    return path


def build_stage2_input(store: dict, *, data_context: dict) -> dict:
    return {
        "report_writer_stage2_contract_version": "1.0",
        "validated_claim_plan_hash": store["claim_plan_business_hash"],
        "validated_claims": [
            {
                key: deepcopy(item.get(key))
                for key in (
                    "claim_id", "target_section_id", "claim_type", "claim_strength",
                    "claim_text", "evidence_reasoning_summary", "confidence",
                    "limitations", "material_for_report", "applies_to_period",
                )
            }
            for item in store.get("accepted_claims") or []
        ],
        "data_context": deepcopy(data_context),
        "fixed_section_ids": [f"S{i:02d}" for i in range(1, 9)],
        "data_limitations": deepcopy((store.get("raw_claim_plan") or {}).get("data_limitations") or []),
    }
