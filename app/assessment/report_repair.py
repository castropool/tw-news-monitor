"""一次自动修复：只使用原输出、验证错误、合同与 Schema。"""

from __future__ import annotations

import json
from typing import Any

from .report_prompt_builder import load_prompt


REPAIRABLE_MARKERS = (
    "未知 event_id",
    "未知 poll_id",
    "未知 source_id",
    "未知 gap_id",
    "缺少必需字段",
    "缺少字段",
    "claim_type 非法",
    "title 引用不存在",
    "section 引用不存在",
    "缺少 inference_basis",
    "无证据依据",
    "required disclosures 不完整",
    "未披露",
    "违反 do_not_infer",
    "缺少披露",
    "事实",
    # v2.0 研判单元契约结构错误（可修复：按契约重写对应单元）
    "report_structure",
    "结论摘要",
    "核心研判",
    "reasoning",
    "falsifiers_or_limits",
    "watch_indicators",
    "重复堆叠",
    "零信息",
    "可证伪",
)

UNREPAIRABLE_MARKERS = (
    "认证失败",
    "超时",
    "限流",
    "配置错误",
    "证据包不合法",
    "合同缺少",
    "generation_mode 非法",
    "期望",
    "无法解析",
)


def is_repairable(validation: dict, provider_error: str | None = None) -> bool:
    if provider_error:
        return False
    errors = " ".join(validation.get("errors") or [])
    if not errors:
        return False
    if any(marker in errors for marker in UNREPAIRABLE_MARKERS):
        return False
    return True


def build_repair_user_payload(
    *,
    original_report: dict,
    validation: dict,
    contract: dict,
    output_schema: dict,
) -> dict:
    return {
        "original_structured_output": original_report,
        "structured_validation_errors": validation.get("errors") or [],
        "llm_input_contract": contract,
        "output_schema": output_schema,
    }


def build_repair_messages(
    *,
    original_report: dict,
    validation: dict,
    contract: dict,
    output_schema: dict,
) -> tuple[str, dict]:
    system_prompt = load_prompt("repair")
    user_payload = build_repair_user_payload(
        original_report=original_report,
        validation=validation,
        contract=contract,
        output_schema=output_schema,
    )
    return system_prompt, user_payload

