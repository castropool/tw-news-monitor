import json
from pathlib import Path

import pytest

from app.assessment.claim_plan_validator import validate_claim_plan
from app.assessment.claim_planner import build_planner_envelope
from tests.assessment.two_stage_fixtures import clone, contract_fixture, valid_plan


CASES = json.loads(
    (Path(__file__).parent / "golden" / "phase43_stage1_cases.json").read_text(encoding="utf-8")
)


def _find(plan, claim_id):
    return next(item for item in plan["claims"] if item["claim_id"] == claim_id)


def _mutate(plan, mutation):
    if mutation == "none": return
    if mutation == "remove_s05": plan["claims"] = [c for c in plan["claims"] if c["target_section_id"] != "S05"]
    elif mutation == "remove_s06": plan["claims"] = [c for c in plan["claims"] if c["target_section_id"] != "S06"]
    elif mutation == "bad_event": _find(plan, "CP_S02_001")["event_ids"] = ["event-outside-allowlist"]
    elif mutation == "bad_source": _find(plan, "CP_S02_001")["source_ids"] = ["source-outside-allowlist"]
    elif mutation == "unlinked_source": _find(plan, "CP_S02_001")["source_ids"] = ["s2"]
    elif mutation == "duplicate_id": plan["claims"][1]["claim_id"] = plan["claims"][0]["claim_id"]
    elif mutation == "bad_formal_hash": plan["formal_state_hash"] = "changed"
    elif mutation == "bad_pack_hash": plan["evidence_pack_hash"] = "changed"
    elif mutation == "bad_election": plan["election_id"] = "another-election"
    elif mutation == "bad_period": plan["reporting_period"]["period_end"] = "2026-08-01"
    elif mutation == "unsupported_strength": _find(plan, "CP_S02_001")["claim_strength"] = "unsupported"
    elif mutation == "forward_strong": _find(plan, "CP_S07_001")["claim_strength"] = "strong_inference"
    elif mutation == "factual_no_evidence":
        c = _find(plan, "CP_S02_001"); c["event_ids"] = []; c["source_ids"] = []
    elif mutation == "current_insufficient":
        c = _find(plan, "CP_S01_001"); c["event_ids"] = ["e1"]; c["source_ids"] = ["s1"]; c["snapshot_dimensions"] = []
    elif mutation == "comparative_no_dimension": _find(plan, "CP_S05_001")["snapshot_dimensions"] = []
    elif mutation == "section_id_mismatch": _find(plan, "CP_S03_001")["target_section_id"] = "S04"
    elif mutation == "extra_field": plan["claims"][0]["invented"] = True
    elif mutation == "missing_s03": plan["claims"] = [c for c in plan["claims"] if c["target_section_id"] != "S03"]
    elif mutation == "poll_missing_source": _find(plan, "CP_S06_001")["source_ids"] = []
    else: raise AssertionError(mutation)


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_stage1_golden(case):
    contract = contract_fixture()
    plan = clone(valid_plan())
    _mutate(plan, case["mutation"])
    envelope = build_planner_envelope(
        contract, formal_state_hash="formal-hash", evidence_pack_hash="pack-hash"
    )
    result = validate_claim_plan(plan, contract=contract, planner_envelope=envelope, config={})
    assert result["claim_plan_status"] == case["expected_status"]


def test_stage1_golden_inventory_is_exactly_20_with_holdouts():
    assert len(CASES) == 20
    assert sum(case["holdout"] for case in CASES) >= 5
