"""research_driven 测试夹具：确定性 FormalData 构造（不依赖实时数据库）。"""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from app.assessment.evidence_pack_builder import FormalData


def make_event(
    event_id: str,
    event_date: str,
    title: str,
    *,
    event_type: str = "campaign_launch",
    fact_summary: str = "",
    actors: list[str] | None = None,
    issues: list[str] | None = None,
    source_ids: list[str] | None = None,
    verified_facts: list[str] | None = None,
    actor_statements: list[str] | None = None,
    analytical_significance: str = "",
    fact_status: str = "verified",
    significance_score: int = 50,
) -> dict:
    return {
        "event_id": event_id,
        "election_id": "tainan_mayoral_2026",
        "event_date": event_date,
        "occurred_at": f"{event_date}T00:00:00+08:00",
        "event_type": event_type,
        "title": title,
        "fact_summary": fact_summary or title,
        "fact_status": fact_status,
        "significance_score": significance_score,
        "actors": actors or [],
        "issues": issues or [],
        "verified_facts": verified_facts or [],
        "actor_statements": actor_statements or [],
        "media_interpretations": [],
        "analytical_significance": analytical_significance,
        "evidence_assertions": [],
        "limitations": [],
        "mentions": [],
        "source_ids": source_ids or [],
        "subevents": [],
    }


def make_poll(
    poll_id: str,
    fieldwork_end: str,
    *,
    pollster: str = "TVBS民意调查中心",
    release_date: str | None = None,
    sample_size: int = 1000,
    numbers: list[tuple[str, float]] | None = None,
    source_ids: list[str] | None = None,
) -> dict:
    questions = [{"question_id": f"{poll_id}_q1", "question_type": "head_to_head", "candidate_set": []}]
    results = [
        {
            "question_id": f"{poll_id}_q1",
            "option_id": f"{poll_id}_o{i}",
            "option_name": name,
            "option_type": "candidate",
            "reported_value": value,
            "value": value,
            "unit": "percent",
        }
        for i, (name, value) in enumerate(numbers or [])
    ]
    return {
        "poll_id": poll_id,
        "poll_type": "head_to_head",
        "fact_status": "verified",
        "methodology_complete": True,
        "recommended_disposition": "",
        "pollster": pollster,
        "sponsor": "",
        "release_date": release_date or fieldwork_end,
        "fieldwork_start": fieldwork_end,
        "fieldwork_end": fieldwork_end,
        "sample_size": sample_size,
        "population": {},
        "trend_eligible": True,
        "question_ids": [f"{poll_id}_q1"],
        "questions": questions,
        "results": results,
        "limitations": [],
        "source_ids": source_ids or [],
    }


def make_formal_data(
    *,
    events: list[dict],
    polls: list[dict] | None = None,
    sources: dict[str, dict] | None = None,
    links: set[tuple[str, str]] | None = None,
    facts_cutoff: str = "2026-08-11",
    poll_cutoff: str = "2026-03-12",
) -> FormalData:
    """构造最小可用的 FormalData（research_pack 构建所需字段）。"""
    polls = polls or []
    sources = sources or {}
    links = links or set()
    active_state = {
        "coverage": {"facts_cutoff": facts_cutoff, "poll_cutoff": poll_cutoff},
        "public_poll_assessment": {
            "latest_field_end": poll_cutoff,
            "supporting_poll_ids": [p["poll_id"] for p in polls],
        },
        "dpp_integration": {
            "formal_status": "formal_complete",
            "organizational_status": "partial",
            "prohibited_conclusions": ["民进党已经全面整合"],
            "limitations": ["整合判断基于部分事件"],
        },
        "kmt_tpp_cooperation": {
            "status": "proposed",
            "prohibited_conclusions": ["蓝白已经全面合作"],
        },
        "key_risks": [
            {"risk": "整合反复风险", "risk_type": "organizational"},
        ],
    }
    previous_state = {
        "coverage": {"facts_cutoff": "2026-07-15", "poll_cutoff": poll_cutoff},
        "dpp_integration": {
            "formal_status": "formal_complete",
            "organizational_status": "initial",
        },
    }
    return FormalData(
        election_id="tainan_mayoral_2026",
        events={e["event_id"]: e for e in events},
        sources=sources,
        links=links,
        polls=polls,
        snapshots=[],
        fts_count=len(events),
        counts={
            "elections": 1,
            "actors": 7,
            "formal_event_count": len(events),
            "formal_source_count": len(sources),
            "formal_link_count": len(links),
            "formal_fts_count": len(events),
            "formal_poll_count": len(polls),
            "poll_question_count": 0,
            "poll_result_count": 0,
            "poll_source_link_count": 0,
            "snapshot_count": 0,
        },
        active_snapshot={
            "snapshot_id": "snap_active",
            "election_id": "tainan_mayoral_2026",
            "as_of": "2026-08-11",
            "created_at": "2026-08-11T00:00:00+08:00",
            "state": active_state,
        },
        previous_snapshot={
            "snapshot_id": "snap_prev",
            "election_id": "tainan_mayoral_2026",
            "as_of": "2026-07-27",
            "created_at": "2026-07-27T00:00:00+08:00",
            "state": previous_state,
        },
        snapshot_selection_basis="superseded_by_chain",
        coverage_dir=Path("data/election_seed/tainan_2026/fact_coverage_20260811_v219"),
        coverage_name="fact_coverage_20260811_v219",
        coverage_preflight={
            "facts_cutoff": facts_cutoff,
            "poll_cutoff": poll_cutoff,
            "preflight_ready": True,
            "active_snapshot": "snap_active",
        },
        coverage_validation={"coverage_ready": True},
        gap_reconciliation=[],
        research_backlog=[],
        closure_record=None,
        blocker_triage={},
        theme_matrix=[],
        blocked_ids=set(),
    )


