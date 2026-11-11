import json
import sys
from pathlib import Path

import pytest

import app.assessment.generate_llm_report as cli
from app.assessment.llm.base_provider import ProviderResult
from app.assessment.llm.mock_provider import MockProvider


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = PROJECT_ROOT / "config" / "election_assessment.yaml"
EVIDENCE_DIR = (
    PROJECT_ROOT
    / "data/reports/tainan_2026/evidence_packages/2026-07-16_2026-07-31"
)


def _run(argv, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["generate_llm_report"] + argv)
    return cli.main()


def _common_args(tmp_path):
    return [
        "--config", str(CONFIG),
        "--evidence-dir", str(EVIDENCE_DIR),
        "--provider", "mock",
        "--output-root", str(tmp_path),
    ]


class TestCli:
    def test_draft_default_rejected(self, tmp_path, monkeypatch):
        code = _run(_common_args(tmp_path), monkeypatch)
        assert code == 1
        assert not (tmp_path / "2026-07-16_2026-07-31" / "report_draft.md").exists()

    def test_draft_with_flag_succeeds(self, tmp_path, monkeypatch):
        code = _run(_common_args(tmp_path) + ["--allow-draft-with-gap"], monkeypatch)
        assert code == 0
        out = tmp_path / "2026-07-16_2026-07-31"
        for name in (
            "llm_request_payload.json",
            "report_output_schema.json",
            "prompt_manifest.json",
            "structured_report_attempt_1.json",
            "claim_evidence_validation_attempt_1.json",
            "structured_report_final.json",
            "report_draft.md",
            "report_generation_manifest.json",
            "report_generation_validation.json",
            "report_generation_idempotency.json",
            "request_correlation.json",
            "provider_response_metadata.json",
        ):
            assert (out / name).exists(), name
        final = json.loads((out / "structured_report_final.json").read_text(encoding="utf-8"))
        assert final["generation_mode"] == "draft_with_data_gap"
        assert final["report_status"] in ("generated", "repaired")
        validation = json.loads((out / "report_generation_validation.json").read_text(encoding="utf-8"))
        assert validation["report_generation_ready"] is True
        assert validation["final_report_allowed"] is False
        assert validation["generated_report_mode"] == "draft_with_data_gap"
        assert validation["required_disclosures_complete"] is True
        assert validation["do_not_infer_compliant"] is True

    def test_client_request_id_links_run_validation_and_manifest(self, tmp_path, monkeypatch):
        assert _run(_common_args(tmp_path) + ["--allow-draft-with-gap"], monkeypatch) == 0
        out = tmp_path / "2026-07-16_2026-07-31"
        correlation = json.loads((out / "request_correlation.json").read_text(encoding="utf-8"))
        validation = json.loads(
            (out / "claim_evidence_validation_attempt_1.json").read_text(encoding="utf-8")
        )
        manifest = json.loads(
            (out / "report_generation_manifest.json").read_text(encoding="utf-8")
        )
        metadata = json.loads(
            (out / "provider_response_metadata.json").read_text(encoding="utf-8")
        )
        client_id = correlation["client_request_id"]
        assert client_id
        assert validation["client_request_id"] == client_id
        assert manifest["client_request_id"] == client_id
        assert metadata["client_request_id"] == client_id
        assert manifest["provider_request_id"] == (manifest["response_id"] or None)
        prompt_payload = (out / "llm_request_payload.json").read_text(encoding="utf-8")
        assert client_id not in prompt_payload

    def test_validate_only_no_writes(self, tmp_path, monkeypatch):
        code = _run(
            _common_args(tmp_path) + ["--allow-draft-with-gap", "--validate-only"],
            monkeypatch,
        )
        assert code == 0
        assert not (tmp_path / "2026-07-16_2026-07-31").exists()

    def test_unrepairable_rejected(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            cli,
            "create_provider",
            lambda provider, config=None, model=None, **kwargs: MockProvider(fixture="unrepairable_invalid"),
        )
        code = _run(_common_args(tmp_path) + ["--allow-draft-with-gap"], monkeypatch)
        assert code == 1
        out = tmp_path / "2026-07-16_2026-07-31"
        final = json.loads((out / "structured_report_final.json").read_text(encoding="utf-8"))
        assert final["report_status"] == "rejected"
        assert not (out / "report_draft.md").exists()
        assert (out / "report_rejection_summary.md").exists()

    def test_repairable_repaired(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            cli,
            "create_provider",
            lambda provider, config=None, model=None, **kwargs: MockProvider(fixture="repairable_invalid"),
        )
        code = _run(_common_args(tmp_path) + ["--allow-draft-with-gap"], monkeypatch)
        assert code == 0
        out = tmp_path / "2026-07-16_2026-07-31"
        final = json.loads((out / "structured_report_final.json").read_text(encoding="utf-8"))
        assert final["report_status"] == "repaired"
        assert (out / "structured_report_attempt_2.json").exists()
        assert (out / "claim_evidence_validation_attempt_2.json").exists()

    def test_cache_reuse_and_force_model_call(self, tmp_path, monkeypatch):
        args = _common_args(tmp_path) + ["--allow-draft-with-gap"]
        assert _run(args, monkeypatch) == 0
        out = tmp_path / "2026-07-16_2026-07-31"
        m1 = json.loads((out / "report_generation_manifest.json").read_text(encoding="utf-8"))
        assert m1["cache_used"] is False
        assert _run(args, monkeypatch) == 0
        m2 = json.loads((out / "report_generation_manifest.json").read_text(encoding="utf-8"))
        assert m2["cache_used"] is True
        assert m2["provider_call_count"] == 0
        assert m2["generation_source"] == "cache"
        idem = json.loads((out / "report_generation_idempotency.json").read_text(encoding="utf-8"))
        assert idem["second_run_source"] == "cache"
        assert idem["business_outputs_equal"] is True
        assert _run(args + ["--force-model-call"], monkeypatch) == 0
        m3 = json.loads((out / "report_generation_manifest.json").read_text(encoding="utf-8"))
        assert m3["cache_used"] is False

    def test_preflight_written_for_mock_not_run(self, tmp_path, monkeypatch):
        assert _run(_common_args(tmp_path) + ["--allow-draft-with-gap"], monkeypatch) == 0
        preflight = json.loads(
            (
                PROJECT_ROOT
                / "data/reports/tainan_2026/deployment_validation/deepseek_production_preflight.json"
            ).read_text(encoding="utf-8")
        )
        assert preflight["live_deepseek_test"] == "not_run"
        assert preflight["preflight_ready"] is False
        assert preflight["production_llm_ready"] is False
        assert preflight["schedule_days"] == [9, 22]

    def test_openai_missing_key_fails(self, tmp_path, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        code = _run(
            _common_args(tmp_path)
            + ["--provider", "openai", "--allow-draft-with-gap", "--model", "gpt-test"],
            monkeypatch,
        )
        assert code == 1

    def test_deepseek_missing_key_fails(self, tmp_path, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        code = _run(
            _common_args(tmp_path)
            + ["--provider", "deepseek", "--allow-draft-with-gap", "--model", "deepseek-v4-flash"],
            monkeypatch,
        )
        assert code == 1

    def test_deepseek_mock_manifest_and_cache_separation(self, tmp_path, monkeypatch):
        def fake_create(provider, config=None, model=None, **kwargs):
            inner = MockProvider(fixture="valid_draft_with_gap")

            class _P:
                def generate_structured_report(self, *, system_prompt, user_payload, output_schema, request_metadata):
                    r = inner.generate_structured_report(
                        system_prompt=system_prompt,
                        user_payload=user_payload,
                        output_schema=output_schema,
                        request_metadata=request_metadata,
                    )
                    r.provider = "deepseek"
                    r.model = "deepseek-v4-flash"
                    return r

            return _P()

        monkeypatch.setattr(cli, "create_provider", fake_create)
        ds_out = tmp_path / "ds"
        assert (
            _run(
                _common_args(ds_out)
                + ["--provider", "deepseek", "--allow-draft-with-gap", "--model", "deepseek-v4-flash"],
                monkeypatch,
            )
            == 0
        )
        manifest = json.loads(
            (ds_out / "2026-07-16_2026-07-31" / "report_generation_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["provider"] == "deepseek"
        assert manifest["model"] == "deepseek-v4-flash"
        assert manifest["api_mode"] == "openai_compatible_chat_completions"
        assert manifest["json_output_mode"] == "json_object"
        assert manifest["thinking_mode"] == "disabled"
        assert manifest["server_side_strict_schema"] is False
        assert manifest["local_strict_schema_validation"] is True

        mock_out = tmp_path / "mock"
        assert _run(_common_args(mock_out) + ["--allow-draft-with-gap"], monkeypatch) == 0
        mock_manifest = json.loads(
            (mock_out / "2026-07-16_2026-07-31" / "report_generation_manifest.json").read_text(encoding="utf-8")
        )
        assert manifest["cache_key"] != mock_manifest["cache_key"]

    def test_invalid_provider_fails(self, tmp_path, monkeypatch):
        code = _run(_common_args(tmp_path) + ["--provider", "nope"], monkeypatch)
        assert code == 1

    def test_formal_and_evidence_package_unchanged(self, tmp_path, monkeypatch):
        before_pack = (EVIDENCE_DIR / "report_evidence_pack.json").read_bytes()
        assert _run(_common_args(tmp_path) + ["--allow-draft-with-gap"], monkeypatch) == 0
        after_pack = (EVIDENCE_DIR / "report_evidence_pack.json").read_bytes()
        assert before_pack == after_pack

    def test_secret_scan_detects_key_and_reasoning(self, tmp_path):
        from app.assessment.generate_llm_report import _scan_output_secrets

        (tmp_path / "bad.json").write_text('{"api_key": "sk-1234567890abcdef1234567890"}', encoding="utf-8")
        (tmp_path / "reason.txt").write_text("reasoning_content=hidden", encoding="utf-8")
        key_exposed, reasoning = _scan_output_secrets(tmp_path, None)
        assert key_exposed is True
        assert reasoning is True
