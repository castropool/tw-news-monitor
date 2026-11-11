import argparse
import json
from datetime import date
from pathlib import Path

import pytest

from app.assessment.pipeline_lock import PipelineLock
from app.assessment.run_assessment_pipeline import (
    _build_summary_text,
    _resolve_failure_data_context,
    main,
    run,
)
from app.assessment.evidence_pack_builder import load_yaml
from tests.assessment.llm.conftest import build_contract, make_report


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = PROJECT_ROOT / "config" / "election_assessment.yaml"


def _args(**overrides):
    base = dict(
        config=CONFIG,
        mode="development",
        as_of=None,
        period_start=None,
        period_end=None,
        provider=None,
        model=None,
        delivery_provider=None,
        delivery_fixture=None,
        allow_draft_with_gap=False,
        force_evidence_rebuild=False,
        force_model_call=False,
        skip_delivery=False,
        validate_only=False,
        output_root=None,
    )
    if isinstance(base.get("as_of"), str):
        base["as_of"] = date.fromisoformat(base["as_of"])
    base.update(overrides)
    if isinstance(base.get("as_of"), str):
        base["as_of"] = date.fromisoformat(base["as_of"])
    return argparse.Namespace(**base)


class TestRunAssessmentPipeline:
    def test_production_blocked_without_credentials(self, tmp_path, capsys):
        code = run(
            CONFIG,
            _args(
                mode="production",
                as_of=date(2026, 8, 9),
                output_root=tmp_path,
            ),
        )
        assert code != 0
        runs = list((tmp_path / "pipeline_runs" / "2026-07-16_2026-07-31").glob("*"))
        run_dir = max(runs, key=lambda p: p.stat().st_mtime)
        failure = json.loads((run_dir / "failure_summary.json").read_text(encoding="utf-8"))
        assert failure["failed_stage"] == "deployment_preflight"
        assert failure["facts_cutoff"] == "2026-07-27"
        assert failure["poll_cutoff"] == "2026-03-12"
        assert failure["active_snapshot_id"] == "tn_state_20260801_v1"
        assert failure["coverage_version"] == "fact_coverage_20260801_v4"
        assert failure["election_id"] == "tainan_mayoral_2026"
        assert isinstance(failure["suggested_actions"], list)
        assert failure["suggested_actions"]
        assert failure["data_context_resolution_error"] is None
        manifest = json.loads((run_dir / "pipeline_manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "blocked"
        assert not (tmp_path / "generated_reports").exists()
        assert not list(run_dir.glob("*.docx"))

    def test_production_mock_provider_rejected(self, tmp_path):
        code = run(CONFIG, _args(mode="production", provider="mock", output_root=tmp_path))
        assert code == 1

    def test_production_allow_draft_rejected(self, tmp_path):
        code = run(
            CONFIG,
            _args(mode="production", allow_draft_with_gap=True, output_root=tmp_path),
        )
        assert code == 1

    def test_production_skip_delivery_rejected(self, tmp_path):
        code = run(
            CONFIG,
            _args(mode="production", skip_delivery=True, output_root=tmp_path),
        )
        assert code == 1

    def test_unknown_provider_rejected(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "app.assessment.run_assessment_pipeline.REGISTERED_PROVIDERS",
            ("mock", "deepseek", "openai"),
        )
        args = _args(provider="unknown", output_root=tmp_path)
        code = run(CONFIG, args)
        assert code == 1

    def test_lock_conflict_returns_2(self, tmp_path):
        config = load_yaml(CONFIG)
        lock = PipelineLock(
            tmp_path / "locks",
            election_id=config["election"]["election_id"],
            period_start="2026-07-16",
            period_end="2026-07-31",
            mode="development",
        )
        assert lock.acquire() is True
        try:
            code = run(
                CONFIG,
                _args(
                    as_of=date(2026, 8, 9),
                    allow_draft_with_gap=True,
                    output_root=tmp_path,
                ),
            )
            assert code == 2
        finally:
            lock.release()

    def test_development_success_with_skip_delivery(self, tmp_path):
        code = run(
            CONFIG,
            _args(
                mode="development",
                as_of="2026-08-09",
                allow_draft_with_gap=True,
                skip_delivery=True,
                output_root=tmp_path,
            ),
        )
        assert code == 0
        runs = list((tmp_path / "pipeline_runs" / "2026-07-16_2026-07-31").glob("*"))
        run_dir = max(runs, key=lambda p: p.stat().st_mtime)
        validation = json.loads((run_dir / "delivery_validation.json").read_text(encoding="utf-8"))
        assert validation["skipped"] is True
        assert not (run_dir / "delivery_receipt.json").exists()

    def test_development_success_with_mock_delivery(self, tmp_path):
        code = run(
            CONFIG,
            _args(
                mode="development",
                as_of="2026-08-09",
                allow_draft_with_gap=True,
                output_root=tmp_path,
            ),
        )
        assert code == 0
        runs = list((tmp_path / "pipeline_runs" / "2026-07-16_2026-07-31").glob("*"))
        run_dir = max(runs, key=lambda p: p.stat().st_mtime)
        assert (run_dir / "delivery_receipt.json").exists()
        assert (run_dir / "pipeline_manifest.json").exists()
        assert (tmp_path / "pipeline_runs" / "latest.json").exists()
        assert list(run_dir.glob("*.docx"))

    def test_dry_run_success_no_network(self, tmp_path):
        code = run(
            CONFIG,
            _args(
                mode="dry_run",
                as_of="2026-08-09",
                allow_draft_with_gap=True,
                output_root=tmp_path,
            ),
        )
        assert code == 0
        runs = list((tmp_path / "pipeline_runs" / "2026-07-16_2026-07-31").glob("*"))
        run_dir = max(runs, key=lambda p: p.stat().st_mtime)
        delivery_validation = json.loads(
            (run_dir / "delivery_validation.json").read_text(encoding="utf-8")
        )
        assert delivery_validation["delivery_success"] is True
        assert delivery_validation["network_calls"] == 0
        receipt = json.loads((run_dir / "delivery_receipt.json").read_text(encoding="utf-8"))
        assert receipt["network_calls"] == 0
        manifest = json.loads((run_dir / "pipeline_manifest.json").read_text(encoding="utf-8"))
        assert manifest["status"] == "success"

    def test_failure_summary_contains_full_data_context_schema(self, tmp_path, capsys):
        code = run(
            CONFIG,
            _args(
                mode="production",
                as_of="2026-08-09",
                output_root=tmp_path,
            ),
        )
        assert code != 0
        runs = list((tmp_path / "pipeline_runs" / "2026-07-16_2026-07-31").glob("*"))
        run_dir = max(runs, key=lambda p: p.stat().st_mtime)
        failure = json.loads((run_dir / "failure_summary.json").read_text(encoding="utf-8"))
        required_keys = {
            "failed_stage",
            "error_category",
            "error_message",
            "election_id",
            "period_start",
            "period_end",
            "facts_cutoff",
            "poll_cutoff",
            "active_snapshot_id",
            "coverage_version",
            "local_draft_generated",
            "artifact_generated",
            "delivery_attempted",
            "log_filename",
            "alert_status",
            "suggested_actions",
        }
        assert required_keys <= set(failure)
        assert failure["facts_cutoff"] == "2026-07-27"
        assert failure["poll_cutoff"] == "2026-03-12"
        assert failure["active_snapshot_id"] == "tn_state_20260801_v1"
        assert failure["coverage_version"] == "fact_coverage_20260801_v4"
        assert isinstance(failure["suggested_actions"], list)
        assert failure["suggested_actions"]

    def test_delivery_failure_stops_pipeline(self, tmp_path):
        code = run(
            CONFIG,
            _args(
                mode="development",
                as_of="2026-08-09",
                allow_draft_with_gap=True,
                delivery_provider="mock",
                delivery_fixture="timeout",
                output_root=tmp_path,
            ),
        )
        assert code == 1
        runs = list((tmp_path / "pipeline_runs" / "2026-07-16_2026-07-31").glob("*"))
        run_dir = max(runs, key=lambda p: p.stat().st_mtime)
        failure = json.loads((run_dir / "failure_summary.json").read_text(encoding="utf-8"))
        assert failure["failed_stage"] == "delivery"
        assert (run_dir / "mock_alert_receipt.json").exists()
        assert not (tmp_path / "pipeline_runs" / "latest.json").exists()

    def test_pipeline_idempotency_two_runs(self, tmp_path):
        args = _args(
            mode="development",
            as_of="2026-08-09",
            allow_draft_with_gap=True,
            output_root=tmp_path,
        )
        assert run(CONFIG, args) == 0
        assert run(CONFIG, args) == 0
        runs = sorted(
            (tmp_path / "pipeline_runs" / "2026-07-16_2026-07-31").glob("*"),
            key=lambda p: p.stat().st_mtime,
        )
        assert len(runs) >= 2
        idem = json.loads((runs[-1] / "pipeline_idempotency.json").read_text(encoding="utf-8"))
        assert idem["report_business_equal"] is True
        assert idem["word_business_equal"] is True
        assert idem["formal_inputs_unchanged"] is True
        assert idem["idempotent"] is True

    def test_summary_contains_draft_warning(self):
        report = make_report(build_contract())
        pack = build_contract()
        summary = _build_summary_text(report, pack, "a.docx", "development")
        assert "【数据不完整草稿，请勿作为完整周期报告使用】" in summary
        assert report["title"] in summary

    def test_main_production_returns_nonzero(self, tmp_path, monkeypatch, capsys):
        monkeypatch.setattr(
            "sys.argv",
            [
                "run_assessment_pipeline",
                "--config",
                str(CONFIG),
                "--mode",
                "production",
                "--as-of",
                "2026-08-09",
                "--output-root",
                str(tmp_path),
            ],
        )
        assert main() == 1

    def test_failure_context_resolution_unavailable(self, tmp_path, monkeypatch):
        from datetime import date as _date

        config = load_yaml(CONFIG)

        def _boom(*args, **kwargs):
            raise RuntimeError("db unavailable")

        monkeypatch.setattr(
            "app.assessment.run_assessment_pipeline.load_formal_data", _boom
        )
        result = _resolve_failure_data_context(
            config,
            PROJECT_ROOT,
            type("P", (), {"period_start": _date(2026, 7, 16), "period_end": _date(2026, 7, 31)})(),
            tmp_path / "no_evidence_pack",
        )
        assert result["facts_cutoff"] is None
        assert result["poll_cutoff"] is None
        assert result["data_context_resolution_error"] is not None
