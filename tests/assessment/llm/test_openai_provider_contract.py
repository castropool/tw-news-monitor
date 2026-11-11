import pytest

from app.assessment.llm.openai_provider import OpenAIProvider
from app.assessment.llm.errors import LLMConfigurationError
from app.assessment.evidence_pack_builder import load_yaml
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG = load_yaml(PROJECT_ROOT / "config" / "election_assessment.yaml")


class TestOpenAIProviderContract:
    def test_missing_api_key_fails(self, monkeypatch):
        monkeypatch.delenv("OPENAI_API_KEY", raising=False)
        monkeypatch.setenv("OPENAI_MODEL", "gpt-test")
        provider = OpenAIProvider(config=CONFIG)
        with pytest.raises(LLMConfigurationError):
            provider.generate_structured_report(
                system_prompt="s",
                user_payload={},
                output_schema={},
                request_metadata={},
            )

    def test_missing_model_fails(self, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
        monkeypatch.delenv("OPENAI_MODEL", raising=False)
        provider = OpenAIProvider(config=CONFIG, model_override=None)
        with pytest.raises(LLMConfigurationError):
            provider.generate_structured_report(
                system_prompt="s",
                user_payload={},
                output_schema={},
                request_metadata={},
            )

    def test_external_tools_disabled_in_config(self):
        llm = CONFIG["llm"]
        assert llm["allow_external_tools"] is False
        assert llm["allow_web_search"] is False
        assert llm["allow_file_search"] is False

    def test_model_override_non_empty(self):
        assert CONFIG["llm"]["openai"]["model_env"] == "OPENAI_MODEL"
