"""R2 generation input tests: explicit project_root, auto coverage resolution, model config.

All fixtures live under tmp_path; never touches live data/*.db or seed trees.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from pathlib import Path
from unittest.mock import patch

import pytest

from app.assessment.evidence_pack_builder import EvidencePackError
from app.assessment.r2.generation import (
    _freeze_production_input,
    default_project_root,
    run_generation,
)
from app.assessment.r2.state import ReportRunStore


def _write_coverage(seed_root: Path, name: str, *, facts_cutoff: str, ready: bool = True,
                    corrupt_preflight: bool = False) -> Path:
    cov = seed_root / name
    cov.mkdir(parents=True, exist_ok=True)
    if corrupt_preflight:
        (cov / "coverage_preflight.json").write_text("{ not json", encoding="utf-8")
    else:
        (cov / "coverage_preflight.json").write_text(
            json.dumps(
                {
                    "coverage_version": name,
                    "coverage_generated_at": facts_cutoff,
                    "facts_cutoff": facts_cutoff,
                    "poll_cutoff": "2026-03-12",
                    "active_snapshot": f"snap_{name}",
                    "preflight_ready": ready,
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
    (cov / "coverage_validation.json").write_text(
        json.dumps({"coverage_ready": ready}, ensure_ascii=False), encoding="utf-8"
    )
    return cov


def _make_project_root(tmp_path: Path) -> Path:
    root = tmp_path / "proj"
    data = root / "data"
    data.mkdir(parents=True)
    conn = sqlite3.connect(data / "election_context.db")
    conn.close()
    (data / "election_seed" / "tainan_2026").mkdir(parents=True)
    return root


def _hash_patches():
    return (
        patch(
            "app.election_context.formal_state_hash.formal_state_business_hash_from_db",
            return_value="db-hash",
        ),
        patch(
            "app.election_context.formal_state_hash.formal_state_business_hash_from_seed_dir",
            return_value="seed-hash",
        ),
    )


def _minimal_config(tmp_path: Path, *, default_model: str = "deepseek-v4-flash") -> Path:
    cfg = {
        "election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"},
        "schedule": {"run_days": [9, 22]},
        "pipeline": {"lock_dir": "work/locks"},
        "llm": {
            "default_provider": "deepseek",
            "deepseek": {
                "model_env": "DEEPSEEK_MODEL",
                "default_model": default_model,
            },
        },
    }
    path = tmp_path / "assessment_config.yaml"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return path


# ---------- project_root ----------


def test_default_project_root_is_repo_root_not_archive():
    repo_root = Path(__file__).resolve().parents[2]
    assert default_project_root() == repo_root
    assert "0801tw-news-monitor-simple" not in str(default_project_root())


def test_freeze_reads_input_from_explicit_project_root(tmp_path):
    root = _make_project_root(tmp_path)
    seed = root / "data/election_seed/tainan_2026"
    _write_coverage(seed, "fact_coverage_20260727_v2", facts_cutoff="2026-07-27")
    _write_coverage(seed, "fact_coverage_20260811_v219", facts_cutoff="2026-08-11")

    run_dir = tmp_path / "run"
    with _hash_patches()[0], _hash_patches()[1]:
        frozen = _freeze_production_input(run_dir, root)

    assert frozen["coverage_version"] == "fact_coverage_20260811_v219"
    assert frozen["facts_cutoff"] == "2026-08-11"
    assert frozen["poll_cutoff"] == "2026-03-12"
    assert frozen["active_snapshot_id"] == "snap_fact_coverage_20260811_v219"
    assert frozen["coverage_generated_at"] == "2026-08-11"
    assert frozen["coverage_ready"] is True
    assert frozen["input_hash"] == "db-hash"
    assert frozen["seed_hash"] == "seed-hash"
    # 冻结副本落在 run_dir/input 且携带解析到的 coverage
    assert (run_dir / "input/election_context.db").exists()
    assert (
        run_dir / "input/election_seed/fact_coverage_20260811_v219/coverage_preflight.json"
    ).exists()


def test_freeze_picks_latest_ready_version_stably(tmp_path):
    root = _make_project_root(tmp_path)
    seed = root / "data/election_seed/tainan_2026"
    _write_coverage(seed, "fact_coverage_20260727_v2", facts_cutoff="2026-07-27")
    _write_coverage(seed, "fact_coverage_20260801_v4", facts_cutoff="2026-07-27")
    _write_coverage(seed, "fact_coverage_20260811_v219", facts_cutoff="2026-08-11")
    # 更新但未 ready 的版本必须被忽略（不得按目录名盲选）
    _write_coverage(seed, "fact_coverage_20260812_v220", facts_cutoff="2026-08-12", ready=False)

    run_dir = tmp_path / "run"
    with _hash_patches()[0], _hash_patches()[1]:
        frozen = _freeze_production_input(run_dir, root)
    assert frozen["coverage_version"] == "fact_coverage_20260811_v219"
    assert frozen["facts_cutoff"] == "2026-08-11"


def test_freeze_fails_on_corrupt_preflight_no_silent_fallback(tmp_path):
    root = _make_project_root(tmp_path)
    seed = root / "data/election_seed/tainan_2026"
    # 存在一个损坏 preflight 的更新版本：不得静默回退到旧版本，必须明确失败
    _write_coverage(seed, "fact_coverage_20260811_v219", facts_cutoff="2026-08-11")
    _write_coverage(seed, "fact_coverage_20260813_v221", facts_cutoff="2026-08-13",
                    corrupt_preflight=True)
    with pytest.raises(Exception, match="Expecting property name|JSONDecodeError|JSON"):
        _freeze_production_input(tmp_path / "run", root)


def test_freeze_fails_when_no_ready_coverage(tmp_path):
    root = _make_project_root(tmp_path)
    seed = root / "data/election_seed/tainan_2026"
    # 只有未 ready 的 coverage
    _write_coverage(seed, "fact_coverage_20260811_v219", facts_cutoff="2026-08-11", ready=False)
    with pytest.raises(EvidencePackError, match="未找到任何 ready 的正式覆盖版本"):
        _freeze_production_input(tmp_path / "run", root)

    # 完全没有 coverage 目录
    empty_root = _make_project_root(tmp_path / "empty")
    with pytest.raises(EvidencePackError, match="未找到任何 ready 的正式覆盖版本"):
        _freeze_production_input(tmp_path / "run2", empty_root)


def test_freeze_fails_when_frozen_copy_missing_preflight(tmp_path):
    root = _make_project_root(tmp_path)
    seed = root / "data/election_seed/tainan_2026"
    _write_coverage(seed, "fact_coverage_20260811_v219", facts_cutoff="2026-08-11")

    # 解析到某版本，但冻结副本未携带该版本（模拟复制丢目录/文件）
    phantom = (
        seed / "fact_coverage_20990101_v9",
        "fact_coverage_20990101_v9",
        {"preflight_ready": True},
        {"coverage_ready": True},
    )
    with _hash_patches()[0], _hash_patches()[1], patch(
        "app.assessment.r2.generation.select_coverage_version", return_value=phantom
    ):
        with pytest.raises(RuntimeError, match="冻结输入缺少 coverage preflight"):
            _freeze_production_input(tmp_path / "run", root)


# ---------- model from config / env ----------


def _run_check_only(config: Path, runs_root: Path, frozen: dict) -> dict:
    with patch(
        "app.assessment.r2.generation._freeze_production_input", return_value=frozen
    ):
        return run_generation(
            config_path=config,
            runs_root=runs_root,
            as_of=date(2026, 8, 22),
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 15),
            trigger_type="scheduled",
            check_only=True,
            force_regenerate=False,
        )


def test_model_resolved_from_assessment_config(tmp_path):
    config = _minimal_config(tmp_path, default_model="deepseek-v4-flash")
    frozen = {
        "input_hash": "h",
        "facts_cutoff": "2026-08-15",
        "poll_cutoff": "2026-03-12",
        "coverage_version": "fact_coverage_test_v1",
    }
    result = _run_check_only(config, tmp_path / "runs", frozen)
    assert result["code"] == "CHECK_OK"
    stored = ReportRunStore(tmp_path / "runs").get(
        "tainan_mayoral_2026__20260801__20260815"
    )
    assert stored["model"] == "deepseek-v4-flash"
    assert stored["coverage_version"] == "fact_coverage_test_v1"


def test_model_env_override_respected(tmp_path, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    config = _minimal_config(tmp_path, default_model="deepseek-v4-flash")
    frozen = {
        "input_hash": "h",
        "facts_cutoff": "2026-08-15",
        "poll_cutoff": "2026-03-12",
        "coverage_version": "fact_coverage_test_v1",
    }
    result = _run_check_only(config, tmp_path / "runs", frozen)
    assert result["code"] == "CHECK_OK"
    stored = ReportRunStore(tmp_path / "runs").get(
        "tainan_mayoral_2026__20260801__20260815"
    )
    assert stored["model"] == "deepseek-v4-pro"
