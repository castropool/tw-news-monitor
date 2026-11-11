import json
import logging

import pytest

from app.assessment.pipeline_state import (
    append_stage_result,
    atomic_write_json,
    create_run_dir,
    setup_pipeline_logger,
    write_failure_summary,
    write_latest,
)


class TestPipelineState:
    def test_create_run_dir(self, tmp_path):
        run_dir = create_run_dir(tmp_path, "2026-07-16", "2026-07-31", "abc")
        assert run_dir.exists()
        assert run_dir == tmp_path / "2026-07-16_2026-07-31" / "abc"

    def test_append_stage_result(self, tmp_path):
        run_dir = create_run_dir(tmp_path, "p", "q", "r")
        append_stage_result(run_dir, "build", "passed", payload={"x": 1})
        append_stage_result(run_dir, "build", "failed", error="boom")
        data = json.loads((run_dir / "stage_results.json").read_text(encoding="utf-8"))
        assert len(data["stages"]) == 2
        assert data["stages"][1]["status"] == "failed"
        assert data["stages"][1]["error"] == "boom"

    def test_write_failure_summary(self, tmp_path):
        run_dir = create_run_dir(tmp_path, "p", "q", "r")
        path = write_failure_summary(
            run_dir,
            failed_stage="delivery",
            error_category="delivery_failed",
            error_message="timeout",
            election_id="tainan_mayoral_2026",
            period_start="2026-07-16",
            period_end="2026-07-31",
            facts_cutoff="2026-07-27",
            poll_cutoff="2026-03-12",
            active_snapshot_id="tn_state_20260801_v1",
            coverage_version="fact_coverage_20260801_v4",
            local_draft_generated=True,
            suggested_actions=["retry"],
        )
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["failed_stage"] == "delivery"
        assert data["election_id"] == "tainan_mayoral_2026"
        assert data["facts_cutoff"] == "2026-07-27"
        assert data["poll_cutoff"] == "2026-03-12"
        assert data["active_snapshot_id"] == "tn_state_20260801_v1"
        assert data["coverage_version"] == "fact_coverage_20260801_v4"
        assert data["local_draft_generated"] is True
        assert data["suggested_actions"] == ["retry"]

    def test_write_latest_success_only(self, tmp_path):
        run_dir = create_run_dir(tmp_path, "p", "q", "r")
        manifest = {
            "run_id": "r",
            "period_start": "p",
            "period_end": "q",
            "mode": "development",
            "status": "success",
        }
        write_latest(tmp_path, run_dir, manifest)
        latest = json.loads((tmp_path / "latest.json").read_text(encoding="utf-8"))
        assert latest["run_id"] == "r"
        with pytest.raises(ValueError):
            write_latest(tmp_path, run_dir, {**manifest, "status": "failed"})

    def test_atomic_write_json(self, tmp_path):
        path = tmp_path / "a.json"
        atomic_write_json(path, {"k": "v"})
        assert json.loads(path.read_text(encoding="utf-8")) == {"k": "v"}
        assert not path.with_suffix(".json.tmp").exists()

    def test_logger_writes_log(self, tmp_path):
        run_dir = create_run_dir(tmp_path, "p", "q", "r")
        logger = setup_pipeline_logger(run_dir)
        logger.info("hello")
        for handler in logger.handlers:
            handler.flush()
        assert "hello" in (run_dir / "pipeline.log").read_text(encoding="utf-8")
