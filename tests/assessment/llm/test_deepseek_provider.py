import json
from types import SimpleNamespace

import pytest

from app.assessment.evidence_pack_builder import load_yaml
from app.assessment.evidence_pack_builder import canonical_hash
from app.assessment.generate_llm_report import compose_deepseek_effective_system_prompt
from app.assessment.llm.deepseek_provider import DeepSeekProvider, LEGACY_MODELS
from app.assessment.report_output_schema import load_schema
from app.assessment.llm.errors import (
    DeepSeekEmptyContentError,
    DeepSeekJSONParseError,
    DeepSeekTruncatedOutputError,
    LLMAuthenticationError,
    LLMConfigurationError,
    LLMRateLimitError,
    LLMTimeoutError,
)
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
CONFIG = load_yaml(PROJECT_ROOT / "config" / "election_assessment.yaml")
_SENTINEL = object()


def _response(content, finish_reason="stop", usage=_SENTINEL):
    if usage is _SENTINEL:
        usage = SimpleNamespace(prompt_tokens=10, completion_tokens=20, total_tokens=30)
    return SimpleNamespace(
        id="resp-1",
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, reasoning_content="SECRET-REASONING"),
                finish_reason=finish_reason,
            )
        ],
        usage=usage,
    )


def _valid_response():
    return _response('{"schema_version": "1.0", "report_id": "r", "ok": true}')


class _FakeCompletions:
    def __init__(self, items):
        self.items = list(items)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        item = self.items.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


class _FakeChat:
    def __init__(self, items):
        self.completions = _FakeCompletions(items)


class _FakeClient:
    def __init__(self, items):
        self.chat = _FakeChat(items)


def _provider(items, **kwargs):
    return DeepSeekProvider(
        config=CONFIG,
        client=_FakeClient(items),
        **kwargs,
    )


def _generate(provider):
    return provider.generate_structured_report(
        system_prompt="s",
        user_payload={"x": 1},
        output_schema={},
        request_metadata={"attempt": 1},
    )


