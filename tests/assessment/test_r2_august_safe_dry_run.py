"""R2 八月报告周期安全 dry-run 集成测试（完全隔离，不碰实时数据）。

设计目标（对应 R2 输入/coverage/模型修复的验收）：
  1. 从“当前主目录事实状态的临时副本”运行 2026-08-01..2026-08-15 周期链路，
     证明不会因 coverage facts_cutoff 过期而跳过（SKIPPED_PERIOD_NOT_READY）。
  2. 覆盖：显式 project_root、自动选择 ready coverage、period readiness、
     实际模型解析、run metadata、输入冻结、mock provider 全链路产物
     （证据包 manifest、生成 manifest、docx）。
  3. 隔离保证：
     - 全部运行产物位于系统临时目录（允许的 pytest tmp_path 替代位置；
       Windows 下 pytest tmp_path 基路径过长会导致 seed 深层文件复制超过
       MAX_PATH 260 字符，故本 harness 使用短路径系统临时目录）；
     - 不复制实时 data/election_context.db（临时 DB 由 run_bootstrap
       从临时 seed 副本合成）；
     - coverage 元数据按主目录最新 ready coverage 构造等价版本
       （facts_cutoff=2026-08-15，满足 period 2026-08-01..2026-08-15）；
     - provider 以 MockProvider 替换（无网络）；run_generation 本身不发送通知；
     - 实时 DB 与 data/election_assessment/tainan_2026/r2_runs 前后指纹一致。
"""

from __future__ import annotations

import contextlib
import io
import json
import shutil
import tempfile
import uuid
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from app.assessment.evidence_pack_builder import select_coverage_version
from app.assessment.llm.mock_provider import MockProvider
from app.assessment.r2.generation import run_generation
from app.assessment.r2.state import ReportRunStore
from app.election_context.bootstrap import run_bootstrap

BASE = Path(__file__).resolve().parents[2]
LIVE_SEED = BASE / "data/election_seed/tainan_2026"
LIVE_DB = BASE / "data/election_context.db"
LIVE_R2_RUNS = BASE / "data/election_assessment/tainan_2026/r2_runs"

AUGUST_PERIOD_END = "2026-08-15"
# 合成版本号必须高于当前主目录的 20260903_v905，才能被选择器选中。
SYNTH_COVERAGE_NAME = "fact_coverage_20260904_v906"
RUN_KEY = "tainan_mayoral_2026__20260801__20260815"
PERIOD_DIR = "work/20260801_20260815"
# Windows 路径预算：seed 深层文件复制路径必须低于 MAX_PATH(260)，
# 保留余量避免生产路径（仓库根目录更长时）越界。
PATH_BUDGET = 250


@pytest.fixture
def short_tmp():
    """短路径系统临时目录（替代 pytest tmp_path 的深层基路径）。"""
    root = Path(tempfile.gettempdir()) / f"r2dry_{uuid.uuid4().hex[:8]}"
    root.mkdir()
    try:
        yield root
    finally:
        shutil.rmtree(root, ignore_errors=True)


# ---------------------------------------------------------------------------
# 实时数据防护（只记录 mtime/文件清单，不哈希读取实时 DB）
# ---------------------------------------------------------------------------


def _fingerprint(path: Path):
    st = path.stat()
    return (st.st_mtime_ns, st.st_size)


def _tree_fingerprint(root: Path) -> dict[str, tuple[int, int]]:
    out: dict[str, tuple[int, int]] = {}
    if not root.exists():
        return out
    for p in sorted(root.rglob("*")):
        if p.is_file():
            out[p.relative_to(root).as_posix()] = _fingerprint(p)
    return out


def _live_guard_snapshot() -> dict:
    assert LIVE_DB.exists(), f"实时 DB 不存在: {LIVE_DB}"
    live_coverage = select_coverage_version(LIVE_SEED)  # 只读解析最新 ready coverage
    live_cov_name = live_coverage[1]
    return {
        "live_db": _fingerprint(LIVE_DB),
        "live_r2_runs": _tree_fingerprint(LIVE_R2_RUNS),
        "live_seed": _tree_fingerprint(LIVE_SEED),
        "live_coverage_version": live_cov_name,
        "live_coverage_preflight": _fingerprint(
            LIVE_SEED / live_cov_name / "coverage_preflight.json"
        ),
        "live_coverage_validation": _fingerprint(
            LIVE_SEED / live_cov_name / "coverage_validation.json"
        ),
    }


