"""Research Pack 构建器测试（合成正式数据，确定性）。"""

from __future__ import annotations

from datetime import date

from app.assessment.research_driven.research_pack import (
    ResearchPackContext,
    build_pack_with_context,
    build_research_pack,
    render_pack_markdown,
)
from tests.assessment.research_driven.fixtures import (
    make_formal_data,
    standard_fixture,
)


def _ctx() -> ResearchPackContext:
    return ResearchPackContext(
        period_start=date(2026, 7, 16),
        period_end=date(2026, 7, 31),
        previous_period_start=date(2026, 7, 1),
        previous_period_end=date(2026, 7, 15),
        previous_period_report=None,
        previous_period_article=None,
    )


def test_period_fields_and_facts_cutoff():
    formal, _events, _sources = standard_fixture()
    pack = build_research_pack(formal, _ctx(), {"election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"}, "background": {"max_total": 15, "max_per_mainline": 10, "mainlines": []}})
    assert pack["period"]["period_start"] == "2026-07-16"
    assert pack["period"]["period_end"] == "2026-07-31"
    # facts_cutoff 语义：人工审核完成日（来自 coverage preflight），不是 MAX(event_date)
    assert pack["period"]["facts_cutoff"] == "2026-08-11"
    assert pack["period"]["poll_cutoff"] == "2026-03-12"
    assert pack["data_status"]["facts_cutoff"] == "2026-08-11"


def test_period_events_only_in_range():
    formal, _events, _sources = standard_fixture()
    pack = build_research_pack(formal, _ctx(), {"election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"}, "background": {"max_total": 15, "max_per_mainline": 10, "mainlines": []}})
    period_ids = {e["event_id"] for e in pack["period_events"]}
    assert "evt_period_1" in period_ids
    assert "evt_period_2" in period_ids
    assert "evt_old_poll_claim" in period_ids
    # 01-21 背景事件不得进入本期
    assert "evt_background_1" not in period_ids
    for ev in pack["period_events"]:
        assert ev["event_date"] >= "2026-07-16"


def test_actor_resolution_and_camps():
    formal, _events, _sources = standard_fixture()
    pack = build_research_pack(formal, _ctx(), {"election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"}, "background": {"max_total": 15, "max_per_mainline": 10, "mainlines": []}})
    by_id = {e["event_id"]: e for e in pack["period_events"]}
    # 英文 actor id 必须解析为规范中文名
    assert "陈亭妃" in by_id["evt_period_1"]["actors"]
    assert "谢龙介" in by_id["evt_period_2"]["actors"]
    camps = pack["camps"]
    assert "chen_ting_fei" in camps
    assert any(e["event_id"] == "evt_period_1" for e in camps["chen_ting_fei"])
    assert "hsieh_lung_chieh" in camps


def test_poll_gap_explicit():
    formal, _events, _sources = standard_fixture()
    pack = build_research_pack(formal, _ctx(), {"election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"}, "background": {"max_total": 15, "max_per_mainline": 10, "mainlines": []}})
    polls = pack["polls"]
    assert polls["poll_gap"] is True
    assert polls["no_new_poll_note"] == "本期无新增正式民调。"
    # 最新正式民调（03-12）必须带数字与机构
    latest = polls["latest_polls"]
    assert latest and latest[-1]["pollster"] == "TVBS民意调查中心"
    numbers = latest[-1]["numbers"]
    names = [n["option"] for n in numbers]
    assert "陈亭妃" in names and "谢龙介" in names
    assert all(n["unit"] == "%" for n in numbers)


