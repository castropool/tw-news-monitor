"""Explicit mode selection（生产模式 research_driven；legacy 模式保留）。

生产研判路径已切换为 research-driven（app/assessment/research_driven/），
本模块保留供 legacy single_stage/two_stage 调用方使用；
research_driven 在此只作为合法模式名通过，实际路由由新入口承担。
"""

from __future__ import annotations


ALLOWED_MODES = {"single_stage", "two_stage", "research_driven"}


def resolve_assessment_generation_mode(config: dict, requested: str | None = None) -> str:
    configured = (config.get("report_generation") or {}).get(
        "assessment_generation_mode", "single_stage"
    )
    mode = requested or configured
    if mode not in ALLOWED_MODES:
        raise ValueError(f"unsupported assessment_generation_mode: {mode!r}")
    return mode


def two_stage_is_explicitly_enabled(config: dict, requested: str | None = None) -> bool:
    return resolve_assessment_generation_mode(config, requested) == "two_stage"