def _assert_live_unchanged(before: dict) -> None:
    after = _live_guard_snapshot()
    assert after["live_db"] == before["live_db"], "实时 data/election_context.db 被修改！"
    assert (
        after["live_r2_runs"] == before["live_r2_runs"]
    ), "实时 data/election_assessment/tainan_2026/r2_runs 被修改！"
    assert after["live_seed"] == before["live_seed"], "实时 election_seed 树被修改！"
    assert after["live_coverage_preflight"] == before["live_coverage_preflight"]
    assert after["live_coverage_validation"] == before["live_coverage_validation"]


# ---------------------------------------------------------------------------
# 临时项目树：seed 副本 + 等价 coverage + 合成 DB（全部在 short_tmp 内）
# ---------------------------------------------------------------------------


def _build_temp_project_tree(work_root: Path) -> Path:
    """从主目录事实状态构造临时 project_root（不复制实时 DB）。"""
    assert LIVE_SEED.exists(), f"主目录 seed 不存在: {LIVE_SEED}"
    proj = work_root / "p"
    seed_dst = proj / "data/election_seed/tainan_2026"
    seed_dst.mkdir(parents=True)
    # 1) 当前主目录事实状态的临时副本（只读源；文件复制到临时目录）
    shutil.copytree(LIVE_SEED, seed_dst, dirs_exist_ok=True)

    # 2) 按主目录最新 ready coverage 构造等价覆盖版本：facts_cutoff 满足
    #    period 2026-08-01..2026-08-15（生产 20260811_v219 的 cutoff=08-11
    #    会触发 SKIPPED_PERIOD_NOT_READY，故模拟下一次 ready 覆盖）。
    live_cov_path, live_cov_name, _, _ = select_coverage_version(LIVE_SEED)
    synth = seed_dst / SYNTH_COVERAGE_NAME
    shutil.copytree(live_cov_path, synth)
    for fname in ("coverage_preflight.json", "coverage_validation.json"):
        p = synth / fname
        obj = json.loads(p.read_text(encoding="utf-8"))
        obj["coverage_version"] = SYNTH_COVERAGE_NAME
        obj["facts_cutoff"] = AUGUST_PERIOD_END
        obj["coverage_generated_at"] = AUGUST_PERIOD_END
        obj["requested_period_end"] = AUGUST_PERIOD_END
        p.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    assert (synth / "coverage_preflight.json").exists()

    # 3) 合成 SQLite：由临时 seed 副本 bootstrap（绝不复制实时 DB）
    db_path = proj / "data/election_context.db"
    with contextlib.redirect_stdout(io.StringIO()):
        ok, stats = run_bootstrap(str(seed_dst), str(db_path), reset=True)
    assert ok, f"临时 DB bootstrap 失败: {stats}"
    assert db_path.exists()
    return proj


def _minimal_config(work_root: Path, *, default_model: str) -> Path:
    cfg = {
        "election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"},
        "schedule": {"run_days": [9, 22]},
        "pipeline": {"lock_dir": str((work_root / "locks").resolve())},
        "llm": {
            "default_provider": "deepseek",
            "deepseek": {"model_env": "DEEPSEEK_MODEL", "default_model": default_model},
        },
    }
    path = work_root / "assessment_config.yaml"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return path


def _frozen_input_dir(runs_root: Path) -> Path:
    return runs_root / PERIOD_DIR / "input"


def _run_record(runs_root: Path) -> dict:
    store = ReportRunStore(runs_root)
    run = store.get(RUN_KEY)
    assert run is not None, f"run record 未写入: {RUN_KEY}"
    return run


