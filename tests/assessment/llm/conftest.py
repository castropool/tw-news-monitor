import pytest

from app.assessment.claim_evidence_validator import build_evidence_context
from app.assessment.llm.mock_provider import MockProvider


def build_contract():
    return {
        "schema_version": "1.1",
        "contract_version": "1.0",
        "election_id": "tainan_mayoral_2026",
        "election_name": "台南市长选举",
        "report_period": {
            "period_start": "2026-07-16",
            "period_end": "2026-07-31",
            "previous_period_start": "2026-07-01",
            "previous_period_end": "2026-07-15",
        },
        "data_status": {
            "facts_cutoff": "2026-07-27",
            "poll_cutoff": "2026-03-12",
            "uncovered_date_range": [
                "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31",
            ],
        },
        "generation_eligibility": {
            "evidence_pack_ready": True,
            "report_period_fully_covered_by_facts": False,
            "final_report_allowed": False,
            "allowed_generation_mode": "draft_with_data_gap",
            "required_disclosures": [
                "正式事实底表仅覆盖至 2026-07-27",
                "尚未覆盖的具体日期范围：2026-07-28、2026-07-29、2026-07-30、2026-07-31",
                "不得将未覆盖期间表述为“没有重要事件”",
                "报告只能作为数据不完整草稿",
            ],
        },
        "current_snapshot": {
            "snapshot_id": "cur",
            "state": {
                "candidate_status": {
                    "chen_ting_fei": {"name": "陈亭妃", "party": "民主进步党"},
                    "hsieh_lung_chieh": {"name": "谢龙介", "party": "中国国民党"},
                }
            },
        },
        "previous_snapshot": {"snapshot_id": "prev", "state": {}},
        "state_diff": {
            "state_diff_mode": "structured_comparison",
            "dimensions": [
                {"dimension": "overall_race_structure"},
                {"dimension": "kmt_tpp_cooperation"},
            ],
        },
        "period_events": [
            {
                "event_id": "e1",
                "event_date": "2026-07-20",
                "title": "陈亭妃与民进党议员拍摄联合宣传照",
                "source_ids": ["s1"],
                "mentions": [{"mention_name": "陈亭妃"}],
                "subevents": [],
            },
            {
                "event_id": "e2",
                "event_date": "2026-07-21",
                "title": "谢龙介安南区庙口拜票",
                "source_ids": ["s2"],
                "mentions": [{"mention_name": "谢龙介"}],
                "subevents": [],
            },
        ],
        "background_events": [
            {
                "event_id": "bg1",
                "event_date": "2026-01-15",
                "title": "民进党台南市长初选结果",
                "source_ids": ["s3"],
                "mentions": [{"mention_name": "陈亭妃"}],
                "subevents": [],
            }
        ],
        "sources": [
            {"source_id": "s1", "publisher": "联合报", "title": "t1"},
            {"source_id": "s2", "publisher": "东森新闻", "title": "t2"},
            {"source_id": "s3", "publisher": "中央社", "title": "t3"},
        ],
        "polls": [
            {
                "poll_id": "p1",
                "release_date": "2026-03-16",
                "fieldwork_end": "2026-03-12",
                "pollster": "TVBS民意调查中心",
                "sponsor": "TVBS",
                "source_ids": ["s3"],
                "results": [{"reported_value": "41.0%", "value": 41.0}],
            }
        ],
        "theme_status": [],
        "coverage_gaps": [
            {"gap_id": "gap_polling", "stable_gap_id": "gap_polling"},
            {"gap_id": "rt07_feb_mar_gap", "stable_gap_id": "rt07_feb_mar_gap"},
        ],
        "active_research_tasks": [{"research_task_id": "RT05"}],
        "known_limitations": ["民调空窗"],
        "do_not_infer": ["陈亭妃已完成全面整合", "第一选区协调不等于全市复制"],
        "evidence_statistics": {"poll_gap": True},
    }


def make_report(contract, fixture="valid_draft_with_gap"):
    provider = MockProvider(fixture=fixture)
    return provider.generate_structured_report(
        system_prompt="",
        user_payload=contract,
        output_schema={},
        request_metadata={"attempt": 1},
    ).structured_output


@pytest.fixture
def contract():
    return build_contract()


@pytest.fixture
def report(contract):
    return make_report(contract)


@pytest.fixture
def ctx(contract):
    return build_evidence_context(
        contract,
        evidence_pack=None,
        config={
            "report_generation": {
                "entity_whitelist": ["民进党", "国民党", "民众党", "绿营", "蓝营", "白营"]
            }
        },
    )

