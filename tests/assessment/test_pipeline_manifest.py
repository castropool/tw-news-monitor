from app.assessment.pipeline_manifest import (
    build_pipeline_manifest,
    build_pipeline_validation,
)


class TestPipelineManifest:
    def test_manifest_fields(self):
        manifest = build_pipeline_manifest(
            run_id="r1",
            mode="development",
            election_id="tainan_mayoral_2026",
            period_start="2026-07-16",
            period_end="2026-07-31",
            status="success",
            stages=[{"stage": "word_render", "status": "passed"}],
            provider="mock",
            model="mock-model",
            delivery_provider="mock",
            generation_mode="draft_with_data_gap",
            report_status="generated",
            artifact_status="ready",
            delivery_status="delivered",
            production_llm_ready=False,
            delivery_preflight_ready=True,
            formal_inputs_unchanged=True,
            started_at="2026-08-05T00:00:00",
            finished_at="2026-08-05T00:00:01",
        )
        assert manifest["pipeline_version"] == "1.0.0"
        assert manifest["status"] == "success"
        assert manifest["stages"][0]["stage"] == "word_render"
        assert manifest["production_llm_ready"] is False

    def test_blocked_manifest(self):
        manifest = build_pipeline_manifest(
            run_id="r1",
            mode="production",
            election_id="tainan_mayoral_2026",
            period_start="2026-07-16",
            period_end="2026-07-31",
            status="blocked",
            stages=[],
            provider="deepseek",
            model="",
            delivery_provider="feishu",
            generation_mode="",
            report_status="",
            artifact_status="not_attempted",
            delivery_status="not_attempted",
            production_llm_ready=False,
            delivery_preflight_ready=False,
            formal_inputs_unchanged=True,
            started_at="",
            finished_at="",
        )
        assert manifest["status"] == "blocked"

    def test_validation_fields(self):
        validation = build_pipeline_validation(
            pipeline_ready=True,
            errors=[],
            warnings=[],
            deployment_preflight_ready=True,
            evidence_pack_ready=True,
            llm_input_contract_ready=True,
            report_generation_ready=True,
            artifact_ready=True,
            delivery_success=True,
            formal_inputs_unchanged=True,
            production_mode_allowed=False,
            network_calls=0,
        )
        assert validation["pipeline_ready"] is True
        assert validation["network_calls"] == 0
