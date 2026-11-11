"""Multi-election parameterization tests (台南 default vs 新北 override).

Covers: prompt templating, word renderer naming, fact-safety region terms,
match_reader city derivation, run-config path rewriting.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.assessment.research_driven.fact_safety import run_fact_safety_check
from app.assessment.research_driven.generation import (
    seed_root_from_config,
)
from app.assessment.research_driven.prompt import (
    SYSTEM_PROMPT,
    build_system_prompt,
    build_user_payload,
)
from app.assessment.research_driven.research_pack import render_pack_markdown
from app.assessment.research_driven.word_renderer import word_filename

PROJECT_ROOT = Path(__file__).resolve().parents[2]


def test_system_prompt_default_matches_tainan_constant():
    assert build_system_prompt() == SYSTEM_PROMPT
    assert "专精台南" in SYSTEM_PROMPT
    assert "本期台南市长选情" in SYSTEM_PROMPT


def test_system_prompt_new_taipei_override():
    p = build_system_prompt(
        election_label="新北市长选情",
        region="新北",
        camp_sample="（侯友宜/李四川/国民党执政系统/民进党挑战者）",
    )
    assert "专精新北" in p
    assert "本期新北市长选情" in p
    assert "侯友宜" in p
    assert "台南" not in p


def test_user_payload_task_label():
    payload = build_user_payload({"period": {}}, election_label="新北市长选情")
    assert "新北市长选情" in payload["task"]
    assert "台南" not in payload["task"]


def test_word_filename_prefix():
    assert word_filename("2026-08-23", "2026-09-03") == "台南选情研判_2026-08-23至2026-09-03.docx"
    assert (
        word_filename("2026-08-23", "2026-09-03", file_prefix="新北选情研判")
        == "新北选情研判_2026-08-23至2026-09-03.docx"
    )


def _minimal_pack(region_label: str = "台南市长选情") -> dict:
    return {
        "election": {
            "election_id": "new_taipei_mayoral_2026",
            "display_name": "新北市长选举",
            "report_label": region_label,
        },
        "period": {
            "period_start": "2026-08-23",
            "period_end": "2026-09-03",
            "facts_cutoff": "2026-09-03",
            "poll_cutoff": "",
        },
        "data_status": {
            "formal_event_count": 0,
            "period_event_count": 0,
            "background_event_count": 0,
        },
        "previous_state_baseline": {
            "state_diff": {"changed_dimensions": [], "unchanged_dimensions": [], "dimensions": []}
        },
        "period_events": [],
        "background_events": [],
        "camps": {},
        "camp_sections": [],
        "polls": {
            "latest_polls": [],
            "changes_vs_previous": [],
            "poll_gap": False,
            "no_new_poll_note": "",
            "stale_note": "",
        },
        "governance_issues": [],
        "known_limitations": [],
        "do_not_infer": [],
        "sources": [],
    }


def test_pack_markdown_title_uses_report_label():
    md = render_pack_markdown(_minimal_pack("新北市长选情"))
    assert md.splitlines()[0] == "# 新北市长选情研判研究包（Assessment Research Pack）"


def test_pack_markdown_falls_back_to_tainan_when_no_label():
    pack = _minimal_pack()
    pack["election"] = {}
    md = render_pack_markdown(pack)
    assert "台南市长选情" in md.splitlines()[0]


_VALID_BODY = (
    "一、核心判断\n\n"
    + "本期最重要的政治变化是测试内容。" * 40
    + "\n\n二、本期关键变化\n\n三、因果链与权力逻辑\n\n四、主要阵营研判\n\n"
    + "五、治理与社会议题\n\n六、趋势判断\n\n七、风险与证据限制"
)


def test_fact_safety_region_title_pattern_tainan_default():
    audit = run_fact_safety_check(
        _VALID_BODY, "台南市长选情分析", _minimal_pack(), "2026-09-03"
    )
    assert any("台南市长选情分析" in p for p in audit["review_notes"])


def test_fact_safety_region_title_pattern_new_taipei():
    audit = run_fact_safety_check(
        _VALID_BODY,
        "新北市长选情分析",
        _minimal_pack("新北市长选情"),
        "2026-09-03",
        region_terms=("新北", "新北市"),
    )
    assert any("新北市长选情分析" in p for p in audit["review_notes"])
    # 台南模式不应再命中新北标题
    assert not any("台南" in p for p in audit["review_notes"])


def test_seed_root_from_config_new_taipei():
    cfg = {
        "election": {"election_id": "new_taipei_mayoral_2026"},
        "paths": {"coverage_root": "data/election_seed/new_taipei_2026"},
    }
    assert seed_root_from_config(cfg, PROJECT_ROOT) == (
        PROJECT_ROOT / "data/election_seed/new_taipei_2026"
    )


def test_seed_root_from_config_defaults_tainan():
    assert seed_root_from_config({}, PROJECT_ROOT) == (
        PROJECT_ROOT / "data/election_seed/tainan_2026"
    )
