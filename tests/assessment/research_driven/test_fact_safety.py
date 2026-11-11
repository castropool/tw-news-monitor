"""Fact Safety Check 测试。"""

from __future__ import annotations

from datetime import date

from app.assessment.research_driven.fact_safety import run_fact_safety_check
from app.assessment.research_driven.research_pack import (
    ResearchPackContext,
    build_research_pack,
)
from tests.assessment.research_driven.fixtures import standard_fixture


def _pack():
    formal, _events, _sources = standard_fixture()
    ctx = ResearchPackContext(
        period_start=date(2026, 7, 16),
        period_end=date(2026, 7, 31),
        previous_period_start=None,
        previous_period_end=None,
        previous_period_report=None,
        previous_period_article=None,
    )
    return build_research_pack(
        formal,
        ctx,
        {"election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"}, "background": {"max_total": 15, "max_per_mainline": 10, "mainlines": []}},
    )


VALID_ARTICLE = """一、核心判断

本期绿营整合由表态支持进入组织协同，但陈亭妃尚未完全掌握地方组织。

二、本期关键变化

一是整合由表态转向组织控制。陈亭妃与民进党提名市议员参选人拍摄联合竞选宣传照。

三、因果链与权力逻辑

第一层：初选结束后的权力过渡窗口打开。

四、主要阵营研判

陈亭妃加速整合，赖系保持隐性制约。

五、治理与社会议题

本期治理议题尚未成为主导议题。

六、趋势判断

未来半个月：预计仍以组织整合为主轴。
未来1-3个月：关键变量是赖系是否让渡关键人事。

七、风险与证据限制

正式民调截止于报告期之前，本期无新增正式民调，不支持支持度判断。"""


def test_pass_on_clean_article():
    pack = _pack()
    audit = run_fact_safety_check(VALID_ARTICLE, "陈亭妃收拢组织确立主导，谢龙介借过渡期抢攻裂缝", pack, "2026-07-31")
    assert audit["status"] == "pass"
    assert audit["hard_block_reasons"] == []


def test_future_event_leakage_blocks():
    pack = _pack()
    article = VALID_ARTICLE + "\n8月18日赖清德宣布改组台南市党部。"
    audit = run_fact_safety_check(article, "标题", pack, "2026-07-31")
    assert audit["status"] == "hard_block"
    assert any("future_event_leakage" in r for r in audit["hard_block_reasons"])
    assert audit["counters"]["future_event_leakage_count"] >= 1


def test_hedged_future_date_does_not_block():
    pack = _pack()
    article = VALID_ARTICLE + "\n预计8月18日前后，赖系可能调整市党部人事。"
    audit = run_fact_safety_check(article, "标题", pack, "2026-07-31")
    assert audit["status"] == "pass"
    assert audit["counters"]["future_event_leakage_count"] == 0


def test_fabricated_poll_number_blocks():
    pack = _pack()
    article = VALID_ARTICLE + "\n最新民调显示陈亭妃支持度52.7%。"
    audit = run_fact_safety_check(article, "标题", pack, "2026-07-31")
    assert audit["status"] == "hard_block"
    assert any("fabricated_poll" in r for r in audit["hard_block_reasons"])


def test_poll_number_from_pack_does_not_block():
    pack = _pack()
    # 47.0 是研究包内正式民调数字（03-12 TVBS）
    article = VALID_ARTICLE + "\n2026年3月12日TVBS民调中陈亭妃为47.0%。"
    audit = run_fact_safety_check(article, "标题", pack, "2026-07-31")
    assert audit["status"] == "pass"
    assert audit["checks"]["fabricated_poll_numbers"]["count"] == 0


def test_unknown_person_is_review_note_only():
    pack = _pack()
    article = VALID_ARTICLE + "\n张三表示将参选。"
    audit = run_fact_safety_check(article, "标题", pack, "2026-07-31")
    assert audit["status"] == "pass"
    assert "张三" in audit["checks"]["unknown_person_candidates"]


def test_known_person_not_flagged():
    pack = _pack()
    audit = run_fact_safety_check(VALID_ARTICLE, "标题", pack, "2026-07-31")
    assert "陈亭妃" not in audit["checks"]["unknown_person_candidates"]
    assert "谢龙介" not in audit["checks"]["unknown_person_candidates"]


def test_forbidden_title_and_missing_sections_are_notes():
    pack = _pack()
    article = "本期选情平稳。"
    audit = run_fact_safety_check(article, "台南市长选情分析", pack, "2026-07-31")
    assert audit["status"] == "pass"
    assert audit["checks"]["forbidden_title_patterns"]
    assert audit["checks"]["missing_sections"]


def test_old_poll_without_date_is_note():
    pack = _pack()
    article = VALID_ARTICLE + "\n当前民调显示陈亭妃为47%。"
    audit = run_fact_safety_check(article, "标题", pack, "2026-07-31")
    assert audit["status"] == "pass"
    assert audit["checks"]["stale_poll_mentions"]


def test_length_below_target_is_note():
    pack = _pack()
    article = "一、核心判断\n\n本期变化有限。" * 3
    audit = run_fact_safety_check(article, "标题", pack, "2026-07-31")
    assert audit["status"] == "pass"
    assert audit["checks"]["chinese_char_count"] < 1800
    assert any("低于" in n for n in audit["review_notes"])
