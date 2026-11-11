"""台南选情研判 research-driven 生产路径（V1）。

生产研判主路径：正式事实底座 -> Assessment Research Pack -> 变化识别
-> 核心判断 -> 因果链 -> 权力关系 -> 趋势推演 -> 最终研判文章 -> Word。

设计原则：事实层严格，分析层开放。Assessment 层只读正式事实，
只写 Assessment 运营数据（Research Pack / 报告 / Word / Run 元数据）。

旧 Claim-centric 路径（generate_llm_report / claim_* / r2 generation）
保留为 legacy，不删除、不继续开发、不进入生产。
"""

from __future__ import annotations

PIPELINE_VERSION = "1.0.0"
PRODUCTION_MODE = "research_driven"
LEGACY_MODES = ("single_stage_claim_validated", "two_stage")
OUTPUT_SCHEMA_VERSION = "3.0"

GENERATION_STATUSES = (
    "period_not_ready",
    "research_pack_ready",
    "generating",
    "generated",
    "generation_failed",
    "ready_for_review",
    "machine_rejected",
    "word_render_failed",
    "human_approved",
    "human_rejected",
    "delivered",
)

IDEMPOTENT_SKIP_STATUSES = (
    "ready_for_review",
    "human_approved",
    "human_rejected",
    "delivered",
    "machine_rejected",
    "generation_failed",
    "word_render_failed",
)

__all__ = [
    "PIPELINE_VERSION",
    "PRODUCTION_MODE",
    "LEGACY_MODES",
    "OUTPUT_SCHEMA_VERSION",
    "GENERATION_STATUSES",
    "IDEMPOTENT_SKIP_STATUSES",
]
