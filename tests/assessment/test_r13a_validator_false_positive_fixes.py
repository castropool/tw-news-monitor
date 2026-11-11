"""Phase R1.3A regression: proven validator false positives fixed,
true violations still rejected, structural constraints unchanged."""

from __future__ import annotations

from copy import deepcopy

import pytest

from app.assessment.claim_evidence_validator import (
    _candidate_orgs,
    _candidate_persons,
    _normalize_org_candidate,
    build_evidence_context,
    validate_structured_report,
)
from app.assessment.llm_input_contract import build_data_context

from tests.assessment.two_stage_fixtures import contract_fixture


OLD_POLL_IDS = [
    "poll_tnn_20250907_easton_rainclear",
    "poll_tnn_20250912_tvbs",
    "poll_tnn_20251029_fengcan_wave1",
    "poll_tnn_20251120_tvbs",
    "poll_tnn_20251209_fengcan_wave2",
    "poll_tnn_20251223_cnews",
    "poll_tnn_20251229_keypoll",
    "poll_tnn_20251230_fengcan_wave3",
    "poll_tnn_20251230_rainclear",
    "poll_tnn_20260228_juwen_pearson_online",
    "poll_tnn_20260312_tvbs",
]


def _final_contract():
    contract = contract_fixture()
    contract["data_status"]["facts_cutoff"] = "2026-08-08"
    contract["data_status"]["poll_cutoff"] = "2026-03-12"
    contract["data_status"]["uncovered_date_range"] = []
    contract["generation_eligibility"]["final_report_allowed"] = True
    contract["generation_eligibility"]["allowed_generation_mode"] = "final"
    extra_polls = [
        {
            "poll_id": pid,
            "release_date": "2026-01-10",
            "fieldwork_end": "2025-12-30",
            "pollster": "TVBS民意调查中心",
            "sponsor": "TVBS",
            "source_ids": ["s3"],
            "results": [],
        }
        for pid in OLD_POLL_IDS
    ]
    contract["polls"] = contract["polls"] + extra_polls
    contract["polls"][0]["fieldwork_end"] = "2026-03-12"
    return contract


def _claim(cid, ctype, text, *, events=(), polls=(), sources=(), dims=(), gaps=(), basis="测试"):
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
        "supporting_gap_ids": list(gaps),
        "inference_basis": basis,
        "limitations": [],
        "applies_to_period": True,
    }


def _report(claims):
    return {
        "schema_version": "1.1",
        "report_id": "test-r13a",
        "election_id": "tainan_mayoral_2026",
        "report_period": {"period_start": "2026-07-16", "period_end": "2026-07-31"},
        "generation_mode": "final",
        "report_status": "generated",
        "title": "测试报告",
        "title_claim_ids": [],
        "overall_judgment_claim_ids": [],
        "sections": [
            {"section_id": "S01", "heading": "一、总体判断", "claim_ids": [], "section_purpose": ""},
            {"section_id": "S08", "heading": "八、证据限制", "claim_ids": [], "section_purpose": ""},
        ],
        "claims": claims,
        "required_disclosures": [],
        "do_not_infer_compliance": [],
        "report_statistics": {},
        "data_context": {},
    }


def _ctx(contract):
    return build_evidence_context(contract, evidence_pack=None, config={})


C04_TEXT = (
    "民进党结构性优势仍在，但判断依据为民调历史序列和党组织能力，而非本期新数据；"
    "最新可比民调截止于2026年3月12日，尚不能代表实时选情。"
)
C07_TEXT = (
    "整体判断受限于两大结构性缺口：一是最新正式民调结束于2026年3月12日；"
    "二是2026年2月至7月竞选活动覆盖仍不完整，尤其是谢龙介组织和蓝白合作后续。"
)
C26_TEXT = "2026年3月12日以后缺少可比的公开追踪民调，禁止据此推断实时支持度变化。"


@pytest.mark.parametrize("poll_id", OLD_POLL_IDS)
def test_r12a_poll_false_positive_c04_no_longer_flagged(poll_id):
    contract = _final_contract()
    claim = _claim(
        "C04", "current_assessment", C04_TEXT,
        events=("bg1",), polls=(poll_id,), sources=("s3",), dims=("overall_race_structure",),
    )
    report = _report([claim])
    report["data_context"] = build_data_context(contract)
    validation = validate_structured_report(report, _ctx(contract), expected_mode="final")
    assert validation["no_unsupported_poll_claims"] is True


def test_r12a_poll_false_positive_c07_limitation_no_longer_flagged():
    contract = _final_contract()
    claim = _claim(
        "C07", "limitation", C07_TEXT,
        polls=("poll_tnn_20260312_tvbs",), sources=("s3",), gaps=("gap_polling",),
    )
    report = _report([claim])
    report["data_context"] = build_data_context(contract)
    validation = validate_structured_report(report, _ctx(contract), expected_mode="final")
    assert validation["no_unsupported_poll_claims"] is True


def test_r12a_poll_false_positive_c26_negation_no_longer_flagged():
    contract = _final_contract()
    claim = _claim(
        "C26", "limitation", C26_TEXT,
        polls=("poll_tnn_20260312_tvbs",), sources=("s3",), gaps=("gap_polling",),
    )
    report = _report([claim])
    report["data_context"] = build_data_context(contract)
    validation = validate_structured_report(report, _ctx(contract), expected_mode="final")
    assert validation["no_unsupported_poll_claims"] is True


TRUE_VIOLATIONS = [
    "最新民调显示当前支持率 45%，陈亭妃领先。",
    "目前民调支持率稳定在 4 成以上，选情未变。",
    "实时支持率约为 42%，足以说明优势扩大。",
    "最新民调结果证明胜选机率高。",
    "2026年3月12日的最新民调显示当前支持率为45%。",
]


