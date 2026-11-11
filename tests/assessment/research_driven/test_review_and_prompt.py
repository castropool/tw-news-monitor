"""research_driven 人工终审与 Prompt 解析测试。"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from app.assessment.research_driven.generation import run_generation
from app.assessment.research_driven.prompt import (
    SYSTEM_PROMPT,
    build_user_payload,
    parse_model_output,
)
from app.assessment.research_driven.review import (
    approve_report,
    reject_report,
)
from app.assessment.r2.state import ReportRunStore

PROJECT_ROOT = Path(__file__).resolve().parents[3]
CONFIG_PATH = PROJECT_ROOT / "config/election_assessment.yaml"
RUN_KEY = "tainan_mayoral_2026__20260716__20260731"


def _generate(tmp_path: Path) -> ReportRunStore:
    run_generation(
        config_path=CONFIG_PATH,
        runs_root=tmp_path,
        as_of=date(2026, 8, 9),
        period_start=date(2026, 7, 16),
        period_end=date(2026, 7, 31),
        trigger_type="controlled",
        check_only=False,
        force_regenerate=False,
        project_root=PROJECT_ROOT,
        mock_fixture="valid",
        lock_dir=tmp_path / "locks",
    )
    return ReportRunStore(tmp_path)


def test_approve_and_reject_flow(tmp_path):
    store = _generate(tmp_path)
    assert approve_report(store, RUN_KEY, reviewer="tester")["code"] == "APPROVED"
    run = store.get(RUN_KEY)
    assert run["generation_status"] == "human_approved"
    # 已批准不允许重复批准
    assert approve_report(store, RUN_KEY, reviewer="tester")["code"] == "BLOCKED_NOT_READY"


def test_reject_flow(tmp_path):
    store = _generate(tmp_path)
    result = reject_report(store, RUN_KEY, reviewer="tester", reason="风格需要修改")
    assert result["code"] == "REJECTED"
    run = store.get(RUN_KEY)
    assert run["generation_status"] == "human_rejected"
    assert run["rejection_reason"] == "风格需要修改"


def test_approve_blocks_on_changed_article(tmp_path):
    store = _generate(tmp_path)
    run = store.get(RUN_KEY)
    article_path = Path(run["output_path"])
    article_path.write_text(article_path.read_text(encoding="utf-8") + "\n被修改", encoding="utf-8")
    result = approve_report(store, RUN_KEY, reviewer="tester")
    assert result["code"] == "BLOCKED_REPORT_CHANGED"


def test_parse_model_output_min_contract():
    good = {
        "analysis_plan": {
            "primary_thesis": {"judgment": "主判断"},
            "key_changes": [{"rank": 1, "change": "变化"}],
        },
        "final_article": {"title": "判断型标题", "body": "一、核心判断\n\n" + "内容" * 160},
    }
    plan, article = parse_model_output(good)
    assert plan["primary_thesis"]["judgment"] == "主判断"
    assert article["title"] == "判断型标题"


def test_parse_model_output_rejects_bad_shapes():
    import pytest

    for bad in (
        {},
        {"analysis_plan": {}, "final_article": {"title": "t", "body": ""}},
        {
            "analysis_plan": {"primary_thesis": {"judgment": ""}, "key_changes": []},
            "final_article": {"title": "t", "body": "x" * 500},
        },
        {"analysis_plan": {"primary_thesis": {"judgment": "j"}, "key_changes": []}, "final_article": {"title": "", "body": "x" * 500}},
    ):
        with pytest.raises(ValueError):
            parse_model_output(bad)


def test_system_prompt_sections():
    for section in (
        "ROLE",
        "TASK",
        "FACT BOUNDARY",
        "ANALYTICAL METHOD",
        "ARTICLE STRUCTURE",
        "WRITING STYLE",
        "FACT VS ASSESSMENT",
        "TREND ANALYSIS",
        "FORBIDDEN BEHAVIOR",
        "OUTPUT FORMAT",
    ):
        assert section in SYSTEM_PROMPT
    # 不得要求逐句机械对应 claim
    assert "atomic claim" not in SYSTEM_PROMPT.lower().replace("claim", "claim")


def test_build_user_payload():
    pack = {"period": {"period_start": "2026-07-16"}}
    payload = build_user_payload(pack, previous_period_article="上一期文章")
    assert payload["research_pack"] is pack
    assert payload["previous_period_article"] == "上一期文章"
    assert "不是新事实来源" in payload["previous_period_note"]
