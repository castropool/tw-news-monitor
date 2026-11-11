"""报告生成总体校验聚合。"""

from __future__ import annotations

from typing import Any


def build_generation_validation(
    *,
    input_contract_ready: bool,
    evidence_pack_ready: bool,
    eligibility_respected: bool,
    provider_result_valid: bool,
    claim_validation: dict,
    final_report_allowed: bool,
    generated_mode: str,
    formal_unchanged: dict[str, bool],
    data_context_complete: bool = True,
    data_context_matches_input: bool = True,
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []
    if not input_contract_ready:
        errors.append("input_contract_ready: LLM 输入合同未通过")
    if not evidence_pack_ready:
        errors.append("evidence_pack_ready: 证据包未通过")
    if not eligibility_respected:
        errors.append("generation_eligibility_respected: 生成资格未被遵守")
    if not provider_result_valid:
        errors.append("provider_result_valid: provider 结果无效")
    if not data_context_complete:
        errors.append("data_context_complete: 数据上下文不完整")
    if not data_context_matches_input:
        errors.append("data_context_matches_input: 数据上下文与输入合同不一致")
    errors.extend(claim_validation.get("errors") or [])

    result: dict[str, Any] = {
        "report_generation_ready": not errors,
        "errors": errors,
        "warnings": warnings,
        "input_contract_ready": input_contract_ready,
        "evidence_pack_ready": evidence_pack_ready,
        "generation_eligibility_respected": eligibility_respected,
        "provider_result_valid": provider_result_valid,
        "structured_output_schema_valid": claim_validation.get("output_schema_valid", False),
        "all_claims_validated": claim_validation.get("all_claims_validated", False),
        "all_references_valid": (
            claim_validation.get("all_event_ids_exist", False)
            and claim_validation.get("all_poll_ids_exist", False)
            and claim_validation.get("all_source_ids_exist", False)
            and claim_validation.get("all_gap_ids_exist", False)
        ),
        "numeric_claims_grounded": claim_validation.get("numeric_claims_grounded", False),
        "date_claims_grounded": claim_validation.get("date_claims_grounded", False),
        "entity_claims_grounded": (
            claim_validation.get("person_names_grounded", False)
            and claim_validation.get("organization_names_grounded", False)
        ),
        "required_disclosures_complete": claim_validation.get("required_disclosures_complete", False),
        "data_context_complete": claim_validation.get("data_context_complete", False),
        "data_context_matches_input": claim_validation.get("data_context_matches_input", False),
        "do_not_infer_compliant": claim_validation.get("do_not_infer_compliant", False),
        "no_external_facts": claim_validation.get("no_external_facts", False),
        "no_unsupported_poll_claims": claim_validation.get("no_unsupported_poll_claims", False),
        "no_unsupported_probability": claim_validation.get("no_unsupported_probability", False),
        "final_report_allowed": final_report_allowed,
        "generated_report_mode": generated_mode,
        "formal_data_unchanged": formal_unchanged.get("formal_data_unchanged", False),
        "snapshot_data_unchanged": formal_unchanged.get("snapshot_data_unchanged", False),
        "coverage_data_unchanged": formal_unchanged.get("coverage_data_unchanged", False),
        "poll_data_unchanged": formal_unchanged.get("poll_data_unchanged", False),
        "evidence_package_unchanged": formal_unchanged.get("evidence_package_unchanged", False),
    }
    return result
