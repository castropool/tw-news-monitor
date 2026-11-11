"""Assessment LLM Adapter 测试（不触网）。"""

from __future__ import annotations

import pytest

from app.assessment.research_driven.adapter import (
    AdapterError,
    AssessmentLLMAdapter,
    _extract_json_object,
    resolve_research_llm_config,
)


CONFIG = {
    "llm": {
        "default_provider": "deepseek",
        "deepseek": {
            "api_key_env": "DEEPSEEK_API_KEY",
            "model_env": "DEEPSEEK_MODEL",
            "base_url": "https://api.deepseek.com",
            "default_model": "deepseek-v4-flash",
            "allowed_models": ["deepseek-v4-flash", "deepseek-v4-pro"],
        },
        "research_driven": {
            "provider": "deepseek",
            "temperature": 0.7,
            "max_output_tokens": 16000,
            "request_timeout_seconds": 300,
            "max_attempts": 2,
        },
    }
}


def test_config_resolution_defaults_and_overrides():
    cfg = resolve_research_llm_config(CONFIG, provider="deepseek", model="deepseek-v4-pro")
    assert cfg["provider"] == "deepseek"
    assert cfg["model"] == "deepseek-v4-pro"
    assert cfg["temperature"] == 0.7
    assert cfg["max_output_tokens"] == 16000
    assert cfg["timeout_seconds"] == 300


def test_config_rejects_model_outside_allowed_list():
    with pytest.raises(AdapterError):
        resolve_research_llm_config(CONFIG, provider="deepseek", model="gpt-4o")


def test_mock_valid_output():
    adapter = AssessmentLLMAdapter(CONFIG, provider="mock")
    payload = {"research_pack": {"period": {"period_start": "2026-07-16", "period_end": "2026-07-31"}, "period_events": []}}
    result = adapter.complete(system_prompt="s", user_payload=payload, json_mode=True)
    assert result.provider == "mock"
    assert result.structured is not None
    assert "analysis_plan" in result.structured
    assert "final_article" in result.structured


def test_mock_invalid_json_returns_raw_without_structured():
    adapter = AssessmentLLMAdapter(CONFIG, provider="mock")
    payload = {"research_pack": {}, "_mock_fixture": "invalid_json"}
    result = adapter.complete(system_prompt="s", user_payload=payload, json_mode=True)
    assert result.structured is None
    assert "not json" in result.raw_text


def test_mock_api_failure_raises():
    adapter = AssessmentLLMAdapter(CONFIG, provider="mock")
    payload = {"research_pack": {}, "_mock_fixture": "api_failure"}
    with pytest.raises(RuntimeError):
        adapter.complete(system_prompt="s", user_payload=payload, json_mode=True)


def test_extract_json_object_variants():
    assert _extract_json_object('{"a": 1}') == {"a": 1}
    assert _extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert _extract_json_object('前缀文字 {"a": 1} 后缀文字') == {"a": 1}
    with pytest.raises(AdapterError):
        _extract_json_object("完全不是 JSON")