def _assert_no_delivery(runs_root: Path) -> None:
    run = _run_record(runs_root)
    assert run["delivery_status"] == "not_attempted"
    assert not (runs_root / "deliveries").exists(), "不得有任何 delivery 记录"


def _assert_path_budget(paths: list[Path]) -> None:
    for p in paths:
        length = len(str(p))
        assert length < PATH_BUDGET, (
            f"临时树路径超长（{length} >= {PATH_BUDGET}）：{p}；"
            "Windows MAX_PATH 风险，请改用更短的临时基路径"
        )


# ---------------------------------------------------------------------------
# 时间冻结：把“当前时刻”固定为 2026-08-22 09:00（本月第 2 个调度日）
# run_generation 把解析出的周期以显式参数传给证据包构建器，
# 后者以真实时钟判断 period_complete；冻结时钟等价于在该调度日当天执行。
# ---------------------------------------------------------------------------


class _FakeDateTime(datetime):
    @classmethod
    def now(cls, tz=None):  # noqa: D102
        return datetime(2026, 8, 22, 9, 0, 0, tzinfo=tz)


def _mock_provider_factory(calls: list):
    def factory(provider, config=None, model=None, thinking_mode="disabled"):
        calls.append({"provider": provider, "model": model})
        assert provider == "deepseek"
        return MockProvider(model=model or "mock-model", fixture="valid_final")

    return factory


# ---------------------------------------------------------------------------
# 用例
# ---------------------------------------------------------------------------


def test_august_cycle_check_only_no_cutoff_skip_on_temp_tree(short_tmp):
    """生成前链路：显式 project_root + 自动选 coverage + period readiness + 模型解析。"""
    before = _live_guard_snapshot()
    proj = _build_temp_project_tree(short_tmp)
    config = _minimal_config(short_tmp, default_model="deepseek-v4-flash")
    runs_root = short_tmp / "r2_runs"

    result = run_generation(
        config_path=config,
        runs_root=runs_root,
        as_of=date(2026, 8, 22),
        period_start=None,
        period_end=None,
        trigger_type="scheduled",
        check_only=True,
        force_regenerate=False,
        project_root=proj,
    )

    # 未因 cutoff 过期而跳过（核心验收）
    assert result["code"] != "SKIPPED_PERIOD_NOT_READY"
    assert result["code"] == "CHECK_OK", result
    assert result["facts_cutoff"] == AUGUST_PERIOD_END

    # 冻结输入与 coverage 来自临时 project_root（自动选择）
    frozen = _frozen_input_dir(runs_root)
    assert str(frozen).startswith(str(short_tmp))
    frozen_preflight = frozen / "election_seed" / SYNTH_COVERAGE_NAME / "coverage_preflight.json"
    assert frozen_preflight.exists(), f"冻结副本缺少 coverage: {frozen_preflight}"
    assert json.loads(frozen_preflight.read_text(encoding="utf-8"))["facts_cutoff"] == (
        AUGUST_PERIOD_END
    )
    assert (frozen / "election_context.db").exists()
    assert (frozen / "election_seed" / SYNTH_COVERAGE_NAME / "coverage_validation.json").exists()
    _assert_path_budget([frozen_preflight, frozen / "election_context.db"])

    # run metadata + 模型为配置解析值（非硬编码）
    run = _run_record(runs_root)
    assert run["generation_status"] == "check_only_passed"
    assert run["model"] == "deepseek-v4-flash"
    assert run["coverage_version"] == SYNTH_COVERAGE_NAME
    assert run["facts_cutoff"] == AUGUST_PERIOD_END
    assert run["input_hash"]  # 真实冻结哈希（非空）
    _assert_no_delivery(runs_root)
    _assert_live_unchanged(before)


