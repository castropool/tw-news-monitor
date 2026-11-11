import pytest

from app.assessment.report_prompt_builder import (
    PromptBuildError,
    build_cache_key,
    build_prompt_manifest,
    build_request_payload,
    load_output_schema,
    load_prompt,
    prompt_hashes,
)
from tests.assessment.llm.conftest import build_contract


class TestPromptBuilder:
    def test_payload_only_allowed_fields(self):
        contract = build_contract()
        contract["contract_definition"] = {"x": 1}
        payload = build_request_payload(contract)
        assert "contract_definition" not in payload
        assert "run_id" not in payload
        assert payload["election_id"] == "tainan_mayoral_2026"

    def test_prohibited_path_fails(self):
        contract = build_contract()
        contract["known_limitations"] = ["D:\\secret\\path"]
        with pytest.raises(PromptBuildError):
            build_request_payload(contract)

    def test_prompt_files_exist(self):
        for name in ("system", "writer", "repair"):
            assert len(load_prompt(name)) > 50
        assert prompt_hashes()

    def test_schema_loaded(self):
        schema = load_output_schema()
        assert schema["additionalProperties"] is False
        assert schema["properties"]["schema_version"]["const"] == "2.0"
        assert "core_assessments" in schema["properties"]
        assert "conclusion_summary" in schema["properties"]

    def test_cache_key_contains_components(self):
        base = dict(
            evidence_business_hash="e",
            contract_hash="c",
            system_prompt_hash="s",
            writer_prompt_hash="w",
            repair_prompt_hash="r",
            output_schema_hash="sc",
            provider="mock",
            model="m",
            base_url_identifier="mock",
            thinking_mode="disabled",
            json_output_mode="mock_json",
            generator_version="1.0.0",
            generation_mode="draft_with_data_gap",
        )
        key1 = build_cache_key(**base)
        key2 = build_cache_key(**{**base, "system_prompt_hash": "s2"})
        key3 = build_cache_key(**{**base, "output_schema_hash": "sc2"})
        key4 = build_cache_key(**{**base, "thinking_mode": "enabled"})
        key5 = build_cache_key(**{**base, "provider": "deepseek", "base_url_identifier": "https://api.deepseek.com"})
        assert key1 != key2
        assert key1 != key3
        assert key1 != key4
        assert key1 != key5
        assert len(key1) == 64

    def test_prompt_manifest_versions(self):
        m = build_prompt_manifest("mock", "m")
        assert m["prompt_versions"] == {"system": "2.0", "writer": "2.0", "repair": "2.0"}
