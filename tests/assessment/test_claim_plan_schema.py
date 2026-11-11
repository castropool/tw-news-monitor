import copy

from app.assessment.claim_plan_schema import validate_claim_plan_schema


def valid_plan():
    return {
        "claim_plan_version": "1.0",
        "claim_planner_contract_version": "1.0",
        "election_id": "tainan_mayoral_2026",
        "reporting_period": {"period_start": "2026-07-16", "period_end": "2026-07-31"},
        "formal_state_hash": "formal-hash",
        "evidence_pack_hash": "pack-hash",
        "claims": [
            {
                "claim_id": "CP_S03_001",
                "target_section_id": "S03",
                "claim_type": "factual_synthesis",
                "claim_strength": "direct_fact",
                "claim_text": "陈亭妃与民进党议员拍摄联合宣传照。",
                "event_ids": ["e1"],
                "source_ids": ["s1"],
                "poll_ids": [],
                "snapshot_dimensions": [],
                "gap_ids": [],
                "evidence_reasoning_summary": "正式事件直接支持",
                "confidence": "high",
                "limitations": [],
                "material_for_report": True,
                "applies_to_period": True,
            }
        ],
        "data_limitations": ["事实覆盖不完整"],
    }


def test_claim_plan_schema_accepts_minimal_valid_plan():
    assert validate_claim_plan_schema(valid_plan()) == []


def test_claim_plan_schema_rejects_extra_fields_and_bad_id():
    value = copy.deepcopy(valid_plan())
    value["extra"] = True
    value["claims"][0]["claim_id"] = "C001"
    errors = validate_claim_plan_schema(value)
    assert any("额外" in item for item in errors)
    assert any("claim_id" in item for item in errors)