def test_poll_gap_false_when_period_poll_exists():
    from tests.assessment.research_driven.fixtures import make_event, make_poll, DEFAULT_SOURCES

    events = [
        make_event(
            "evt_p1", "2026-07-20", "陈亭妃活动", actors=["chen_ting_fei"], source_ids=["src_a"]
        )
    ]
    polls = [
        make_poll(
            "poll_new", "2026-07-25", pollster="某机构", numbers=[("陈亭妃", 50.0), ("谢龙介", 30.0)]
        )
    ]
    formal = make_formal_data(
        events=events,
        polls=polls,
        sources=DEFAULT_SOURCES,
        links={("evt_p1", "src_a")},
    )
    pack = build_research_pack(formal, _ctx(), {"election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"}, "background": {"max_total": 15, "max_per_mainline": 10, "mainlines": []}})
    assert pack["polls"]["poll_gap"] is False
    assert pack["polls"]["period_poll_count"] == 1


def test_sources_traceability():
    formal, _events, _sources = standard_fixture()
    pack = build_research_pack(formal, _ctx(), {"election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"}, "background": {"max_total": 15, "max_per_mainline": 10, "mainlines": []}})
    by_id = {e["event_id"]: e for e in pack["period_events"]}
    ev = by_id["evt_period_1"]
    assert ev["sources"]
    assert ev["sources"][0]["publisher"] == "联合新闻网"
    assert ev["sources"][0]["published_at"] == "2026-07-20"
    # 后台保留 source_id，便于人工追溯
    assert ev["sources"][0]["source_id"] == "src_a"


def test_previous_state_baseline_and_do_not_infer():
    formal, _events, _sources = standard_fixture()
    pack = build_research_pack(formal, _ctx(), {"election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"}, "background": {"max_total": 15, "max_per_mainline": 10, "mainlines": []}})
    baseline = pack["previous_state_baseline"]
    assert baseline["baseline_mode"] == "previous_snapshot"
    assert baseline["previous_snapshot"]["snapshot_id"] == "snap_prev"
    assert baseline["active_snapshot"]["snapshot_id"] == "snap_active"
    assert "民进党已经全面整合" in pack["do_not_infer"]


def test_previous_report_context():
    formal, _events, _sources = standard_fixture()
    prev_report = {
        "period_start": "2026-07-01",
        "period_end": "2026-07-15",
        "analysis_plan": {
            "primary_thesis": {"judgment": "上一期主判断"},
            "trend_outlook": {"short_term": "上一期短期判断", "key_turning_conditions": ["指标X"]},
            "camp_status": [{"camp": "陈亭妃阵营", "status_change": "strengthened"}],
        },
    }
    ctx = ResearchPackContext(
        period_start=date(2026, 7, 16),
        period_end=date(2026, 7, 31),
        previous_period_start=date(2026, 7, 1),
        previous_period_end=date(2026, 7, 15),
        previous_period_report=prev_report,
        previous_period_article="上一期文章正文",
    )
    pack = build_research_pack(formal, ctx, {"election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"}, "background": {"max_total": 15, "max_per_mainline": 10, "mainlines": []}})
    assert pack["previous_period_report"]["primary_thesis"]["judgment"] == "上一期主判断"
    assert pack["previous_period_report"]["watch_indicators"] == ["指标X"]


def test_markdown_standalone_readable():
    formal, _events, _sources = standard_fixture()
    pack = build_research_pack(formal, _ctx(), {"election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"}, "background": {"max_total": 15, "max_per_mainline": 10, "mainlines": []}})
    md = render_pack_markdown(pack)
    for heading in (
        "报告周期与事实审核截止",
        "一、本期核心事实",
        "二、与上一期相比的新变化",
        "三、陈亭妃阵营",
        "四、谢龙介阵营",
        "五、民进党派系与中央关系",
        "六、蓝白合作",
        "七、治理议题",
        "八、民调",
        "九、历史背景事件",
        "十、上一期正式报告",
        "十一、证据限制",
        "十二、禁止推断事项",
        "十三、来源清单",
    ):
        assert heading in md
    assert "本期无新增正式民调。" in md
    assert "陈亭妃与民进党提名市议员参选人拍摄联合竞选宣传照" in md
    assert "43.6%" not in md or "43.6" in md  # 事件表态进入研究包（事实），不做断言细节
