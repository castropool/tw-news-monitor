"""Command Code 模型定价与成本估算（订阅 credits，单位 $/1M tokens）。

数据来源：commandcode.ai/models 页（2026-08 抓取，off-peak/peak 两档）。
- DeepSeek V4 Pro：off-peak 0.66/1.98（in/out），peak 1.32/3.96，cache-read 0.044
- DeepSeek V4 Flash：off-peak 0.22/0.66，peak 0.44/1.32，cache-read ~0.007~0.33
"""

from __future__ import annotations

from dataclasses import dataclass

# 默认按 off-peak 估算（每天 17 小时为 off-peak）
MODEL_PRICING: dict[str, dict] = {
    "deepseek/deepseek-v4-pro": {
        "input_per_m": 0.66,
        "output_per_m": 1.98,
        "input_peak_per_m": 1.32,
        "output_peak_per_m": 3.96,
        "cache_read_per_m": 0.044,
    },
    "deepseek/deepseek-v4-flash": {
        "input_per_m": 0.22,
        "output_per_m": 0.66,
        "input_peak_per_m": 0.44,
        "output_peak_per_m": 1.32,
        "cache_read_per_m": 0.007,
    },
}


@dataclass
class CostBreakdown:
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    input_cost: float = 0.0
    output_cost: float = 0.0
    total_cost: float = 0.0
    calls: int = 0
    peak: bool = False

    def to_dict(self) -> dict:
        return {
            "model": self.model,
            "calls": self.calls,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "input_cost_usd": round(self.input_cost, 6),
            "output_cost_usd": round(self.output_cost, 6),
            "total_cost_usd": round(self.total_cost, 6),
            "peak": self.peak,
        }


def estimate_cost(
    model: str,
    input_tokens: int,
    output_tokens: int,
    *,
    peak: bool = False,
) -> float:
    """按给定模型与用量估算 credits 成本（美元）。"""
    pricing = MODEL_PRICING.get(model)
    if not pricing:
        return 0.0
    if peak:
        input_rate = pricing["input_peak_per_m"]
        output_rate = pricing["output_peak_per_m"]
    else:
        input_rate = pricing["input_per_m"]
        output_rate = pricing["output_per_m"]
    return (input_tokens / 1_000_000) * input_rate + (output_tokens / 1_000_000) * output_rate


def normalize_model_id(model: str) -> str:
    """接受短名（flash/pro/deepseek-v4-flash）或完整 id（deepseek/deepseek-v4-flash）。"""
    model = model.strip()
    if "/" in model:
        return model
    aliases = {
        "pro": "deepseek/deepseek-v4-pro",
        "flash": "deepseek/deepseek-v4-flash",
        "deepseek-pro": "deepseek/deepseek-v4-pro",
        "deepseek-flash": "deepseek/deepseek-v4-flash",
        "deepseek-v4-pro": "deepseek/deepseek-v4-pro",
        "deepseek-v4-flash": "deepseek/deepseek-v4-flash",
        "v4-pro": "deepseek/deepseek-v4-pro",
        "v4-flash": "deepseek/deepseek-v4-flash",
    }
    return aliases.get(model, model)
