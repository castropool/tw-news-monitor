import copy

import pytest

from app.assessment.claim_evidence_semantics import validate_claim_semantics
from app.assessment.claim_evidence_validator import build_evidence_context
from tests.assessment.llm.conftest import build_contract, make_report


def _base():
    contract = build_contract()
    contract["period_events"][0]["evidence_assertions"] = [
        {
            "assertion_id": "e1_fact",
            "assertion_type": "observed_fact",
            "text": "陈亭妃与民进党议员拍摄联合宣传照",
            "speaker": None,
            "source_ids": ["s1"],
        }
    ]
    contract["period_events"][1]["evidence_assertions"] = [
        {
            "assertion_id": "e2_fact",
            "assertion_type": "observed_fact",
            "text": "谢龙介在安南区进行庙口拜票",
            "speaker": None,
            "source_ids": ["s2"],
        }
    ]
    report = make_report(contract)
    claim = next(item for item in report["claims"] if item["claim_id"] == "C007")
    return contract, copy.deepcopy(claim)


def _case(case_id, split, expected, mutate):
    return pytest.param(split, expected, mutate, id=case_id)


CASES = [
    _case("cal01_direct_fact", "calibration", True, lambda c, k: None),
    _case("cal02_one_event_multi_source", "calibration", True, lambda c, k: (k["period_events"][0].update(source_ids=["s1", "s2"]), k["sources"].append({"source_id": "s2", "publisher": "东森", "title": "t"}), k["period_events"][0]["evidence_assertions"][0].update(source_ids=["s1", "s2"]), c.update(supporting_source_ids=["s1", "s2"]))),
    _case("cal03_multi_event_bounded_analysis", "calibration", True, lambda c, k: c.update(claim_type="current_assessment", claim_text="基于两项正式动作，研判双方组织活动正在增加。", supporting_event_ids=["e1", "e2"], supporting_source_ids=["s1", "s2"], supporting_snapshot_dimensions=["overall_race_structure"], inference_basis="两项正式事件共同支持")),
    _case("cal04_statement_attributed", "calibration", True, lambda c, k: (k["period_events"][0].update(fact_status="candidate_claim", evidence_assertions=[{"assertion_id": "st1", "assertion_type": "actor_statement", "text": "蓝白合作已有进展", "speaker": "谢龙介", "source_ids": ["s1"]}]), c.update(claim_text="谢龙介公开表示蓝白合作已有进展。"))),
    _case("cal05_statement_as_fact", "calibration", False, lambda c, k: (k["period_events"][0].update(fact_status="candidate_claim", evidence_assertions=[{"assertion_id": "st1", "assertion_type": "actor_statement", "text": "蓝白合作已有进展", "speaker": "谢龙介", "source_ids": ["s1"]}]), c.update(claim_text="蓝白合作已有进展。"))),
    _case("cal06_allegation_attributed", "calibration", True, lambda c, k: (k["period_events"][0].update(event_type="scandal_allegation", evidence_assertions=[{"assertion_id": "al1", "assertion_type": "allegation", "text": "乙收受不当利益", "speaker": "甲", "source_ids": ["s1"]}]), c.update(claim_text="甲公开指控乙收受不当利益。"))),
    _case("cal07_allegation_as_fact", "calibration", False, lambda c, k: (k["period_events"][0].update(event_type="scandal_allegation", evidence_assertions=[{"assertion_id": "al1", "assertion_type": "allegation", "text": "乙收受不当利益", "speaker": "甲", "source_ids": ["s1"]}]), c.update(claim_text="乙收受不当利益。"))),
    _case("cal08_missing_evidence", "calibration", False, lambda c, k: c.update(supporting_event_ids=[], supporting_source_ids=[])),
    _case("cal09_invalid_event", "calibration", False, lambda c, k: c.update(supporting_event_ids=["missing"])),
    _case("cal10_invalid_source", "calibration", False, lambda c, k: c.update(supporting_source_ids=["missing"])),
    _case("cal11_overstatement", "calibration", False, lambda c, k: c.update(claim_type="current_assessment", claim_text="陈亭妃已经全面完成组织整合。", supporting_snapshot_dimensions=["overall_race_structure"], inference_basis="单一宣传照")),
    _case("cal12_compound_claim", "calibration", False, lambda c, k: c.update(claim_text="陈亭妃完成整合；谢龙介组织成熟；因此选情已经逆转。")),
    _case("cal13_temporal_mismatch", "calibration", False, lambda c, k: c.update(claim_text="2026-07-25陈亭妃与民进党议员拍摄联合宣传照。")),
    _case("cal14_actor_mismatch", "calibration", False, lambda c, k: c.update(claim_text="谢龙介与民进党议员拍摄联合宣传照。")),
    _case("cal15_source_only_supports_statement", "calibration", False, lambda c, k: (k["period_events"][0].update(fact_status="candidate_claim", evidence_assertions=[{"assertion_id": "st2", "assertion_type": "actor_statement", "text": "整合已经完成", "speaker": "甲", "source_ids": ["s1"]}]), c.update(claim_text="整合已经完成。"))),
    _case("cal16_analysis_two_events", "calibration", True, lambda c, k: c.update(claim_type="current_assessment", claim_text="基于两项正式动作，研判竞选活动正在升温。", supporting_event_ids=["e1", "e2"], supporting_source_ids=["s1", "s2"], supporting_snapshot_dimensions=["overall_race_structure"], inference_basis="e1与e2共同构成判断基础")),
    _case("cal17_analysis_no_fact", "calibration", False, lambda c, k: c.update(claim_type="current_assessment", claim_text="研判选情已经逆转。", supporting_event_ids=[], supporting_source_ids=[], supporting_snapshot_dimensions=[], inference_basis="")),
    _case("cal18_bounded_conclusion", "calibration", True, lambda c, k: c.update(claim_type="current_assessment", claim_text="宣传照显示联合动员有所增加，但不足以证明全面整合。", supporting_snapshot_dimensions=["overall_race_structure"], inference_basis="单一事件，仅作限定判断")),
    _case("cal19_strong_weak_evidence", "calibration", False, lambda c, k: c.update(claim_type="current_assessment", claim_text="单一活动确定已经改变胜负格局。", supporting_snapshot_dimensions=["overall_race_structure"], inference_basis="单一事件")),
    _case("cal20_section_claim_consistency", "calibration", True, lambda c, k: c.update(claim_text="陈亭妃与民进党议员拍摄联合宣传照。")),
    _case("hold01_latest_formal_poll_disclosure", "holdout", True, lambda c, k: c.update(claim_type="data_disclosure", claim_text="最新正式民调调查截止于2026-03-12，不代表7月底实时支持率。", supporting_event_ids=[], supporting_poll_ids=["p1"], supporting_source_ids=["s3"], confidence="not_applicable", material_for_report=False)),
    _case("hold02_realtime_old_poll", "holdout", False, lambda c, k: c.update(claim_type="current_assessment", claim_text="最新实时民调显示当前支持率为41.0%。", supporting_event_ids=[], supporting_poll_ids=["p1"], supporting_source_ids=["s3"], supporting_snapshot_dimensions=[], inference_basis="")),
    _case("hold03_bipartite_sources", "holdout", True, lambda c, k: c.update(claim_type="current_assessment", claim_text="基于两项正式动作，研判竞选活动增加。", supporting_event_ids=["e1", "e2"], supporting_source_ids=["s1", "s2"], supporting_snapshot_dimensions=["overall_race_structure"], inference_basis="两项事件")),
    _case("hold04_uncovered_event_source", "holdout", False, lambda c, k: c.update(claim_type="current_assessment", claim_text="基于两项正式动作，研判竞选活动增加。", supporting_event_ids=["e1", "e2"], supporting_source_ids=["s1"], supporting_snapshot_dimensions=["overall_race_structure"], inference_basis="两项事件")),
    _case("hold05_person_substring", "holdout", True, lambda c, k: c.update(claim_type="current_assessment", claim_text="后续研判仍依赖组织动态。", supporting_snapshot_dimensions=["overall_race_structure"], inference_basis="组织动态")),
    _case("hold06_negated_strong_claim", "holdout", True, lambda c, k: c.update(claim_type="current_assessment", claim_text="单一宣传照不足以证明已经全面完成整合。", supporting_snapshot_dimensions=["overall_race_structure"], inference_basis="单一事件且明确限制")),
    _case("hold07_paraphrased_statement_fact", "holdout", False, lambda c, k: (k["period_events"][0].update(fact_status="candidate_claim", evidence_assertions=[{"assertion_id": "st3", "assertion_type": "actor_statement", "text": "双方合作取得明显进度", "speaker": "谢龙介", "source_ids": ["s1"]}]), c.update(claim_text="双方合作已取得明显进度。"))),
    _case("hold08_paraphrased_statement_attributed", "holdout", True, lambda c, k: (k["period_events"][0].update(fact_status="candidate_claim", evidence_assertions=[{"assertion_id": "st3", "assertion_type": "actor_statement", "text": "双方合作取得明显进度", "speaker": "谢龙介", "source_ids": ["s1"]}]), c.update(claim_text="谢龙介称双方合作已取得明显进度。"))),
    _case("hold09_forward_two_events", "holdout", True, lambda c, k: c.update(claim_type="forward_outlook", claim_text="基于两项正式动作，预计后续联合行程可能增加。", supporting_event_ids=["e1", "e2"], supporting_source_ids=["s1", "s2"], inference_basis="e1与e2共同支持", confidence="medium")),
    _case("hold10_forward_one_event", "holdout", False, lambda c, k: c.update(claim_type="forward_outlook", claim_text="预计后续联合行程可能增加。", supporting_event_ids=["e1"], supporting_source_ids=["s1"], inference_basis="单一事件", confidence="medium")),
]


@pytest.mark.parametrize("split,expected,mutate", CASES)
def test_phase42_claim_evidence_golden(split, expected, mutate):
    contract, claim = _base()
    mutate(claim, contract)
    result = validate_claim_semantics(claim, build_evidence_context(contract))
    assert result["accepted"] is expected


def test_golden_partition_and_metrics_are_exact():
    ids = [case.id for case in CASES]
    assert len(ids) == 30
    assert sum(item.startswith("cal") for item in ids) == 20
    assert sum(item.startswith("hold") for item in ids) == 10
    assert len(ids) == len(set(ids))
