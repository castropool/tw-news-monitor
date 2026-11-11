from copy import deepcopy

from tests.assessment.llm.conftest import build_contract


def contract_fixture():
    contract = build_contract()
    contract["period_events"][0]["evidence_assertions"] = [{
        "assertion_id": "a1", "assertion_type": "observed_fact",
        "text": "陈亭妃与民进党议员拍摄联合宣传照", "speaker": None,
        "source_ids": ["s1"],
    }]
    contract["period_events"][1]["evidence_assertions"] = [{
        "assertion_id": "a2", "assertion_type": "observed_fact",
        "text": "谢龙介在安南区进行庙口拜票", "speaker": None,
        "source_ids": ["s2"],
    }]
    return contract


def _claim(cid, section, ctype, strength, text, *, events=(), sources=(), polls=(), dims=(), gaps=(), basis="", confidence="medium", material=True):
    return {
        "claim_id": cid,
        "target_section_id": section,
        "claim_type": ctype,
        "claim_strength": strength,
        "claim_text": text,
        "event_ids": list(events),
        "source_ids": list(sources),
        "poll_ids": list(polls),
        "snapshot_dimensions": list(dims),
        "gap_ids": list(gaps),
        "evidence_reasoning_summary": basis,
        "confidence": confidence,
        "limitations": [],
        "material_for_report": material,
        "applies_to_period": True,
    }


def valid_plan():
    return {
        "claim_plan_version": "1.0",
        "claim_planner_contract_version": "1.0",
        "election_id": "tainan_mayoral_2026",
        "reporting_period": {"period_start": "2026-07-16", "period_end": "2026-07-31"},
        "formal_state_hash": "formal-hash",
        "evidence_pack_hash": "pack-hash",
        "claims": [
            _claim("CP_S01_001", "S01", "current_assessment", "bounded_inference", "基于两项正式动作，研判双方竞选活动正在增加。", events=("e1", "e2"), sources=("s1", "s2"), dims=("overall_race_structure",), basis="两项事件共同支持"),
            _claim("CP_S02_001", "S02", "factual_synthesis", "direct_fact", "陈亭妃与民进党议员拍摄联合宣传照。", events=("e1",), sources=("s1",), confidence="high"),
            _claim("CP_S03_001", "S03", "factual_synthesis", "direct_fact", "陈亭妃与民进党议员拍摄联合宣传照。", events=("e1",), sources=("s1",), confidence="high"),
            _claim("CP_S04_001", "S04", "factual_synthesis", "direct_fact", "谢龙介在安南区进行庙口拜票。", events=("e2",), sources=("s2",), confidence="high"),
            _claim("CP_S05_001", "S05", "comparative_assessment", "bounded_inference", "基于两项正式动作，研判蓝白合作变化仍值得观察。", events=("e1", "e2"), sources=("s1", "s2"), dims=("kmt_tpp_cooperation",), basis="事件与快照维度共同支持"),
            _claim("CP_S06_001", "S06", "data_disclosure", "direct_fact", "正式民调调查截止于2026-03-12，不代表7月底实时支持率。", polls=("p1",), sources=("s3",), confidence="not_applicable", material=False),
            _claim("CP_S07_001", "S07", "forward_outlook", "bounded_inference", "基于两项正式动作，预计未来半月竞选活动可能继续增加。", events=("e1", "e2"), sources=("s1", "s2"), basis="两项事件共同支持"),
            _claim("CP_S08_001", "S08", "limitation", "direct_fact", "民调空窗是本期研判限制。", gaps=("gap_polling",), confidence="not_applicable", material=False),
        ],
        "data_limitations": ["事实覆盖不完整", "民调空窗"],
    }


def clone(value):
    return deepcopy(value)

