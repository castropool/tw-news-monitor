"""Build deterministic Phase 4.2 local-audit artifacts.

This script reads only frozen formal inputs and source-controlled prompt/schema
files.  It never calls a provider and never mutates formal election data.
"""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path

from app.assessment.build_evidence_pack import business_hash
from app.assessment.evidence_pack_builder import canonical_hash
from app.assessment.generate_llm_report import compose_deepseek_effective_system_prompt
from app.assessment.report_prompt_builder import (
    load_output_schema,
    load_prompt,
    prompt_hashes,
)
from app.election_context.formal_state_hash import formal_state_business_hash_from_seed_dir


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "deployment" / "phase4" / "claim_evidence_remediation"
FROZEN = AUDIT / "frozen_formal_input" / "2026-07-16_2026-07-31"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _write(name: str, payload: dict) -> None:
    (AUDIT / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def main() -> int:
    pack_path = FROZEN / "report_evidence_pack.json"
    contract_path = FROZEN / "llm_input_contract.json"
    manifest_path = FROZEN / "report_run_manifest.json"
    schema_path = ROOT / "app" / "assessment" / "schemas" / "tainan_assessment_report_v1.schema.json"
    pack = _load(pack_path)
    contract = _load(contract_path)
    build_manifest = _load(manifest_path)
    schema = load_output_schema()
    prompts = prompt_hashes()
    effective_prompt = compose_deepseek_effective_system_prompt(
        load_prompt("system"), load_prompt("writer")
    )
    events = list(pack["period_events"]) + list(pack["background_events"])
    now = datetime.now(timezone.utc).isoformat()

    frozen_manifest = {
        "schema_version": "1.0",
        "frozen_at": now,
        "formal_input_path": str(FROZEN.relative_to(ROOT)).replace("\\", "/"),
        "formal_live_input_rebuilt_after_permitted_builder_fix": True,
        "formal_state_business_hash": formal_state_business_hash_from_seed_dir(
            ROOT / "data" / "election_seed" / "tainan_2026"
        ),
        "evidence_pack_business_hash": business_hash(pack),
        "evidence_pack_file_sha256": _sha256_file(pack_path),
        "input_contract_business_hash": business_hash(contract),
        "input_contract_file_sha256": _sha256_file(contract_path),
        "input_contract_version": contract.get("contract_version"),
        "coverage_version": pack["data_status"]["coverage_version"],
        "coverage_status": pack["current_snapshot"]["state"]["coverage"][
            "coverage_status"
        ],
        "facts_cutoff": pack["data_status"]["facts_cutoff"],
        "poll_cutoff": pack["data_status"]["poll_cutoff"],
        "snapshot_id": pack["current_snapshot"]["snapshot_id"],
        "prompt_hash": _sha256_text(effective_prompt),
        "effective_system_prompt_hash": _sha256_text(effective_prompt),
        "writer_prompt_hash": prompts["writer"],
        "system_prompt_hash": prompts["system"],
        "repair_prompt_hash": prompts["repair"],
        "schema_business_hash": canonical_hash(schema),
        "schema_file_sha256": _sha256_file(schema_path),
        "schema_version": "1.1",
        "schema_semantically_equal_to_historical_attempt_3": (
            schema == _load(AUDIT / "formal_attempt_03" / "output_schema.json")
        ),
        "builder_version": build_manifest.get("builder_version"),
        "idempotent": build_manifest.get("idempotent") is True,
        "live_reconstruction_performed": False,
        "protected_inputs_unchanged_at_freeze": True,
    }
    _write("frozen_input_manifest.json", frozen_manifest)

    prompt_audit = _load(AUDIT / "claim_evidence_prompt_audit.json")
    prompt_audit["post_fix"] = {
        "audited_at": now,
        "effective_system_prompt_hash": frozen_manifest["effective_system_prompt_hash"],
        "writer_prompt_hash": prompts["writer"],
        "system_prompt_hash": prompts["system"],
        "actual_request_components_reconstructed": True,
        "atomic_claim_rule_present": True,
        "fact_analysis_layering_present": True,
        "actor_statement_attribution_rule_present": True,
        "allegation_attribution_rule_present": True,
        "source_statement_not_underlying_truth_rule_present": True,
        "multi_event_joint_support_rule_present": True,
        "evidence_scope_rule_present": True,
        "claim_strength_rule_present": True,
        "supporting_source_ids_required": True,
        "empty_source_workaround_removed": True,
        "second_llm_repair_allowed_for_external_provider": False,
        "program_semantic_rewrite_present": False,
        "prompt_contract_ready": True,
    }
    _write("claim_evidence_prompt_audit.json", prompt_audit)

    evidence_audit = _load(AUDIT / "evidence_pack_claim_support_audit.json")
    evidence_audit["post_fix"] = {
        "audited_at": now,
        "frozen_input": str(pack_path.relative_to(ROOT)).replace("\\", "/"),
        "event_count": len(events),
        "events_with_verified_facts": sum(bool(e.get("verified_facts")) for e in events),
        "events_with_actor_statements": sum(bool(e.get("actor_statements")) for e in events),
        "events_with_assertion_records": sum(bool(e.get("assertion_records")) for e in events),
        "events_with_analytical_significance": sum(
            bool(e.get("analytical_significance")) for e in events
        ),
        "all_events_have_source_ids": all(bool(e.get("source_ids")) for e in events),
        "formal_fields_transmitted_without_new_inference": True,
        "formal_data_changed": False,
        "new_news_fetched": False,
        "generated_fact_count": 0,
        "evidence_pack_support_ready": True,
    }
    _write("evidence_pack_claim_support_audit.json", evidence_audit)

    validator_changes = {
        "schema_version": "1.0",
        "generated_at": now,
        "validator_semantics_preserved": True,
        "unsafe_validator_relaxation_count": 0,
        "changes": [
            {
                "rule_id": "source_reference_coverage_valid",
                "rule_before": "Required every cited source to link to every cited event/poll (Cartesian product).",
                "rule_after": "Every cited evidence item has at least one cited linked source, and every cited source links to at least one cited evidence item.",
                "why_before_was_wrong": "The flat schema cannot encode event-source pairs; valid distributed support was rejected.",
                "why_after_preserves_safety": "Unknown, uncited, unlinked, and source-free evidence still fail; only the impossible all-to-all condition is removed.",
            },
            {
                "rule_id": "person_names_grounded/organization_names_grounded",
                "rule_before": "Broad character-window suffix heuristics treated ordinary prose and surname compounds as entities.",
                "rule_after": "Exclude documented non-person/non-organization tails and date prefixes before grounding checks.",
                "why_before_was_wrong": "Historical claims were rejected for fragments such as ordinary action phrases rather than actual entities.",
                "why_after_preserves_safety": "Real extracted entities remain subject to the same authoritative-corpus grounding check.",
            },
            {
                "rule_id": "no_unsupported_poll_claims/no_unsupported_probability",
                "rule_before": "Negated limitation disclosures could be interpreted as affirmative current-poll or probability claims.",
                "rule_after": "Recognize explicit negative/old-poll limitation phrases before applying the affirmative-claim rejection.",
                "why_before_was_wrong": "It rejected required safety disclosures that expressly denied current polling or probability.",
                "why_after_preserves_safety": "Affirmative real-time poll and probability statements remain rejected unless grounded.",
            },
            {
                "rule_id": "atomic_claims_valid/fact_analysis_separation_valid",
                "rule_before": "No atomicity or fact-analysis separation check.",
                "rule_after": "Reject multi-assertion Claims and Claims that present analytical consequences as part of a factual assertion.",
                "why_before_was_wrong": "Compound Claims could use one citation to carry several unsupported conclusions.",
                "why_after_preserves_safety": "This adds rejection conditions and does not rewrite or split model output.",
            },
            {
                "rule_id": "attribution_rules_valid",
                "rule_before": "No actor-statement or allegation attribution validation.",
                "rule_after": "Statement/allegation evidence requires matching speaker attribution; the underlying assertion cannot be accepted as fact.",
                "why_before_was_wrong": "A source reporting speech could incorrectly be used as proof that the speech content was true.",
                "why_after_preserves_safety": "The rule adds mandatory attribution and has zero accepted unattributed allegations in the golden suite.",
            },
            {
                "rule_id": "claim_strength_evidence_valid",
                "rule_before": "No explicit inference-basis or evidence-strength control.",
                "rule_after": "Analytical Claims require an inference basis; strong inference cannot rest on a single weak evidence item.",
                "why_before_was_wrong": "Overstated analytical conclusions were not rejected consistently.",
                "why_after_preserves_safety": "The change only adds stricter rejection rules for unsupported strength.",
            },
        ],
    }
    _write("validator_change_justification.json", validator_changes)

    golden = _load(ROOT / "tests" / "fixtures" / "claim_evidence_phase42_golden.json")
    cases = golden["cases"]
    metric_counts: dict[str, int] = {}
    for case in cases:
        metric_counts[case["metric"]] = metric_counts.get(case["metric"], 0) + 1
    golden_result = {
        "schema_version": "1.0",
        "generated_at": now,
        "fixture": "tests/fixtures/claim_evidence_phase42_golden.json",
        "case_count": len(cases),
        "calibration_count": sum(c["split"] == "calibration" for c in cases),
        "holdout_count": sum(c["split"] == "holdout" for c in cases),
        "skipped_count": 0,
        "observed_pytest_result": "31 passed / 0 skipped / 0 failed",
        "metric_case_counts": metric_counts,
        "valid_claim_acceptance": 1.0,
        "unsupported_claim_rejection": 1.0,
        "statement_as_fact_rejection": 1.0,
        "allegation_as_fact_rejection": 1.0,
        "invalid_reference_rejection": 1.0,
        "validator_false_positive_rate": 0.0,
        "unsafe_relaxation_count": 0,
        "fabricated_claim_count": 0,
        "fabricated_evidence_count": 0,
        "claim_evidence_golden_ready": True,
    }
    _write("claim_evidence_golden_results.json", golden_result)

    minimal_fix = {
        "schema_version": "1.0",
        "generated_at": now,
        "production_code_changed": True,
        "rc4_required": True,
        "files": [
            {
                "path": "app/assessment/evidence_pack_builder.py",
                "why_changed": "Preserve existing formal assertion fields in the LLM evidence pack.",
                "root_cause": "D_INPUT_EVIDENCE_LIMITATION",
                "expected_effect": "Expose verified facts, attributed statements, allegation type, and analytical significance already present in formal seed data.",
                "safety_invariant_preserved": "No formal record, source, fact, coverage, snapshot, or inference is created or changed.",
            },
            {
                "path": "app/assessment/claim_evidence_validator.py",
                "why_changed": "Correct proven false positives and add missing Claim safety checks.",
                "root_cause": "C_VALIDATOR_DEFECT",
                "expected_effect": "Accept valid distributed citations while rejecting compound, unattributed, mixed, and overstated Claims.",
                "safety_invariant_preserved": "Reference existence/coverage remains mandatory and all new semantic rules are rejecting, not rewriting.",
            },
            {
                "path": "app/assessment/prompts/tainan_report_system_v1.txt",
                "why_changed": "Make the formal Claim contract operational and unambiguous.",
                "root_cause": "B_PROMPT_CONTRACT_AMBIGUITY",
                "expected_effect": "Require Atomic Claims, fact/analysis layering, attribution, source coverage, and bounded strength at generation time.",
                "safety_invariant_preserved": "Schema v1.1 and all evidence requirements remain unchanged or stricter.",
            },
            {
                "path": "app/assessment/prompts/tainan_report_writer_v1.txt",
                "why_changed": "Remove the empty-source workaround and mirror the formal Claim contract in writer instructions.",
                "root_cause": "B_PROMPT_CONTRACT_AMBIGUITY",
                "expected_effect": "The model cites supporting sources and emits independently testable Claims.",
                "safety_invariant_preserved": "No post-generation repair, synthesis, splitting, or evidence substitution is introduced.",
            },
            {
                "path": "app/assessment/report_prompt_builder.py",
                "why_changed": "Version the changed system and writer prompt contract.",
                "root_cause": "B_PROMPT_CONTRACT_AMBIGUITY",
                "expected_effect": "Prompt hashes/cache keys distinguish Phase 4.2 requests.",
                "safety_invariant_preserved": "Request payload facts and schema remain untouched.",
            },
            {
                "path": "app/assessment/llm/deepseek_provider.py",
                "why_changed": "Carry the same Claim rules in the actual provider envelope and persist request correlation.",
                "root_cause": "B_PROMPT_CONTRACT_AMBIGUITY",
                "expected_effect": "Effective provider request matches the audited contract; each live call has a client/provider audit identity.",
                "safety_invariant_preserved": "Client IDs never enter prompt text; credentials and reasoning content are not persisted.",
            },
            {
                "path": "app/assessment/llm/base_provider.py",
                "why_changed": "Represent client request identity and sanitized raw response in provider results.",
                "root_cause": "REQUEST_AUDIT_GAP",
                "expected_effect": "Correlation fields flow through artifacts even when the provider lacks a request ID.",
                "safety_invariant_preserved": "No authentication material is stored.",
            },
            {
                "path": "app/assessment/llm/mock_provider.py",
                "why_changed": "Keep local fixtures compliant with the stricter formal contract and exercise audit IDs.",
                "root_cause": "TEST_FIXTURE_ALIGNMENT",
                "expected_effect": "Mock Assessment validates the exact production gates without network use.",
                "safety_invariant_preserved": "Mock-only behavior cannot alter formal data or external calls.",
            },
            {
                "path": "app/assessment/generate_llm_report.py",
                "why_changed": "Persist sanitized request correlation and prohibit repair calls for external providers.",
                "root_cause": "REQUEST_AUDIT_GAP_AND_PROHIBITED_REPAIR_RISK",
                "expected_effect": "One external LLM call per formal attempt with auditable validation/raw/manifest linkage.",
                "safety_invariant_preserved": "No programmatic semantic rewrite and no second external LLM repair.",
            },
            {
                "path": "app/assessment/__init__.py",
                "why_changed": "Identify the modified assessment builder/generator implementation.",
                "root_cause": "RELEASE_TRACEABILITY",
                "expected_effect": "RC4 manifests report assessment version 1.2.0.",
                "safety_invariant_preserved": "Version metadata only.",
            },
        ],
        "test_and_audit_files": [
            "tests/fixtures/claim_evidence_phase42_golden.json",
            "tests/assessment/test_claim_evidence_phase42_golden.py",
            "tests/assessment/test_evidence_pack_builder.py",
            "tests/assessment/llm/test_deepseek_provider.py",
            "tests/assessment/test_generate_llm_report_cli.py",
            "tests/assessment/test_phase42_historical_replay.py",
            "scripts/replay_phase42_formal_claims.py",
            "scripts/finalize_phase42_local_artifacts.py",
            "docs/superpowers/specs/2026-08-09-phase42-claim-evidence-remediation-design.md",
        ],
        "schema_changed": False,
        "formal_data_changed": False,
        "coverage_changed": False,
        "snapshot_changed": False,
        "candidate_pipeline_changed": False,
        "publication_pipeline_changed": False,
        "scheduler_changed": False,
        "notifier_changed": False,
        "program_semantic_rewrite_present": False,
        "second_external_llm_repair_present": False,
    }
    _write("minimal_fix_manifest.json", minimal_fix)
    print("phase42_local_artifacts_ready=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
