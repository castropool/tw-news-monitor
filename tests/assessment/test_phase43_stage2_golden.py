import json
from pathlib import Path

import pytest

from app.assessment.claim_plan_validator import validate_claim_plan
from app.assessment.claim_planner import build_planner_envelope
from app.assessment.final_claim_coverage_validator import validate_final_claim_coverage
from app.assessment.validated_claim_store import build_validated_claim_store
from tests.assessment.test_final_claim_coverage_validator import _draft
from tests.assessment.two_stage_fixtures import clone, contract_fixture, valid_plan


CASES = json.loads(
    (Path(__file__).parent / "golden" / "phase43_stage2_cases.json").read_text(encoding="utf-8")
)


def _store():
    contract = contract_fixture(); plan = valid_plan()
    envelope = build_planner_envelope(contract, formal_state_hash="formal-hash", evidence_pack_hash="pack-hash")
    validation = validate_claim_plan(plan, contract=contract, planner_envelope=envelope, config={})
    return build_validated_claim_store(plan, validation, input_hashes={}, prompt_hash="p", schema_hash="s", provider_metadata={}, contract=contract)


def _mutate(draft, mutation):
    if mutation == "none": return
    if mutation == "reverse_renderings": draft["claim_renderings"].reverse()
    elif mutation == "title_subset": draft["title_claim_ids"] = []
    elif mutation == "overall_subset": draft["overall_judgment_claim_ids"] = []
    elif mutation == "heading_change": draft["sections"][0]["heading"] = "总体研判"
    elif mutation == "unauthorized_render": draft["claim_renderings"].append({"claim_id":"CP_S01_999","rendered_text":"新增判断"})
    elif mutation == "missing_render": draft["claim_renderings"].pop()
    elif mutation == "duplicate_render": draft["claim_renderings"].append(clone(draft["claim_renderings"][0]))
    elif mutation == "bad_plan_hash": draft["validated_claim_plan_hash"] = "changed"
    elif mutation == "unauthorized_section_claim": draft["sections"][0]["claim_ids"].append("CP_S01_999")
    elif mutation == "missing_section_claim": draft["sections"][0]["claim_ids"].pop()
    elif mutation == "duplicate_section_claim": draft["sections"][0]["claim_ids"].append(draft["sections"][0]["claim_ids"][0])
    elif mutation == "target_section_mismatch":
        cid = draft["sections"][0]["claim_ids"].pop(); draft["sections"][1]["claim_ids"].append(cid)
    elif mutation == "title_unauthorized": draft["title_claim_ids"] = ["CP_S01_999"]
    elif mutation == "overall_unauthorized": draft["overall_judgment_claim_ids"] = ["CP_S01_999"]
    elif mutation == "strength_escalation": draft["claim_renderings"][0]["rendered_text"] = "双方确定已经全面完成整合。"
    elif mutation == "bounded_marker_removed": draft["claim_renderings"][0]["rendered_text"] = "双方竞选活动增加。"
    elif mutation == "new_date": draft["claim_renderings"][0]["rendered_text"] += " 2026-08-08。"
    elif mutation == "extra_top_field": draft["invented"] = True
    elif mutation == "empty_render": draft["claim_renderings"][0]["rendered_text"] = ""
    else: raise AssertionError(mutation)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_stage2_golden(case):
    store = _store(); draft = _draft(store); _mutate(draft, case["mutation"])
    result = validate_final_claim_coverage(draft, store)
    assert result["final_claim_coverage_ready"] is case["expected_ready"]


def test_stage2_golden_inventory_is_exactly_20_with_holdouts():
    assert len(CASES) == 20
    assert sum(case["holdout"] for case in CASES) >= 5
