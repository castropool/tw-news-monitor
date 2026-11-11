"""DeepSeek 生产就绪门禁与 live 输出人工检查。"""

from __future__ import annotations

from pathlib import Path
from typing import Any


def build_preflight(
    *,
    schedule_days: list[int],
    period_definition: str,
    schedule_definition: str,
    calendar_lag_semantics_valid: bool,
    full_preparation_days_semantics_valid: bool,
    default_provider: str,
    default_model: str,
    credentials_present: bool,
    live_deepseek_test: str,
    json_output_valid: bool,
    local_schema_valid: bool,
    claim_evidence_valid: bool,
    do_not_infer_valid: bool,
    required_disclosures_complete: bool,
    real_token_usage_available: bool,
    cost_estimation_status: str,
    cache_reuse_valid: bool,
    api_key_exposure_detected: bool,
    reasoning_content_persisted: bool,
    formal_data_unchanged: bool,
    evidence_package_unchanged: bool,
    formal_live_validation_pass_count: int = 0,
    required_formal_live_validation_passes: int = 2,
    formal_live_input_business_hash: str = "",
    formal_live_response_ids: list[str] | None = None,
    errors: list[str] | None = None,
    warnings: list[str] | None = None,
) -> dict:
    errors = list(errors or [])
    warnings = list(warnings or [])
    core_ok = (
        calendar_lag_semantics_valid
        and full_preparation_days_semantics_valid
        and default_provider == "deepseek"
        and json_output_valid
        and local_schema_valid
        and claim_evidence_valid
        and do_not_infer_valid
        and required_disclosures_complete
        and not api_key_exposure_detected
        and not reasoning_content_persisted
        and formal_data_unchanged
        and evidence_package_unchanged
    )
    if not credentials_present:
        errors.append("credentials_present: DEEPSEEK_API_KEY 未配置")
    if live_deepseek_test != "passed":
        errors.append(f"live_deepseek_test={live_deepseek_test}（生产门禁要求 passed）")
    if formal_live_validation_pass_count < required_formal_live_validation_passes:
        errors.append(
            "formal_live_stability: "
            f"同一冻结输入通过 {formal_live_validation_pass_count}/"
            f"{required_formal_live_validation_passes} 次"
        )
    if not cache_reuse_valid:
        errors.append("cache_reuse_valid: 缓存复用未验证")

    stability_ready = (
        formal_live_validation_pass_count >= required_formal_live_validation_passes
    )
    production_llm_ready = (
        core_ok
        and credentials_present
        and live_deepseek_test == "passed"
        and stability_ready
        and not errors
    )
    preflight_ready = production_llm_ready
    blocker = None
    if not production_llm_ready:
        blocker = (
            "provider_contract_incompatibility"
            if live_deepseek_test == "failed"
            else "formal_live_stability_not_satisfied"
        )
    return {
        "preflight_ready": preflight_ready,
        "errors": errors,
        "warnings": warnings,
        "schedule_days": schedule_days,
        "period_definition": period_definition,
        "schedule_definition": schedule_definition,
        "calendar_lag_semantics_valid": calendar_lag_semantics_valid,
        "full_preparation_days_semantics_valid": full_preparation_days_semantics_valid,
        "default_provider": default_provider,
        "default_model": default_model,
        "credentials_present": credentials_present,
        "live_deepseek_test": live_deepseek_test,
        "json_output_valid": json_output_valid,
        "local_schema_valid": local_schema_valid,
        "claim_evidence_valid": claim_evidence_valid,
        "do_not_infer_valid": do_not_infer_valid,
        "required_disclosures_complete": required_disclosures_complete,
        "real_token_usage_available": real_token_usage_available,
        "cost_estimation_status": cost_estimation_status,
        "cache_reuse_valid": cache_reuse_valid,
        "api_key_exposure_detected": api_key_exposure_detected,
        "reasoning_content_persisted": reasoning_content_persisted,
        "formal_data_unchanged": formal_data_unchanged,
        "evidence_package_unchanged": evidence_package_unchanged,
        "formal_live_validation_pass_count": formal_live_validation_pass_count,
        "required_formal_live_validation_passes": required_formal_live_validation_passes,
        "formal_live_stability_ready": stability_ready,
        "formal_live_input_business_hash": formal_live_input_business_hash,
        "formal_live_response_ids": list(formal_live_response_ids or []),
        "production_llm_ready": production_llm_ready,
        "blocker": blocker,
    }


def write_preflight(root: Path, preflight: dict) -> Path:
    out = root / "data" / "reports" / "tainan_2026" / "deployment_validation" / "deepseek_production_preflight.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        __import__("json").dumps(preflight, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


def render_live_review(report: dict, contract: dict, validation: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("# DeepSeek Live 输出人工检查")
    add("")
    add(f"## 标题\n{report.get('title') or ''}")
    add("")
    add("## 总体判断")
    claims = {c.get("claim_id"): c for c in (report.get("claims") or [])}
    for cid in report.get("overall_judgment_claim_ids") or []:
        c = claims.get(cid)
        if c:
            add(f"- [{cid}] {c.get('claim_text')}")
    add("")
    add("## Section 清单")
    for s in report.get("sections") or []:
        add(f"- {s.get('heading')} -> {', '.join(s.get('claim_ids') or [])}")
    add("")
    add("## Claim 清单与证据")
    for c in report.get("claims") or []:
        refs = []
        if c.get("supporting_event_ids"):
            refs.append("event:" + ",".join(c["supporting_event_ids"]))
        if c.get("supporting_poll_ids"):
            refs.append("poll:" + ",".join(c["supporting_poll_ids"]))
        if c.get("supporting_source_ids"):
            refs.append("source:" + ",".join(c["supporting_source_ids"]))
        if c.get("supporting_gap_ids"):
            refs.append("gap:" + ",".join(c["supporting_gap_ids"]))
        if c.get("supporting_snapshot_dimensions"):
            refs.append("dim:" + ",".join(c["supporting_snapshot_dimensions"]))
        add(f"- {c.get('claim_id')} [{c.get('claim_type')}] {c.get('claim_text')}")
        add(f"  - 证据: {'; '.join(refs) if refs else '无'}")
    add("")
    add("## Required Disclosures")
    for c in (report.get("claims") or []):
        if c.get("claim_type") == "data_disclosure":
            add(f"- {c.get('claim_text')}")
    add("")
    add("## 机器校验结果")
    add(f"- all_claims_validated: {validation.get('all_claims_validated')}")
    add(f"- required_disclosures_complete: {validation.get('required_disclosures_complete')}")
    add(f"- do_not_infer_compliant: {validation.get('do_not_infer_compliant')}")
    add(f"- errors: {validation.get('errors')}")
    return "\n".join(lines) + "\n"
