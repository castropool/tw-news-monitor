"""Phase R1.1 Fix A regression: deterministic disclosure injection,
Event/Poll->Source mapping, final-mode disclosure validation, and org parser."""

from __future__ import annotations

from app.assessment.claim_evidence_validator import (
    _candidate_orgs,
    build_evidence_context,
    validate_structured_report,
)
from app.assessment.generate_llm_report import _apply_deterministic_fixes
from app.assessment.llm_input_contract import build_data_context

from tests.assessment.two_stage_fixtures import contract_fixture


def _claim(cid, ctype, text, *, events=(), polls=(), sources=(), dims=()):
    return {
        "claim_id": cid,
        "claim_type": ctype,
        "claim_text": text,
        "confidence": "medium",
        "material_for_report": True,
        "supporting_event_ids": list(events),
        "supporting_poll_ids": list(polls),
        "supporting_source_ids": list(sources),
        "supporting_snapshot_dimensions": list(dims),
        "supporting_gap_ids": [],
        "inference_basis": "测试",
        "limitations": [],
        "applies_to_period": True,
    }


def _report(claims, generation_mode="final"):
    disclosure_ids = [
        c["claim_id"] for c in claims if c["claim_type"] == "data_disclosure"
    ]
    return {
        "schema_version": "1.1",
        "report_id": "test",
        "election_id": "tainan_mayoral_2026",
        "report_period": {
            "period_start": "2026-07-16",
            "period_end": "2026-07-31",
        },
        "generation_mode": generation_mode,
        "report_status": "accepted",
        "title": "测试报告",
        "title_claim_ids": [],
        "overall_judgment_claim_ids": [],
        "sections": [
            {"section_id": "S01", "heading": "一、总体判断", "claim_ids": [], "section_purpose": ""},
            {
                "section_id": "S08",
                "heading": "八、证据限制",
                "claim_ids": [c["claim_id"] for c in claims],
                "section_purpose": "",
            },
        ],
        "claims": claims,
        "required_disclosures": disclosure_ids,
        "do_not_infer_compliance": [],
        "report_statistics": {},
        "data_context": {},
    }


def _final_contract():
    contract = contract_fixture()
    contract["data_status"]["facts_cutoff"] = "2026-08-08"
    contract["data_status"]["poll_cutoff"] = "2026-03-12"
    contract["data_status"]["uncovered_date_range"] = []
    contract["generation_eligibility"]["final_report_allowed"] = True
    contract["generation_eligibility"]["allowed_generation_mode"] = "final"
    return contract


def test_disclosure_injection_and_final_validation():
    contract = _final_contract()
    report = _report(
        [
            _claim("C15", "data_disclosure", "正式民调截止至 2026-03-12")
        ]
    )
    report["claims"][0]["confidence"] = "not_applicable"
    audit = _apply_deterministic_fixes(report, contract)
    assert audit["injected_disclosures"]
    texts = [c["claim_text"] for c in report["claims"]]
    assert any("正式事实底表仅覆盖至 2026-08-08" in t for t in texts)
    assert any("本期无未覆盖日期" in t for t in texts)
    assert set(report["required_disclosures"]) == {
        c["claim_id"] for c in report["claims"] if c["claim_type"] == "data_disclosure"
    }
    report["data_context"] = build_data_context(contract)
    ctx = build_evidence_context(contract, evidence_pack=None, config={})
    validation = validate_structured_report(report, ctx, expected_mode="final")
    assert validation["facts_cutoff_disclosed"] is True
    assert validation["poll_cutoff_disclosed"] is True
    assert validation["required_disclosures_complete"] is True


def test_final_mode_does_not_require_draft_disclosures():
    contract = _final_contract()
    report = _report(
        [
            _claim("C15", "data_disclosure", "正式民调截止至 2026-03-12")
        ]
    )
    report["claims"][0]["confidence"] = "not_applicable"
    _apply_deterministic_fixes(report, contract)
    report["data_context"] = build_data_context(contract)
    ctx = build_evidence_context(contract, evidence_pack=None, config={})
    report["generation_mode"] = "draft_with_data_gap"
    draft_validation = validate_structured_report(
        report, ctx, expected_mode="draft_with_data_gap"
    )
    assert draft_validation["required_disclosures_complete"] is False


def test_poll_event_source_mapping_fills_allowed_sources():
    contract = _final_contract()
    claim = _claim(
        "C01",
        "current_assessment",
        "基于正式民调与事件研判选情。",
        events=("e1",),
        polls=("p1",),
        dims=("overall_race_structure",),
    )
    report = _report([claim])
    _apply_deterministic_fixes(report, contract)
    fixed = report["claims"][0]
    assert fixed["supporting_source_ids"]
    ctx = build_evidence_context(contract, evidence_pack=None, config={})
    event_sources = set((contract["period_events"] + contract["background_events"])[0].get("source_ids") or [])
    poll_sources = set(next(p for p in contract["polls"] if p["poll_id"] == "p1").get("source_ids") or [])
    assert set(fixed["supporting_source_ids"]) <= (event_sources | poll_sources)
    validation = validate_structured_report(
        _report([fixed]), ctx, expected_mode="final"
    )
    assert validation["poll_source_relationships_valid"] is True


def test_source_mapping_only_uses_sources_present_in_pack():
    contract = _final_contract()
    poll = next(p for p in contract["polls"] if p["poll_id"] == "p1")
    known = {s["source_id"] for s in contract["sources"]}
    poll_declared = {
        sid for p in contract["polls"] for sid in (p.get("source_ids") or [])
    }
    allowed = known | poll_declared
    claim = _claim(
        "C01",
        "current_assessment",
        "基于正式民调研判选情。",
        polls=("p1",),
        sources=("src_bogus_unknown",),
        dims=("overall_race_structure",),
    )
    report = _report([claim])
    _apply_deterministic_fixes(report, contract)
    fixed_sources = set(report["claims"][0]["supporting_source_ids"])
    assert fixed_sources <= allowed
    assert "src_bogus_unknown" not in fixed_sources
    assert fixed_sources


def test_org_parser_ignores_clause_fragments():
    text = "未来半月，陈亭妃预计将持续借地方后援会成立和联合宣传巩固组织盘"
    assert _candidate_orgs(text) == []
    assert _candidate_orgs("观光产业后援会成立") == ["观光产业后援会"]
    assert _candidate_orgs("民进党提名") == ["民进党"]
