import json

import pytest

from app.assessment.llm.mock_provider import MOCK_FIXTURES, MockProvider
from app.assessment.llm.errors import LLMRateLimitError, LLMTimeoutError
from tests.assessment.llm.conftest import build_contract


def _generate(fixture):
    return MockProvider(fixture=fixture).generate_structured_report(
        system_prompt="s",
        user_payload=build_contract(),
        output_schema={},
        request_metadata={"attempt": 1},
    )


class TestMockProvider:
    def test_valid_output(self):
        r = _generate("valid_draft_with_gap")
        assert r.structured_output["schema_version"] == "1.1"
        assert "data_context" in r.structured_output
        assert r.structured_output["generation_mode"] == "draft_with_data_gap"
        assert r.provider == "mock"

    def test_valid_final_mode(self):
        r = _generate("valid_final")
        assert r.structured_output["generation_mode"] == "final"

    def test_valid_v2_contract(self):
        r = _generate("valid_v2")
        out = r.structured_output
        assert out["schema_version"] == "2.0"
        assert out["conclusion_summary"]
        assert 1 <= len(out["core_assessments"]) <= 3
        for unit in out["core_assessments"]:
            for key in (
                "judgment",
                "evidence_items",
                "evidence_refs",
                "reasoning",
                "falsifiers_or_limits",
                "confidence",
                "watch_indicators",
            ):
                assert key in unit
            assert 2 <= len(unit["evidence_items"]) <= 4

    def test_timeout(self):
        with pytest.raises(LLMTimeoutError):
            _generate("provider_timeout")

    def test_rate_limit(self):
        with pytest.raises(LLMRateLimitError):
            _generate("provider_rate_limit")

    def test_deterministic(self):
        a = _generate("valid_draft_with_gap").structured_output
        b = _generate("valid_draft_with_gap").structured_output
        assert a == b

    def test_no_api_key_in_result(self):
        r = _generate("valid_draft_with_gap").to_dict()
        text = json.dumps(r, ensure_ascii=False)
        assert "api_key" not in text.lower()
        assert "OPENAI_API_KEY" not in text

    def test_fixtures_registered(self):
        assert "invalid_unknown_event" in MOCK_FIXTURES
        assert "repairable_invalid" in MOCK_FIXTURES
