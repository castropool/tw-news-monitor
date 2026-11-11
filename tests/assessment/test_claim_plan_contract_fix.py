"""Phase R1.1 Fix B regression: attributed_statement belongs to
claim_strength, not claim_type; speaker/allegation attribution must be kept."""

from __future__ import annotations

from app.assessment.claim_evidence_semantics import validate_claim_semantics
from app.assessment.claim_evidence_validator import build_evidence_context
from app.assessment.claim_plan_schema import validate_claim_plan_schema
from app.assessment.claim_plan_validator import validate_claim_plan
from app.assessment.claim_planner import build_planner_envelope

from tests.assessment.two_stage_fixtures import contract_fixture, valid_plan


def _envelope(contract):
    return build_planner_envelope(
        contract, formal_state_hash="formal-hash", evidence_pack_hash="pack-hash"
    )


def test_b1_current_assessment_with_attributed_statement_strength_passes():
    plan = valid_plan()
    plan["claims"][0]["claim_type"] = "current_assessment"
    plan["claims"][0]["claim_strength"] = "attributed_statement"
    assert validate_claim_plan_schema(plan) == []
    contract = contract_fixture()
    result = validate_claim_plan(
        plan, contract=contract, planner_envelope=_envelope(contract), config={}
    )
    assert result["claim_plan_schema_valid"] is True
    by_id = {r["claim_id"]: r for r in result["claim_results"]}
    assert by_id["CP_S01_001"]["accepted"] is True


def test_b2_attributed_statement_as_claim_type_fails_schema():
    plan = valid_plan()
    plan["claims"][0]["claim_type"] = "attributed_statement"
    errors = validate_claim_plan_schema(plan)
    assert any("claims[0].claim_type 非法" in e for e in errors)
    contract = contract_fixture()
    result = validate_claim_plan(
        plan, contract=contract, planner_envelope=_envelope(contract), config={}
    )
    assert result["claim_plan_schema_valid"] is False
    assert result["claim_plan_status"] == "rejected"


def test_b3_actor_statement_keeps_speaker_attribution():
    contract = contract_fixture()
    contract["period_events"][0]["evidence_assertions"].append(
        {
            "assertion_id": "a_stmt",
            "assertion_type": "actor_statement",
            "text": "陈亭妃表示团队将深入37个行政区",
            "speaker": "陈亭妃",
            "source_ids": ["s1"],
        }
    )
    ctx = build_evidence_context(contract, evidence_pack=None, config={})
    claim = {
        "claim_type": "current_assessment",
        "claim_text": "陈亭妃表示团队将深入37个行政区。",
        "supporting_event_ids": ["e1"],
        "supporting_source_ids": ["s1"],
        "supporting_snapshot_dimensions": ["overall_race_structure"],
        "inference_basis": "引用陈亭妃公开表述。",
    }
    result = validate_claim_semantics(claim, ctx)
    assert "statement_as_fact" not in result["failures"]


def test_b4_allegation_must_keep_speaker_and_marker():
    contract = contract_fixture()
    contract["period_events"][1]["evidence_assertions"].append(
        {
            "assertion_id": "a_alg",
            "assertion_type": "allegation",
            "text": "谢龙介指控民进党内部未整合",
            "speaker": "谢龙介",
            "source_ids": ["s2"],
        }
    )
    ctx = build_evidence_context(contract, evidence_pack=None, config={})
    base = {
        "claim_type": "current_assessment",
        "supporting_event_ids": ["e2"],
        "supporting_source_ids": ["s2"],
        "supporting_snapshot_dimensions": ["overall_race_structure"],
        "inference_basis": "引用谢龙介公开指控。",
    }
    kept = validate_claim_semantics(
        {**base, "claim_text": "谢龙介指控民进党内部未整合。"}, ctx
    )
    assert "allegation_as_fact" not in kept["failures"]
    dropped = validate_claim_semantics(
        {**base, "claim_text": "民进党内部未整合。"}, ctx
    )
    assert "allegation_as_fact" in dropped["failures"]


def test_b5_all_legal_claim_types_pass_schema():
    for ctype in (
        "factual_synthesis",
        "current_assessment",
        "comparative_assessment",
        "forward_outlook",
        "limitation",
        "data_disclosure",
    ):
        plan = valid_plan()
        plan["claims"][0]["claim_type"] = ctype
        assert validate_claim_plan_schema(plan) == []