class TestDeepSeekProvider:
    def test_valid_init_and_default_model(self):
        p = DeepSeekProvider(config=CONFIG, client=_FakeClient([_valid_response()]))
        assert p._resolve_model() == "deepseek-v4-flash"

    def test_missing_api_key_fails(self, monkeypatch):
        monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
        p = DeepSeekProvider(config=CONFIG)
        with pytest.raises(LLMConfigurationError):
            _generate(p)

    def test_pro_model_allowed(self):
        p = _provider([_valid_response()], model_override="deepseek-v4-pro")
        assert p._resolve_model() == "deepseek-v4-pro"

    @pytest.mark.parametrize("legacy", ["deepseek-chat", "deepseek-reasoner"])
    def test_legacy_models_rejected(self, legacy):
        p = _provider([_valid_response()], model_override=legacy)
        with pytest.raises(LLMConfigurationError) as exc:
            p._resolve_model()
        assert "弃用" in str(exc.value)

    def test_invalid_model_rejected(self):
        p = _provider([_valid_response()], model_override="gpt-4")
        with pytest.raises(LLMConfigurationError):
            p._resolve_model()

    def test_base_url(self):
        p = DeepSeekProvider(config=CONFIG, client=_FakeClient([_valid_response()]))
        assert p.base_url == "https://api.deepseek.com"

    def test_uses_chat_completions_and_json_object(self):
        fake = _FakeClient([_valid_response()])
        p = DeepSeekProvider(config=CONFIG, client=fake)
        _generate(p)
        call = fake.chat.completions.calls[0]
        assert call["response_format"] == {"type": "json_object"}
        assert call["stream"] is False
        assert call["extra_body"] == {"thinking": {"type": "disabled"}}
        assert "tools" not in call

    def test_actual_request_contains_exact_loaded_schema_and_input_contract(self):
        fake = _FakeClient([_valid_response()])
        provider = DeepSeekProvider(config=CONFIG, client=fake)
        schema = load_schema()
        payload = {"contract_version": "1.0", "election_id": "tainan_mayoral_2026"}
        provider.generate_structured_report(
            system_prompt="system",
            user_payload=payload,
            output_schema=schema,
            request_metadata={"attempt": 1},
        )
        sent = json.loads(fake.chat.completions.calls[0]["messages"][1]["content"])
        assert sent["input_contract"] == payload
        assert sent["input_contract"]["contract_version"] == "1.0"
        assert sent["output_contract"]["schema_version"] == "1.1"
        assert sent["output_contract"]["json_schema"] == schema
        assert canonical_hash(sent["output_contract"]["json_schema"]) == canonical_hash(schema)

    def test_request_envelope_projects_only_formally_allowed_references(self):
        fake = _FakeClient([_valid_response()])
        payload = {
            "contract_version": "1.0",
            "period_events": [{"event_id": "evt_allowed", "source_ids": ["src_allowed"]}],
            "background_events": [],
            "polls": [{"poll_id": "poll_allowed", "source_ids": ["src_allowed"]}],
            "sources": [{"source_id": "src_allowed"}],
            "coverage_gaps": [{"gap_id": "gap_allowed"}],
            "state_diff": {"dimensions": [{"dimension": "organization"}]},
            "data_status": {"poll_cutoff": "2026-03-12"},
            "generation_eligibility": {
                "required_disclosures": ["正式事实底表仅覆盖至 2026-07-27"]
            },
            "research_tasks": [{"supporting_event_ids": ["evt_not_formally_allowed"]}],
        }
        DeepSeekProvider(config=CONFIG, client=fake).generate_structured_report(
            system_prompt="system",
            user_payload=payload,
            output_schema=load_schema(),
            request_metadata={"attempt": 1},
        )
        sent = json.loads(fake.chat.completions.calls[0]["messages"][1]["content"])
        allowed = sent["allowed_reference_ids"]
        assert allowed["event_ids"] == ["evt_allowed"]
        assert "evt_not_formally_allowed" not in allowed["event_ids"]
        assert allowed["source_ids"] == ["src_allowed"]
        assert sent["mandatory_disclosure_texts"] == [
            "正式事实底表仅覆盖至 2026-07-27",
            "正式民调截止至 2026-03-12",
            "本期没有新增正式民调",
        ]

    def test_actual_system_message_contains_complete_writer_prompt(self):
        fake = _FakeClient([_valid_response()])
        provider = DeepSeekProvider(config=CONFIG, client=fake)
        writer = "完整 writer prompt：八个章节、Claims、引用、披露。"
        effective = compose_deepseek_effective_system_prompt("system", writer)
        provider.generate_structured_report(
            system_prompt=effective,
            user_payload={"contract_version": "1.0"},
            output_schema=load_schema(),
            request_metadata={"attempt": 1},
        )
        sent_system = fake.chat.completions.calls[0]["messages"][0]["content"]
        assert sent_system == effective
        assert writer in sent_system

    def test_actual_system_message_contains_phase42_claim_contract(self):
        fake = _FakeClient([_valid_response()])
        writer = (
            PROJECT_ROOT
            / "app"
            / "assessment"
            / "prompts"
            / "tainan_report_writer_v1.txt"
        ).read_text(encoding="utf-8")
        effective = compose_deepseek_effective_system_prompt("system", writer)
        DeepSeekProvider(config=CONFIG, client=fake).generate_structured_report(
            system_prompt=effective,
            user_payload={"contract_version": "1.0"},
            output_schema=load_schema(),
            request_metadata={"attempt": 1, "client_request_id": "audit-only-id"},
        )
        sent = fake.chat.completions.calls[0]["messages"][0]["content"]
        assert sent == effective
        for required in (
            "Atomic Claim",
            "FACTUAL CLAIM",
            "ANALYTICAL CLAIM",
            "Actor Statement",
            "Allegation",
            "Claim strength",
            "evidence_assertions",
        ):
            assert required in sent
        assert "audit-only-id" not in sent

    def test_request_audit_records_schema_business_hash(self):
        schema = load_schema()
        result = DeepSeekProvider(
            config=CONFIG, client=_FakeClient([_valid_response()])
        ).generate_structured_report(
            system_prompt="system",
            user_payload={"contract_version": "1.0"},
            output_schema=schema,
            request_metadata={"attempt": 1},
        )
        assert result.request_audit["output_schema_business_hash"] == canonical_hash(schema)
        assert result.request_audit["output_schema_serialized_to_request"] is True
        assert result.request_audit["native_json_schema"] is False

    def test_client_request_id_is_auditable_and_never_enters_prompt(self):
        fake = _FakeClient([_valid_response()])
        result = DeepSeekProvider(config=CONFIG, client=fake).generate_structured_report(
            system_prompt="system",
            user_payload={"contract_version": "1.0"},
            output_schema=load_schema(),
            request_metadata={"attempt": 1, "client_request_id": "client-test-123"},
        )
        sent = json.dumps(fake.chat.completions.calls[0]["messages"], ensure_ascii=False)
        assert "client-test-123" not in sent
        assert result.client_request_id == "client-test-123"
        assert result.response_id == "resp-1"
        assert result.provider_request_id_supported is True
        assert result.request_audit["client_request_id"] == "client-test-123"
        assert result.request_audit["provider_request_id"] == "resp-1"

    def test_client_request_id_is_generated_when_caller_omits_it(self):
        result = _generate(_provider([_valid_response()]))
        assert result.client_request_id
        assert result.request_audit["client_request_id"] == result.client_request_id

    def test_markdown_fence_is_format_normalized(self):
        result = _generate(_provider([_response('```json\n{"ok": true}\n```')]))
        assert result.structured_output == {"ok": True}
        assert "provider_output_normalization:unwrapped_single_json_fence" in result.provider_warnings

    def test_multiple_json_objects_are_rejected(self):
        provider = _provider([_response('{"a": 1} {"b": 2}'), _response('{"a": 1} {"b": 2}')])
        with pytest.raises(DeepSeekJSONParseError):
            _generate(provider)

    def test_thinking_disabled_passed(self):
        fake = _FakeClient([_valid_response()])
        p = DeepSeekProvider(config=CONFIG, client=fake)
        _generate(p)
        assert fake.chat.completions.calls[0]["extra_body"]["thinking"]["type"] == "disabled"

    def test_empty_content_retry_once(self):
        fake = _FakeClient([_response(""), _valid_response()])
        p = DeepSeekProvider(config=CONFIG, client=fake)
        result = _generate(p)
        assert result.structured_output["ok"] is True
        assert len(fake.chat.completions.calls) == 2

    def test_empty_content_twice_fails(self):
        fake = _FakeClient([_response(""), _response("")])
        p = DeepSeekProvider(config=CONFIG, client=fake)
        with pytest.raises(DeepSeekEmptyContentError):
            _generate(p)
        assert len(fake.chat.completions.calls) == 2

    def test_invalid_json_retry_then_valid(self):
        fake = _FakeClient([_response("not json"), _valid_response()])
        p = DeepSeekProvider(config=CONFIG, client=fake)
        assert _generate(p).structured_output["ok"] is True

    def test_invalid_json_twice_fails(self):
        fake = _FakeClient([_response("bad"), _response("bad2")])
        p = DeepSeekProvider(config=CONFIG, client=fake)
        with pytest.raises(DeepSeekJSONParseError):
            _generate(p)

    def test_truncated_retry_then_valid(self):
        fake = _FakeClient([_response('{"a":1}', finish_reason="length"), _valid_response()])
        p = DeepSeekProvider(config=CONFIG, client=fake)
        assert _generate(p).structured_output["ok"] is True

    def test_truncated_twice_fails(self):
        fake = _FakeClient(
            [_response('{"a":1}', finish_reason="length"), _response('{"b":2}', finish_reason="length")]
        )
        p = DeepSeekProvider(config=CONFIG, client=fake)
        with pytest.raises(DeepSeekTruncatedOutputError):
            _generate(p)

    def test_max_two_generation_attempts(self):
        fake = _FakeClient([_response(""), _response(""), _response("")])
        p = DeepSeekProvider(config=CONFIG, client=fake)
        with pytest.raises(DeepSeekEmptyContentError):
            _generate(p)
        assert len(fake.chat.completions.calls) == 2

    def test_reasoning_content_not_saved(self):
        p = _provider([_valid_response()])
        result = _generate(p).to_dict()
        assert "reasoning_content" not in json.dumps(result, ensure_ascii=False)

    def test_usage_read_and_missing_null(self):
        fake = _FakeClient([_response("{}", usage=SimpleNamespace(prompt_tokens=7, completion_tokens=9, total_tokens=16))])
        result = _generate(DeepSeekProvider(config=CONFIG, client=fake))
        assert (result.input_token_count, result.output_token_count, result.total_token_count) == (7, 9, 16)
        assert result.prompt_cache_hit_tokens is None
        fake2 = _FakeClient([_response("{}", usage=None)])
        result2 = _generate(DeepSeekProvider(config=CONFIG, client=fake2))
        assert result2.input_token_count is None
        assert result2.output_token_count is None

    def test_cache_breakdown_tokens_read(self):
        usage = SimpleNamespace(
            prompt_tokens=1000,
            completion_tokens=200,
            total_tokens=1200,
            prompt_cache_hit_tokens=400,
            prompt_cache_miss_tokens=600,
        )
        result = _generate(DeepSeekProvider(config=CONFIG, client=_FakeClient([_response("{}", usage=usage)])))
        assert (result.prompt_cache_hit_tokens, result.prompt_cache_miss_tokens) == (400, 600)

    def test_authentication_error_mapping(self):
        class FakeAuthError(Exception):
            pass

        FakeAuthError.__name__ = "AuthenticationError"
        fake = _FakeClient([FakeAuthError("auth failed")])
        p = DeepSeekProvider(config=CONFIG, client=fake)
        with pytest.raises(LLMAuthenticationError) as exc:
            _generate(p)
        assert exc.value.provider_error_code == "authentication_error"
        assert "sk-" not in str(exc.value)

    def test_rate_limit_mapping(self):
        class FakeRateLimit(Exception):
            pass

        FakeRateLimit.__name__ = "RateLimitError"
        fake = _FakeClient([FakeRateLimit("slow down")])
        with pytest.raises(LLMRateLimitError):
            _generate(DeepSeekProvider(config=CONFIG, client=fake))

    def test_timeout_mapping(self):
        class FakeTimeout(Exception):
            pass

        FakeTimeout.__name__ = "APITimeoutError"
        fake = _FakeClient([FakeTimeout("too slow")])
        with pytest.raises(LLMTimeoutError):
            _generate(DeepSeekProvider(config=CONFIG, client=fake))

    def test_legacy_model_names_constant(self):
        assert LEGACY_MODELS == {"deepseek-chat", "deepseek-reasoner"}
