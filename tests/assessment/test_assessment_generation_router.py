from pathlib import Path

import pytest

from app.assessment.assessment_generation_router import (
    resolve_assessment_generation_mode,
    two_stage_is_explicitly_enabled,
)
from app.assessment.evidence_pack_builder import load_yaml

ROOT = Path(__file__).resolve().parents[2]


def test_formal_config_points_to_research_driven_production():
    config = load_yaml(ROOT / "config" / "election_assessment.yaml")
    report = config["report_generation"]
    # 生产模式：research_driven（旧 single_stage/two_stage 保留为 legacy）
    assert resolve_assessment_generation_mode(config) == "research_driven"
    assert report["legacy_assessment_generation_modes"] == [
        "single_stage_claim_validated",
        "two_stage",
    ]
    # legacy 版本字段仍保持（旧路径历史兼容）
    assert report["assessment_generation_pipeline_version"] == "2.0.0-rc1"
    assert report["claim_plan_schema_version"] == "1.0"
    assert report["report_output_schema_version"] == "1.1"


def test_legacy_modes_and_unknown_mode_fails_closed():
    assert resolve_assessment_generation_mode({}, "two_stage") == "two_stage"
    assert resolve_assessment_generation_mode({}, "single_stage") == "single_stage"
    assert two_stage_is_explicitly_enabled({}, "two_stage") is True
    with pytest.raises(ValueError):
        resolve_assessment_generation_mode({}, "automatic")
