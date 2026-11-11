"""Build the read-only Stage 1 Planner Envelope and request."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path

from .claim_plan_schema import load_claim_plan_schema
from .llm_input_contract import build_data_context


PROMPT_PATH = Path(__file__).resolve().parent / "prompts" / "tainan_claim_planner_v1.txt"
SECTION_DEFINITIONS = [
    {"section_id": "S01", "heading": "一、总体判断", "purpose": "overall_judgment"},
    {"section_id": "S02", "heading": "二、本期关键变化", "purpose": "key_changes"},
    {"section_id": "S03", "heading": "三、陈亭妃整合进展", "purpose": "chen_integration"},
    {"section_id": "S04", "heading": "四、谢龙介组织及竞选动作", "purpose": "hsieh_organization"},
    {"section_id": "S05", "heading": "五、蓝白合作变化", "purpose": "kmt_tpp"},
    {"section_id": "S06", "heading": "六、民调与治理议题", "purpose": "polls_governance"},
    {"section_id": "S07", "heading": "七、未来半月走势", "purpose": "forward_outlook"},
    {"section_id": "S08", "heading": "八、证据限制", "purpose": "evidence_limitations"},
]


def load_claim_planner_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def _event_projection(event: dict) -> dict:
    allowed = sorted(set(event.get("source_ids") or []))
    keep = {
        key: deepcopy(event.get(key))
        for key in (
            "event_id", "event_date", "title", "fact_summary", "event_type",
            "fact_status", "mentions", "evidence_assertions", "subevents",
        )
        if key in event
    }
    keep["allowed_source_ids"] = allowed
    return keep


def _poll_projection(poll: dict) -> dict:
    allowed = sorted(set(poll.get("source_ids") or []))
    keep = {
        key: deepcopy(poll.get(key))
        for key in (
            "poll_id", "release_date", "fieldwork_start", "fieldwork_end",
            "pollster", "sponsor", "results", "limitations",
        )
        if key in poll
    }
    keep["allowed_poll_source_ids"] = allowed
    return keep


def build_planner_envelope(
    contract: dict, *, formal_state_hash: str, evidence_pack_hash: str
) -> dict:
    return {
        "claim_planner_contract_version": "1.0",
        "input_contract_version": str(contract.get("contract_version") or ""),
        "election_id": contract.get("election_id"),
        "election_name": contract.get("election_name"),
        "reporting_period": deepcopy(contract.get("report_period") or {}),
        "formal_state_hash": formal_state_hash,
        "evidence_pack_hash": evidence_pack_hash,
        "data_context": build_data_context(contract),
        "generation_eligibility": deepcopy(contract.get("generation_eligibility") or {}),
        "sections": deepcopy(SECTION_DEFINITIONS),
        "events": [
            _event_projection(item)
            for item in (contract.get("period_events") or []) + (contract.get("background_events") or [])
        ],
        "polls": [_poll_projection(item) for item in contract.get("polls") or []],
        "sources": deepcopy(contract.get("sources") or []),
        "snapshot_dimensions": deepcopy((contract.get("state_diff") or {}).get("dimensions") or []),
        "coverage_gaps": deepcopy(contract.get("coverage_gaps") or []),
        "known_limitations": deepcopy(contract.get("known_limitations") or []),
        "do_not_infer": deepcopy(contract.get("do_not_infer") or []),
    }


def build_claim_planner_request(envelope: dict) -> dict:
    return {
        "contract_envelope_version": "claim_planner_envelope_v1",
        "planner_input": deepcopy(envelope),
        "output_contract": {
            "schema_version": "1.0",
            "json_schema": load_claim_plan_schema(),
        },
    }