def test_august_cycle_full_mock_generation_isolated(short_tmp):
    """全链路：真实冻结 + 证据包 + mock provider 生成 + 机器门禁 + docx，无网络无投递。"""
    before = _live_guard_snapshot()
    proj = _build_temp_project_tree(short_tmp)
    config = _minimal_config(short_tmp, default_model="deepseek-v4-flash")
    runs_root = short_tmp / "r2_runs"
    provider_calls: list = []

    with patch(
        "app.assessment.reporting_period.datetime", _FakeDateTime
    ), patch(
        "app.assessment.generate_llm_report.create_provider",
        _mock_provider_factory(provider_calls),
    ):
        result = run_generation(
            config_path=config,
            runs_root=runs_root,
            as_of=date(2026, 8, 22),
            period_start=None,
            period_end=None,
            trigger_type="scheduled",
            check_only=False,
            force_regenerate=False,
            project_root=proj,
        )

    assert result["code"] != "SKIPPED_PERIOD_NOT_READY"
    assert result["code"] == "GENERATED_READY_FOR_REVIEW", result
    assert result["machine_disposition"] == "PASS"
    assert result["run_key"] == RUN_KEY

    # 冻结输入与 coverage 来自临时 project_root
    frozen = _frozen_input_dir(runs_root)
    assert str(frozen).startswith(str(short_tmp))
    frozen_preflight = frozen / "election_seed" / SYNTH_COVERAGE_NAME / "coverage_preflight.json"
    assert frozen_preflight.exists()
    assert json.loads(frozen_preflight.read_text(encoding="utf-8"))["facts_cutoff"] == (
        AUGUST_PERIOD_END
    )
    _assert_path_budget([frozen_preflight, Path(result["word_path"])])

    # 报告/manifest 产物
    period_label = "2026-08-01_2026-08-15"
    out = runs_root / PERIOD_DIR / "ab" / "single_stage" / period_label
    assert (out / "structured_report_attempt_1.json").exists()
    assert (out / "structured_report_final.json").exists()
    assert (out / "report_generation_manifest.json").exists()
    assert (runs_root / PERIOD_DIR / "work" / period_label / "report_run_manifest.json").exists()
    assert Path(result["word_path"]).exists()
    assert Path(result["word_path"]).suffix == ".docx"

    # 无网络：mock provider 实打实响应（provider_response_metadata 记录 provider=mock）
    assert len(provider_calls) == 1 and provider_calls[0]["provider"] == "deepseek"
    meta = json.loads(
        (out / "provider_response_metadata.json").read_text(encoding="utf-8")
    )
    assert meta["provider"] == "mock"
    assert meta["provider_request_id"].startswith("mock-")
    corr = json.loads((out / "request_correlation.json").read_text(encoding="utf-8"))
    assert corr["status"] == "response_received"

    # run metadata：模型为配置解析值；无投递
    run = _run_record(runs_root)
    assert run["generation_status"] == "ready_for_human_review"
    assert run["model"] == "deepseek-v4-flash"
    assert run["coverage_version"] == SYNTH_COVERAGE_NAME
    assert run["facts_cutoff"] == AUGUST_PERIOD_END
    assert run["machine_validation_status"] == "passed"
    assert run["report_hash"] and run["word_hash"]
    _assert_no_delivery(runs_root)
    _assert_live_unchanged(before)


def test_august_cycle_model_env_override_via_real_freeze(short_tmp, monkeypatch):
    """模型解析：DEEPSEEK_MODEL 环境变量覆盖配置默认值（真实冻结链路）。"""
    before = _live_guard_snapshot()
    monkeypatch.setenv("DEEPSEEK_MODEL", "deepseek-v4-pro")
    proj = _build_temp_project_tree(short_tmp)
    config = _minimal_config(short_tmp, default_model="deepseek-v4-flash")
    runs_root = short_tmp / "r2_runs"

    result = run_generation(
        config_path=config,
        runs_root=runs_root,
        as_of=date(2026, 8, 22),
        period_start=None,
        period_end=None,
        trigger_type="scheduled",
        check_only=True,
        force_regenerate=False,
        project_root=proj,
    )
    assert result["code"] == "CHECK_OK", result
    run = _run_record(runs_root)
    assert run["model"] == "deepseek-v4-pro"
    assert run["coverage_version"] == SYNTH_COVERAGE_NAME
    _assert_no_delivery(runs_root)
    _assert_live_unchanged(before)
