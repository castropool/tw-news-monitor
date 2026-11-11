import json
from datetime import date
from pathlib import Path

from app.assessment.build_evidence_pack import compute_input_hashes
from app.assessment.evidence_pack_builder import load_formal_data, load_yaml
from app.assessment.run_assessment_pipeline import run
from tests.assessment.test_run_assessment_pipeline import CONFIG, _args


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _formal_hashes():
    config = load_yaml(CONFIG)
    formal = load_formal_data(config, PROJECT_ROOT, config["election"]["election_id"])
    return compute_input_hashes(config, PROJECT_ROOT, formal.coverage_dir)


class TestAssessmentPipelineE2E:
    def test_development_full_chain(self, tmp_path):
        before = _formal_hashes()
        code = run(
            CONFIG,
            _args(
                mode="development",
                as_of=date(2026, 8, 9),
                provider="mock",
                delivery_provider="mock",
                allow_draft_with_gap=True,
                output_root=tmp_path,
            ),
        )
        assert code == 0
        runs = sorted(
            (tmp_path / "pipeline_runs" / "2026-07-16_2026-07-31").glob("*"),
            key=lambda p: p.stat().st_mtime,
        )
        run_dir = runs[-1]
        manifest = json.loads((run_dir / "pipeline_manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "success"
        assert manifest["generation_mode"] == "final"
        assert manifest["report_status"] in ("generated", "repaired")
        assert manifest["artifact_status"] == "ready"
        assert manifest["delivery_status"] == "delivered"
        assert manifest["production_llm_ready"] is False
        assert (run_dir / "delivery_receipt.json").exists()
        assert (run_dir / "artifact_validation.json").exists()
        assert (run_dir / "report_draft.md").exists()
        assert list(run_dir.glob("*.docx"))
        validation = json.loads((run_dir / "pipeline_validation.json").read_text(encoding="utf-8"))
        assert validation["pipeline_ready"] is True
        assert validation["network_calls"] == 0
        assert _formal_hashes() == before

    def test_dry_run_full_chain_no_network(self, tmp_path):
        before = _formal_hashes()
        code = run(
            CONFIG,
            _args(
                mode="dry_run",
                as_of=date(2026, 8, 9),
                provider="mock",
                delivery_provider="mock",
                allow_draft_with_gap=True,
                output_root=tmp_path,
            ),
        )
        assert code == 0
        runs = sorted(
            (tmp_path / "pipeline_runs" / "2026-07-16_2026-07-31").glob("*"),
            key=lambda p: p.stat().st_mtime,
        )
        run_dir = runs[-1]
        receipt = json.loads((run_dir / "delivery_receipt.json").read_text(encoding="utf-8"))
        assert receipt["network_calls"] == 0
        assert _formal_hashes() == before
        # dry_run 不安装计划任务（安装仅由 PowerShell -DryRun 调用，测试中无副作用）
        assert not list(run_dir.glob("*.ps1"))

    def test_production_blocked_end_to_end(self, tmp_path, monkeypatch):
        # 显式声明"本机无 LLM/飞书凭据"场景：结果不受真实 .env 或系统环境变量影响，
        # 同时保证失败告警因缺少飞书凭据而安全降级（零网络调用，不发真实通知）。
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        monkeypatch.delenv("FEISHU_APP_ID", raising=False)
        monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
        monkeypatch.delenv("FEISHU_CHAT_ID", raising=False)
        before = _formal_hashes()
        code = run(
            CONFIG,
            _args(
                mode="production",
                as_of=date(2026, 8, 9),
                output_root=tmp_path,
            ),
        )
        assert code != 0
        runs = sorted(
            (tmp_path / "pipeline_runs" / "2026-07-16_2026-07-31").glob("*"),
            key=lambda p: p.stat().st_mtime,
        )
        run_dir = runs[-1]
        manifest = json.loads((run_dir / "pipeline_manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "blocked"
        assert manifest["production_llm_ready"] is False
        failure = json.loads((run_dir / "failure_summary.json").read_text(encoding="utf-8"))
        joined = "；".join(
            [
                str(failure.get("error_message") or ""),
                str(json.dumps(failure.get("report_period") or {})),
            ]
        )
        # 门禁语义：生产前置检查被阻止，且缺 LLM 凭据被明确上报
        assert "生产前置检查未通过" in joined
        assert "DEEPSEEK_API_KEY" in joined
        assert not list(run_dir.glob("*.docx"))
        assert not (tmp_path / "pipeline_runs" / "latest.json").exists()
        assert _formal_hashes() == before

    def test_two_default_runs_idempotent(self, tmp_path):
        args = _args(
            mode="development",
            as_of=date(2026, 8, 9),
            provider="mock",
            delivery_provider="mock",
            allow_draft_with_gap=True,
            output_root=tmp_path,
        )
        assert run(CONFIG, args) == 0
        assert run(CONFIG, args) == 0
        runs = sorted(
            (tmp_path / "pipeline_runs" / "2026-07-16_2026-07-31").glob("*"),
            key=lambda p: p.stat().st_mtime,
        )
        idem = json.loads((runs[-1] / "pipeline_idempotency.json").read_text(encoding="utf-8"))
        assert idem["idempotent"] is True
        assert idem["first_run_business_hash"] == idem["second_run_business_hash"]