@pytest.mark.parametrize("text", TRUE_VIOLATIONS)
def test_true_poll_violations_still_rejected(text):
    contract = _final_contract()
    claim = _claim(
        "C99", "current_assessment", text,
        polls=("poll_tnn_20260312_tvbs",), sources=("s3",), dims=("overall_race_structure",),
    )
    report = _report([claim])
    report["data_context"] = build_data_context(contract)
    validation = validate_structured_report(report, _ctx(contract), expected_mode="final")
    assert validation["no_unsupported_poll_claims"] is False
    assert any("被写成当前实时支持率" in e for e in validation["errors"])


def test_zhang_xinshi_person_parse_noise_removed():
    text = "谢龙介持续利用媒体专访攻击绿营分裂，但缺乏组织扩张新事实；竞选主轴确定为“绿营裂痕＋四年改革＋基本盘”。"
    candidates = _candidate_persons(text)
    assert "张新事" not in candidates
    assert "谢龙介" in candidates

    contract = _final_contract()
    claim = _claim(
        "C15", "current_assessment", text,
        events=("e2",), sources=("s2",), dims=("overall_race_structure",),
    )
    report = _report([claim])
    report["data_context"] = build_data_context(contract)
    validation = validate_structured_report(report, _ctx(contract), expected_mode="final")
    assert validation["person_names_grounded"] is True


def test_org_variant_normalized_against_evidence_pack():
    contract = _final_contract()
    contract["period_events"].append(
        {
            "event_id": "evt_tnn_20260727_chen_tourism_support_association",
            "event_date": "2026-07-27",
            "title": "陈亭妃出席大台南观光产业后援会成立大会并提五大观光轴",
            "source_ids": ["s1"],
            "mentions": [{"mention_name": "陈亭妃"}],
            "subevents": [],
        }
    )
    assert _normalize_org_candidate("本期陈亭妃观光产业后援会") == "大台南观光产业后援会"
    candidates = _candidate_orgs("本期陈亭妃观光产业后援会成立，显示组织动员扩展至产业后援会。")
    assert candidates
    claim = _claim(
        "C10", "comparative_assessment",
        "本期陈亭妃观光产业后援会成立，显示组织动员扩展至产业后援会，但该动作尚未改变基本盘结构。",
        events=("evt_tnn_20260727_chen_tourism_support_association",),
        sources=("s1",), dims=("overall_race_structure",),
    )
    report = _report([claim])
    report["data_context"] = build_data_context(contract)
    validation = validate_structured_report(report, _ctx(contract), expected_mode="final")
    assert validation["organization_names_grounded"] is True


def test_zeng_biaoshi_attribution_parse_noise_removed():
    text = "谢龙介曾表示市长支持由中央决定。"
    candidates = _candidate_persons(text)
    assert "曾表示" not in candidates
    assert "谢龙介" in candidates

    contract = _final_contract()
    claim = _claim(
        "C_KMTTPP_001", "current_assessment", text,
        events=("bg1",), sources=("s3",), dims=("kmt_tpp_cooperation",),
    )
    report = _report([claim])
    report["data_context"] = build_data_context(contract)
    validation = validate_structured_report(report, _ctx(contract), expected_mode="final")
    assert validation["person_names_grounded"] is True


def test_org_absence_phrase_no_longer_extracted():
    text = "本期未出现谢龙介竞选总部成立、大规模募款或议员联合竞选事件。"
    candidates = _candidate_orgs(text)
    assert not any("竞选总部" in c for c in candidates)

    contract = _final_contract()
    claim = _claim(
        "C_HSIEH_003", "current_assessment", text,
        events=("e2",), sources=("s2",), dims=("overall_race_structure",),
    )
    report = _report([claim])
    report["data_context"] = build_data_context(contract)
    validation = validate_structured_report(report, _ctx(contract), expected_mode="final")
    assert validation["organization_names_grounded"] is True


def test_outside_evidence_event_still_rejected():
    contract = _final_contract()
    claim = _claim(
        "C21", "current_assessment",
        "治理议题攻防仍集中在三爷溪淹水，但缺口限制完整评估。",
        events=("evt_tnn_20260626_sanye_creek_flooding_campaign_conflict",),
        sources=(), dims=("overall_race_structure",),
    )
    report = _report([claim])
    report["data_context"] = build_data_context(contract)
    validation = validate_structured_report(report, _ctx(contract), expected_mode="final")
    assert validation["all_event_ids_exist"] is False


def test_non_atomic_claim_still_rejected():
    contract = _final_contract()
    claim = _claim(
        "C04", "current_assessment", C04_TEXT,
        events=("e1", "bg1"), sources=("s1", "s3"), dims=("overall_race_structure",),
        polls=("poll_tnn_20260312_tvbs",),
    )
    report = _report([claim])
    report["data_context"] = build_data_context(contract)
    validation = validate_structured_report(report, _ctx(contract), expected_mode="final")
    assert validation["claim_semantics_valid"] is False
    results = {r["claim_id"]: r for r in validation["claim_semantic_results"]}
    assert "claim_not_atomic" in results["C04"]["failures"]


def test_missing_inference_basis_still_rejected():
    contract = _final_contract()
    claim = _claim(
        "C17", "current_assessment",
        "本期无蓝白合作新证据，维持提案阶段状态。",
        events=("bg1",), sources=("s3",), dims=("kmt_tpp_cooperation",), basis="",
    )
    report = _report([claim])
    report["data_context"] = build_data_context(contract)
    validation = validate_structured_report(report, _ctx(contract), expected_mode="final")
    results = {r["claim_id"]: r for r in validation["claim_semantic_results"]}
    assert "missing_inference_basis" in results["C17"]["failures"]
