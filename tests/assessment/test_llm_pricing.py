from pathlib import Path

from app.assessment.evidence_pack_builder import load_yaml
from app.assessment.generate_llm_report import _estimate_cost


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
PRICING = load_yaml(PROJECT_ROOT / "config" / "llm_pricing.yaml")


def _tokens(hit=1_000_000, miss=1_000_000, out=1_000_000):
    return {
        "prompt_cache_hit_tokens": hit,
        "prompt_cache_miss_tokens": miss,
        "output_token_count": out,
    }


class TestLlmPricing:
    def test_flash_pricing_ready(self):
        m = PRICING["providers"]["deepseek"]["models"]["deepseek-v4-flash"]
        assert m["input_cache_hit"] == 0.0028
        assert m["input_cache_miss"] == 0.14
        assert m["output"] == 0.28

    def test_pro_pricing_ready(self):
        m = PRICING["providers"]["deepseek"]["models"]["deepseek-v4-pro"]
        assert m["input_cache_hit"] == 0.003625
        assert m["input_cache_miss"] == 0.435
        assert m["output"] == 0.87

    def test_unit_per_1m_tokens(self):
        assert PRICING["unit"] == "per_1m_tokens"
        assert PRICING["currency"] == "USD"
        assert PRICING["pricing_schema_version"] == "1.0"
        assert PRICING["verified_at"] == "2026-08-05"

    def test_peak_not_effective(self):
        peak = PRICING["providers"]["deepseek"]["peak_pricing"]
        assert peak["effective"] is False
        assert peak["effective_date"] is None
        assert peak["apply_to_estimates"] is False

    def test_peak_not_applied_when_ineffective(self):
        cost = _estimate_cost("deepseek", "deepseek-v4-flash", _tokens(), PRICING)
        assert cost["pricing_peak_multiplier_applied"] is False
        assert cost["estimated_cost"] == 0.4228

    def test_unknown_model_cost_null(self):
        cost = _estimate_cost("deepseek", "unknown-model", _tokens(), PRICING)
        assert cost["estimated_cost"] is None
        assert cost["cost_estimation_status"] == "pricing_unavailable"

    def test_insufficient_usage_cost_null(self):
        tokens = {"prompt_cache_hit_tokens": None, "prompt_cache_miss_tokens": None, "output_token_count": 10}
        cost = _estimate_cost("deepseek", "deepseek-v4-flash", tokens, PRICING)
        assert cost["estimated_cost"] is None
        assert cost["cost_estimation_status"] == "insufficient_usage_breakdown"

    def test_full_usage_cost_correct(self):
        tokens = _tokens(hit=1_000_000, miss=2_000_000, out=500_000)
        cost = _estimate_cost("deepseek", "deepseek-v4-flash", tokens, PRICING)
        expected = 0.0028 + 2 * 0.14 + 0.5 * 0.28
        assert cost["estimated_cost"] == round(expected, 6)
        assert cost["cost_estimation_status"] == "estimated"

    def test_config_error_blocks_cost(self):
        bad = {
            "providers": {
                "deepseek": {
                    "models": {"deepseek-v4-flash": {"input_cache_hit": "oops", "input_cache_miss": 0.14, "output": 0.28}}
                }
            }
        }
        cost = _estimate_cost("deepseek", "deepseek-v4-flash", _tokens(), bad)
        assert cost["estimated_cost"] is None
        assert cost["cost_estimation_status"] == "pricing_config_error"

    def test_mock_cost(self):
        cost = _estimate_cost("mock", "mock-model", {}, PRICING)
        assert cost["estimated_cost"] == 0
        assert cost["estimated_cost_currency"] == "test"

    def test_peak_multiplier_only_when_effective(self):
        pricing = {
            "currency": "USD",
            "providers": {
                "deepseek": {
                    "models": {"deepseek-v4-flash": {"input_cache_hit": 1.0, "input_cache_miss": 1.0, "output": 1.0}},
                    "peak_pricing": {
                        "effective": True,
                        "effective_date": "2026-09-01",
                        "apply_to_estimates": True,
                        "multiplier": 2.0,
                    },
                }
            },
        }
        cost = _estimate_cost("deepseek", "deepseek-v4-flash", _tokens(hit=500_000, miss=500_000, out=500_000), pricing)
        assert cost["pricing_peak_multiplier_applied"] is True
        assert cost["estimated_cost"] == round((0.5 + 0.5 + 0.5) * 2.0, 6)