DEFAULT_SOURCES: dict[str, dict] = {
    "src_a": {
        "source_id": "src_a",
        "publisher": "联合新闻网",
        "title": "测试来源A",
        "url": "https://example.com/a",
        "published_at": "2026-07-20T10:00:00+08:00",
        "fetched_at": "2026-07-20T10:00:00+08:00",
        "source_type": "news",
        "evidence_level": "high",
    },
    "src_b": {
        "source_id": "src_b",
        "publisher": "Yahoo新闻",
        "title": "测试来源B",
        "url": "https://example.com/b",
        "published_at": "2026-07-21T10:00:00+08:00",
        "fetched_at": "2026-07-21T10:00:00+08:00",
        "source_type": "news",
        "evidence_level": "high",
    },
}


def standard_fixture() -> tuple[FormalData, list[dict], dict]:
    """2026-07-16..07-31 的确定性标准夹具。"""
    events = [
        make_event(
            "evt_period_1",
            "2026-07-20",
            "陈亭妃与民进党提名市议员参选人拍摄联合竞选宣传照",
            actors=["chen_ting_fei"],
            issues=["dpp_integration", "campaign_strategy"],
            source_ids=["src_a"],
            verified_facts=["陈亭妃与民进党提名的台南市议员参选人共同拍摄竞选宣传照。"],
            analytical_significance="绿营正式提名体系整合动作",
        ),
        make_event(
            "evt_period_2",
            "2026-07-21",
            "谢龙介与蓝营议员参选人在安南区联合拜票",
            actors=["hsieh_lung_chieh"],
            issues=["kmt_organization", "campaign_strategy"],
            source_ids=["src_b"],
            fact_status="multi_source_verified",
        ),
        make_event(
            "evt_background_1",
            "2026-01-21",
            "民进党正式提名陈亭妃参选台南市长",
            actors=["chen_ting_fei"],
            issues=["dpp_integration"],
            source_ids=[],
        ),
        make_event(
            "evt_old_poll_claim",
            "2026-07-21",
            "谢龙介受访称民进党台南未整合并重申四年市长承诺",
            actors=["hsieh_lung_chieh", "chen_ting_fei"],
            issues=["campaign_strategy"],
            source_ids=["src_b"],
            fact_status="candidate_claim",
            actor_statements=["自己的支持度从2022年得票率43.6%起跳。"],
        ),
    ]
    polls = [
        make_poll(
            "poll_20260312_tvbs",
            "2026-03-12",
            pollster="TVBS民意调查中心",
            sample_size=1000,
            numbers=[("陈亭妃", 47.0), ("谢龙介", 34.0)],
        )
    ]
    formal = make_formal_data(
        events=events,
        polls=polls,
        sources=DEFAULT_SOURCES,
        links={("evt_period_1", "src_a"), ("evt_period_2", "src_b"), ("evt_old_poll_claim", "src_b")},
    )
    return formal, events, DEFAULT_SOURCES
