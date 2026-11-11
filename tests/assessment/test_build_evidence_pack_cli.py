import json
import sys
from pathlib import Path

import pytest

import app.assessment.build_evidence_pack as cli


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = PROJECT_ROOT / "config" / "election_assessment.yaml"


def _run(argv: list[str], monkeypatch: pytest.MonkeyPatch) -> int:
    monkeypatch.setattr(sys, "argv", ["build_evidence_pack"] + argv)
    return cli.main()


class TestCliArgRules:
    def test_as_of_and_period_conflict(self, monkeypatch):
        assert (
            _run(
                ["--as-of", "2026-08-01", "--period-start", "2026-07-16",
                 "--period-end", "2026-07-31"],
                monkeypatch,
            )
            == 1
        )

    def test_explicit_missing_end(self, monkeypatch):
        assert _run(["--period-start", "2026-07-16"], monkeypatch) == 1

    def test_explicit_reversed(self, monkeypatch):
        assert (
            _run(["--period-start", "2026-07-31", "--period-end", "2026-07-16"], monkeypatch)
            == 1
        )

    def test_old_schedule_day1_fails(self, monkeypatch):
        assert _run(["--as-of", "2026-08-01"], monkeypatch) == 1

    def test_old_schedule_day16_fails(self, monkeypatch):
        assert _run(["--as-of", "2026-08-16"], monkeypatch) == 1


class TestCliIntegration:
    def test_backfill_20260801_success_and_idempotent(self, tmp_path, monkeypatch):
        out = tmp_path / "out"
        code = _run(
            ["--config", str(CONFIG), "--election-id", "tainan_mayoral_2026",
             "--as-of", "2026-08-09", "--output-root", str(out)],
            monkeypatch,
        )
        assert code == 0
        period_dir = out / "2026-07-16_2026-07-31"
        for name in (
            "reporting_period.json",
            "report_evidence_pack.json",
            "report_evidence_pack.md",
            "report_run_manifest.json",
            "evidence_pack_validation.json",
            "evidence_pack_idempotency.json",
            "research_task_status_reconciliation.json",
            "snapshot_evidence_change_reconciliation.json",
            "gap_change_reconciliation.json",
            "state_diff_semantic_validation.json",
            "llm_input_contract.json",
            "llm_input_contract_validation.json",
        ):
            assert (period_dir / name).exists()
        validation = json.loads((period_dir / "evidence_pack_validation.json").read_text(encoding="utf-8"))
        assert validation["evidence_pack_ready"] is True
        for key in (
            "research_task_status_consistent",
            "state_diff_semantically_valid",
            "snapshot_reference_changes_distinguished",
            "formal_record_deletion_check_passed",
            "gap_changes_reconciled",
            "risk_changes_reconciled",
            "generation_eligibility_valid",
            "llm_input_contract_ready",
        ):
            assert validation[key] is True, key
        pack = json.loads((period_dir / "report_evidence_pack.json").read_text(encoding="utf-8"))
        assert pack["schema_version"] == "1.1"
        assert pack["report_period"]["period_start"] == "2026-07-16"
        assert pack["report_period"]["period_end"] == "2026-07-31"
        assert pack["data_status"]["active_snapshot_id"] == "tn_state_20260811_v2"
        # facts_cutoff 随人工审核推进（>= 报告期末即可，不锁定历史值）
        assert pack["data_status"]["facts_cutoff"] >= "2026-07-31"
        assert pack["data_status"]["poll_cutoff"] == "2026-03-12"
        assert pack["data_status"]["report_period_fully_covered_by_facts"] is True
        assert pack["data_status"]["uncovered_date_range"] == []
        rp = pack["report_period"]
        assert rp["scheduled_run_date"] == "2026-08-09"
        assert rp["calendar_lag_days"] == 9
        assert rp["full_preparation_days"] == 8
        assert rp["preparation_lag_days"] == 9
        assert rp["preparation_lag_semantics"] == "calendar_date_difference"
        assert rp["preparation_lag_deprecated"] is True
        assert rp["period_definition"] == "natural_half_month"
        assert rp["schedule_definition"] == "delayed_generation"
        elig = pack["generation_eligibility"]
        assert elig["final_report_allowed"] is True
        assert elig["allowed_generation_mode"] == "final"
        # 当前正式 coverage 已完成全部研究任务，因此活动任务数为 0。
        assert pack["evidence_statistics"]["active_research_task_count"] == 0
        assert pack["evidence_statistics"]["risk_change_count"] == len(pack["risk_changes"])
        contract = json.loads((period_dir / "llm_input_contract.json").read_text(encoding="utf-8"))
        assert contract["schema_version"] == "1.1"
        assert contract["contract_version"] == "1.0"
        contract_validation = json.loads(
            (period_dir / "llm_input_contract_validation.json").read_text(encoding="utf-8")
        )
        assert contract_validation["llm_input_contract_ready"] is True

        # 第二次执行必须幂等
        code2 = _run(
            ["--config", str(CONFIG), "--election-id", "tainan_mayoral_2026",
             "--as-of", "2026-08-09", "--output-root", str(out)],
            monkeypatch,
        )
        assert code2 == 0
        idem = json.loads(
            (period_dir / "evidence_pack_idempotency.json").read_text(encoding="utf-8")
        )
        assert idem["business_outputs_equal"] is True
        assert idem["formal_inputs_unchanged"] is True
        assert idem["idempotent"] is True

    def test_validate_only_writes_nothing(self, tmp_path, monkeypatch):
        out = tmp_path / "out"
        code = _run(
            ["--config", str(CONFIG), "--election-id", "tainan_mayoral_2026",
             "--as-of", "2026-08-09", "--output-root", str(out), "--validate-only"],
            monkeypatch,
        )
        assert code == 0
        assert not (out / "2026-07-16_2026-07-31").exists()
