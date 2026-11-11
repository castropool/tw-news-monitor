"""Phase 4.3 two-stage Claim Planner -> Writer pipeline.

The module is opt-in.  It performs no repair call and stops before Stage 2 when
the deterministic Claim Plan gate rejects Stage 1 output.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .claim_evidence_validator import build_evidence_context, validate_structured_report
from .claim_plan_schema import load_claim_plan_schema, load_stage2_draft_schema
from .claim_plan_validator import validate_claim_plan
from .claim_planner import (
    build_claim_planner_request,
    build_planner_envelope,
    load_claim_planner_prompt,
)
from .final_claim_coverage_validator import validate_final_claim_coverage
from .llm_input_contract import build_data_context
from .report_writer_stage2 import build_stage2_request, load_stage2_system_prompt
from .two_stage_report_assembler import assemble_final_report
from .validated_claim_store import build_stage2_input, build_validated_claim_store


PIPELINE_VERSION = "2.0.0-rc1"


def _hash(value: Any) -> str:
    if isinstance(value, str):
        raw = value
    else:
        raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _write(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def _provider_metadata(result: Any) -> dict:
    data = result.to_dict() if hasattr(result, "to_dict") else {}
    data.pop("structured_output", None)
    return data


def run_two_stage_generation(
    *,
    contract: dict,
    evidence_pack: dict | None,
    provider: Any,
    output_dir: Path,
    formal_state_hash: str,
    evidence_pack_hash: str,
    config: dict,
    run_id: str,
) -> dict:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    planner_prompt = load_claim_planner_prompt()
    claim_schema = load_claim_plan_schema()
    envelope = build_planner_envelope(
        contract,
        formal_state_hash=formal_state_hash,
        evidence_pack_hash=evidence_pack_hash,
    )
    stage1_request = build_claim_planner_request(envelope)
    stage1_client_id = f"{run_id}-stage1-01"
    stage1_prompt_hash = _hash(planner_prompt)
    stage1_schema_hash = _hash(claim_schema)
    stage1_result = provider.generate_structured_report(
        system_prompt=planner_prompt,
        user_payload=stage1_request,
        output_schema=claim_schema,
        request_metadata={
            "run_id": run_id,
            "stage": "claim_planner",
            "attempt": 1,
            "client_request_id": stage1_client_id,
            "effective_system_prompt_hash": stage1_prompt_hash,
            "output_schema_business_hash": stage1_schema_hash,
        },
    )
    raw_plan = stage1_result.structured_output
    _write(output_dir / "stage1_raw_output.json", raw_plan)
    validation = validate_claim_plan(
        raw_plan, contract=contract, planner_envelope=envelope, config=config
    )
    _write(output_dir / "claim_plan_validation.json", validation)
    store = build_validated_claim_store(
        raw_plan,
        validation,
        input_hashes={
            "formal_state_hash": formal_state_hash,
            "evidence_pack_hash": evidence_pack_hash,
        },
        prompt_hash=stage1_prompt_hash,
        schema_hash=stage1_schema_hash,
        provider_metadata=_provider_metadata(stage1_result),
        contract=contract,
    )
    _write(output_dir / "validated_claim_store.json", store)
    base = {
        "pipeline_version": PIPELINE_VERSION,
        "run_id": run_id,
        "stage1_call_count": 1,
        "stage2_call_count": 0,
        "llm_repair_call_count": 0,
        "semantic_repair_performed": False,
        "stage1_client_request_ids": [stage1_client_id],
        "stage1_effective_system_prompt_hash": stage1_prompt_hash,
        "stage1_output_schema_business_hash": stage1_schema_hash,
        "stage2_client_request_ids": [],
        "claim_plan_validation": validation,
        "validated_claim_store": store,
    }
    if validation.get("claim_plan_status") == "rejected":
        result = {**base, "two_stage_status": "stage1_rejected", "report_generation_not_started": True}
        _write(output_dir / "two_stage_run_manifest.json", result)
        return result

    stage2_input = build_stage2_input(store, data_context=build_data_context(contract))
    stage2_request = build_stage2_request(stage2_input)
    stage2_schema = load_stage2_draft_schema()
    stage2_prompt = load_stage2_system_prompt()
    stage2_prompt_hash = _hash(stage2_prompt)
    stage2_schema_hash = _hash(stage2_schema)
    stage2_client_id = f"{run_id}-stage2-01"
    stage2_result = provider.generate_structured_report(
        system_prompt=stage2_prompt,
        user_payload=stage2_request,
        output_schema=stage2_schema,
        request_metadata={
            "run_id": run_id,
            "stage": "report_writer_stage2",
            "attempt": 1,
            "client_request_id": stage2_client_id,
            "effective_system_prompt_hash": stage2_prompt_hash,
            "output_schema_business_hash": stage2_schema_hash,
        },
    )
    draft = stage2_result.structured_output
    _write(output_dir / "stage2_raw_output.json", draft)
    coverage = validate_final_claim_coverage(draft, store)
    _write(output_dir / "final_claim_coverage_validation.json", coverage)
    common = {
        **base,
        "stage2_call_count": 1,
        "stage2_client_request_ids": [stage2_client_id],
        "stage2_provider_metadata": _provider_metadata(stage2_result),
        "stage2_effective_system_prompt_hash": stage2_prompt_hash,
        "stage2_output_schema_business_hash": stage2_schema_hash,
        "final_claim_coverage_validation": coverage,
    }
    if not coverage.get("final_claim_coverage_ready"):
        result = {**common, "two_stage_status": "stage2_rejected"}
        _write(output_dir / "two_stage_run_manifest.json", result)
        return result

    report = assemble_final_report(draft, store=store, contract=contract)
    ctx = build_evidence_context(contract, evidence_pack=evidence_pack, config=config)
    expected_mode = (contract.get("generation_eligibility") or {}).get(
        "allowed_generation_mode", "draft_with_data_gap"
    )
    final_validation = validate_structured_report(
        report, ctx, expected_mode=expected_mode, assembled=True
    )
    _write(output_dir / "structured_report_final.json", report)
    _write(output_dir / "final_report_validation.json", final_validation)
    status = "passed" if final_validation.get("all_claims_validated") else "final_report_rejected"
    result = {
        **common,
        "two_stage_status": status,
        "final_report_validation": final_validation,
        "final_report": report,
    }
    manifest = {key: value for key, value in result.items() if key != "final_report"}
    _write(output_dir / "two_stage_run_manifest.json", manifest)
    return result
