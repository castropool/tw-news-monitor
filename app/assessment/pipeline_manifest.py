"""Pipeline manifest 与 pipeline validation 构建。"""

from __future__ import annotations

from datetime import datetime
from typing import Any


PIPELINE_VERSION = "1.0.0"


def build_pipeline_manifest(
    *,
    run_id: str,
    mode: str,
    election_id: str,
    period_start: str,
    period_end: str,
    status: str,
    stages: list[dict],
    provider: str,
    model: str,
    delivery_provider: str,
    generation_mode: str,
    report_status: str,
    artifact_status: str,
    delivery_status: str,
    production_llm_ready: bool,
    delivery_preflight_ready: bool,
    formal_inputs_unchanged: bool,
    started_at: str,
    finished_at: str,
) -> dict:
    return {
        "pipeline_version": PIPELINE_VERSION,
        "run_id": run_id,
        "mode": mode,
        "election_id": election_id,
        "period_start": period_start,
        "period_end": period_end,
        "started_at": started_at,
        "finished_at": finished_at,
        "status": status,
        "stages": stages,
        "provider": provider,
        "model": model,
        "delivery_provider": delivery_provider,
        "generation_mode": generation_mode,
        "report_status": report_status,
        "artifact_status": artifact_status,
        "delivery_status": delivery_status,
        "production_llm_ready": production_llm_ready,
        "delivery_preflight_ready": delivery_preflight_ready,
        "formal_inputs_unchanged": formal_inputs_unchanged,
    }


def build_pipeline_validation(
    *,
    pipeline_ready: bool,
    errors: list[str],
    warnings: list[str],
    deployment_preflight_ready: bool,
    evidence_pack_ready: bool,
    llm_input_contract_ready: bool,
    report_generation_ready: bool,
    artifact_ready: bool,
    delivery_success: bool,
    formal_inputs_unchanged: bool,
    production_mode_allowed: bool,
    network_calls: int = 0,
) -> dict:
    return {
        "pipeline_ready": pipeline_ready,
        "errors": errors,
        "warnings": warnings,
        "deployment_preflight_ready": deployment_preflight_ready,
        "evidence_pack_ready": evidence_pack_ready,
        "llm_input_contract_ready": llm_input_contract_ready,
        "report_generation_ready": report_generation_ready,
        "artifact_ready": artifact_ready,
        "delivery_success": delivery_success,
        "formal_inputs_unchanged": formal_inputs_unchanged,
        "production_mode_allowed": production_mode_allowed,
        "network_calls": network_calls,
    }
