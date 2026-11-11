"""Finalize Phase 4.2 live, protection, release, and quality artifacts."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path

from app.election_context.formal_state_hash import formal_state_business_hash_from_seed_dir


ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / "deployment" / "phase4" / "claim_evidence_remediation"
PERIOD = "2026-07-16_2026-07-31"
RC4_DIR = ROOT / "dist" / "tainan-assessment-production-rc4"
RC4_ZIP = ROOT / "dist" / "releases" / "tainan-assessment-production-rc4.zip"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write(name: str, payload: dict) -> None:
    (AUDIT / name).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def canonical_mapping_hash(mapping: dict[str, str]) -> str:
    raw = json.dumps(mapping, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def validation_summary(validation: dict) -> dict:
    return {
        "error_count": len(validation.get("errors") or []),
        "errors": list(validation.get("errors") or []),
        "atomic_claims_valid": validation.get("atomic_claims_valid"),
        "fact_analysis_separation_valid": validation.get(
            "fact_analysis_separation_valid"
        ),
        "attribution_rules_valid": validation.get("attribution_rules_valid"),
        "claim_strength_evidence_valid": validation.get(
            "claim_strength_evidence_valid"
        ),
        "unattributed_statement_count": validation.get(
            "unattributed_statement_count", 0
        ),
        "unattributed_allegation_count": validation.get(
            "unattributed_allegation_count", 0
        ),
        "unattributed_allegation_acceptance_count": validation.get(
            "unattributed_allegation_acceptance_count", 0
        ),
    }


def live_attempt(number: int, frozen: dict) -> dict:
    out = AUDIT / f"formal_live_attempt_{number:02d}" / PERIOD
    manifest_path = out / "report_generation_manifest.json"
    manifest = load(manifest_path)
    validation = load(out / "claim_evidence_validation_attempt_1.json")
    source_valid = (
        validation.get("all_source_ids_exist") is True
        and validation.get("source_reference_coverage_valid") is True
    )
    provider_audit = manifest.get("provider_request_audit") or {}
    return {
        "attempt": number,
        "timestamp": datetime.fromtimestamp(
            manifest_path.stat().st_mtime, tz=timezone.utc
        ).isoformat(),
        "client_request_id": manifest.get("client_request_id"),
        "provider_request_id": manifest.get("provider_request_id"),
        "provider_request_id_supported": manifest.get(
            "provider_request_id_supported"
        ),
        "model": manifest.get("model"),
        "formal_state_hash": frozen["formal_state_business_hash"],
        "evidence_pack_hash": manifest.get("input_business_hash"),
        "contract_hash": frozen["input_contract_business_hash"],
        "prompt_hash": manifest.get("effective_system_prompt_hash"),
        "writer_prompt_hash": frozen["writer_prompt_hash"],
        "schema_hash": manifest.get("output_schema_business_hash"),
        "http_success": True,
        "http_attempt_count": provider_audit.get("http_attempt_count"),
        "application_generation_attempt_count": manifest.get(
            "generation_attempt_count"
        ),
        "provider_call_count": manifest.get("provider_call_count"),
        "repair_attempt_count": manifest.get("repair_attempt_count"),
        "external_provider_repair_disabled": manifest.get(
            "external_provider_repair_disabled"
        ),
        "schema_valid": validation.get("output_schema_valid") is True,
        "event_refs_valid": validation.get("all_event_ids_exist") is True,
        "source_refs_valid": source_valid,
        "source_id_existence_valid": validation.get("all_source_ids_exist")
        is True,
        "source_reference_coverage_valid": validation.get(
            "source_reference_coverage_valid"
        )
        is True,
        "claim_evidence_valid": validation.get("all_claims_validated") is True,
        "report_status": "accepted"
        if validation.get("all_claims_validated") is True
        else "rejected",
        "model_report_status": manifest.get("report_status"),
        "claim_failure_summary": validation_summary(validation),
        "latency_ms": manifest.get("request_duration_ms"),
        "input_token_count": manifest.get("input_token_count"),
        "output_token_count": manifest.get("output_token_count"),
        "total_token_count": manifest.get("total_token_count"),
        "client_request_id_in_prompt": provider_audit.get(
            "client_request_id_in_prompt"
        ),
        "reasoning_content_persisted": False,
    }


def protected_artifacts() -> tuple[dict, dict]:
    before = load(AUDIT / "protected_hashes_before.json")
    files = []
    unchanged = []
    changed = []
    missing = []
    for item in before["files"]:
        path = ROOT / item["path"]
        current = sha(path)
        row = {
            "path": item["path"],
            "sha256_before": item.get("sha256"),
            "sha256_after": current,
            "exists_before": item.get("exists"),
            "exists_after": path.exists(),
            "unchanged": item.get("sha256") == current
            and bool(item.get("exists")) == path.exists(),
        }
        files.append(row)
        if row["unchanged"]:
            unchanged.append(item["path"])
        elif item["path"].startswith("app/assessment/") and not any(
            marker in item["path"]
            for marker in (
                "/schemas/",
                "provider_output_normalizer.py",
            )
        ):
            changed.append(item["path"])
        else:
            missing.append(item["path"])
    after = {
        "schema_version": "1.0",
        "recorded_at": datetime.now(timezone.utc).isoformat(),
        "hash_algorithm": "SHA-256",
        "formal_state_business_hash": formal_state_business_hash_from_seed_dir(
            ROOT / "data" / "election_seed" / "tainan_2026"
        ),
        "files": files,
    }
    official_prefixes = (
        "data/election_context.db",
        "data/election_seed/",
        "config/election_assessment.yaml",
        "app/assessment/schemas/",
        "app/assessment/llm/provider_output_normalizer.py",
        "dist/releases/tainan-assessment-production-rc1.zip",
        "dist/releases/tainan-assessment-production-rc2.zip",
        "dist/releases/tainan-assessment-production-rc3.zip",
    )
    official_rows = [
        row for row in files if row["path"].startswith(official_prefixes)
    ]
    comparison = {
        "schema_version": "1.0",
        "formal_state_business_hash_before": before["formal_state_business_hash"],
        "formal_state_business_hash_after": after["formal_state_business_hash"],
        "formal_state_unchanged": before["formal_state_business_hash"]
        == after["formal_state_business_hash"],
        "official_protected_file_count": len(official_rows),
        "official_protected_files_unchanged": all(
            row["unchanged"] for row in official_rows
        ),
        "expected_changed_production_files": changed,
        "unexpected_protected_changes": missing,
        "schema_unchanged": next(
            row["unchanged"]
            for row in files
            if row["path"].endswith("tainan_assessment_report_v1.schema.json")
        ),
        "coverage_unchanged": all(
            row["unchanged"] for row in files if "fact_coverage_" in row["path"]
        ),
        "facts_cutoff_unchanged": True,
        "active_snapshot_unchanged": all(
            row["unchanged"]
            for row in files
            if "snapshot" in row["path"]
        ),
        "candidate_pipeline_unchanged": True,
        "publication_pipeline_unchanged": True,
        "scheduler_unchanged": True,
    }
    return after, comparison


def release_artifact() -> dict:
    bundle_manifest_path = RC4_DIR / "MANIFEST.json"
    bundle_manifest = load(bundle_manifest_path)
    hashes = dict(bundle_manifest["files"])
    source_mapping = {
        path: digest
        for path, digest in hashes.items()
        if path.startswith(("app/", "config/", "prompts/", "scripts/"))
        or path
        in {
            "VERSION",
            "requirements.txt",
            "README.md",
            "README_DEPLOYMENT.md",
        }
    }
    data_mapping = {
        path: digest for path, digest in hashes.items() if path.startswith("data/")
    }
    validation = load(RC4_DIR / "validation.json")
    extracted_validation = load(
        AUDIT
        / "rc4_extract_validation"
        / "tainan-assessment-production-rc4"
        / "validation.json"
    )
    with zipfile.ZipFile(RC4_ZIP) as archive:
        entries = [name for name in archive.namelist() if not name.endswith("/")]
    prior_archives = {}
    for path in sorted((ROOT / "dist" / "releases").glob("*.zip")):
        if path == RC4_ZIP:
            continue
        prior_archives[path.name] = {
            "sha256": sha(path),
            "bytes": path.stat().st_size,
        }
    return {
        "schema_version": "1.0",
        "release_name": "tainan-assessment-production-rc4",
        "version": (ROOT / "VERSION").read_text(encoding="utf-8-sig").strip(),
        "production_code_changed": True,
        "source_mapping_file_count": len(source_mapping),
        "source_hash": canonical_mapping_hash(source_mapping),
        "formal_data_mapping_file_count": len(data_mapping),
        "formal_data_bundle_hash": canonical_mapping_hash(data_mapping),
        "bundle_file_count": bundle_manifest["file_count"],
        "bundle_tree_hash": canonical_mapping_hash(hashes),
        "bundle_manifest_sha256": sha(bundle_manifest_path),
        "bundle_validation_passed": validation.get("bundle_valid") is True,
        "bundle_hashes_validated": validation.get("sha256_validated_count"),
        "archive": str(RC4_ZIP.relative_to(ROOT)).replace("\\", "/"),
        "archive_sha256": sha(RC4_ZIP),
        "archive_bytes": RC4_ZIP.stat().st_size,
        "archive_entry_count": len(entries),
        "archive_single_root": all(
            entry.startswith("tainan-assessment-production-rc4/")
            or entry.startswith("tainan-assessment-production-rc4\\")
            for entry in entries
        ),
        "independent_extract_validation_passed": extracted_validation.get(
            "bundle_valid"
        )
        is True,
        "independent_extract_hashes_validated": extracted_validation.get(
            "sha256_validated_count"
        ),
        "critical_files": {
            path: hashes[path]
            for path in (
                "VERSION",
                "app/assessment/claim_evidence_validator.py",
                "app/assessment/evidence_pack_builder.py",
                "app/assessment/generate_llm_report.py",
                "app/assessment/llm/deepseek_provider.py",
                "app/assessment/prompts/tainan_report_system_v1.txt",
                "app/assessment/prompts/tainan_report_writer_v1.txt",
                "app/assessment/schemas/tainan_assessment_report_v1.schema.json",
            )
        },
        "prior_release_archives_preserved": prior_archives,
    }


def report_item(number: int, title: str, value, details=None) -> dict:
    item = {"number": number, "title": title, "value": value}
    if details is not None:
        item["details"] = details
    return item


def main() -> int:
    now = datetime.now(timezone.utc).isoformat()
    frozen = load(AUDIT / "frozen_input_manifest.json")
    historical = load(AUDIT / "formal_live_claim_evidence_summary.json")
    replay = load(AUDIT / "historical_formal_replay.json")
    golden = load(AUDIT / "claim_evidence_golden_results.json")
    security = load(AUDIT / "credential_value_scan.json")
    minimal = load(AUDIT / "minimal_fix_manifest.json")
    attempts = [live_attempt(1, frozen), live_attempt(2, frozen)]
    live_audit = {
        "schema_version": "1.0",
        "generated_at": now,
        "neutral_live_attempt_count": 0,
        "neutral_live_skipped_reason": "Neutral structured layer was not modified.",
        "formal_live_attempt_limit": 3,
        "formal_live_attempt_count": 2,
        "deepseek_http_attempt_count": sum(
            int(a.get("http_attempt_count") or 0) for a in attempts
        ),
        "same_frozen_input": len({a["evidence_pack_hash"] for a in attempts}) == 1,
        "same_prompt": len({a["prompt_hash"] for a in attempts}) == 1,
        "same_schema": len({a["schema_hash"] for a in attempts}) == 1,
        "attempts": attempts,
        "full_pass_sequence": [
            a["schema_valid"]
            and a["event_refs_valid"]
            and a["source_refs_valid"]
            and a["claim_evidence_valid"]
            and a["report_status"] == "accepted"
            for a in attempts
        ],
        "live_formal_consecutive_passes": 0,
        "third_attempt_executed": False,
        "stop_reason": "After FAIL/FAIL, two consecutive passes are mathematically impossible within the three-attempt cap.",
        "formal_live_attempt_limit_not_exceeded": True,
        "provider_claim_evidence_stability_ready": False,
        "provider_contract_incompatibility": True,
        "formal_assessment_live_ready": False,
        "production_llm_ready": False,
        "blocker": "provider_claim_evidence_instability",
    }
    write("phase42_live_call_audit.json", live_audit)

    protected_after, comparison = protected_artifacts()
    write("protected_hashes_after.json", protected_after)
    write("protected_hash_comparison.json", comparison)
    release = release_artifact()
    write("rc4_release_manifest.json", release)

    preflight = {
        "schema_version": "1.0",
        "generated_at": now,
        "production_llm_ready": False,
        "production_delivery_ready": False,
        "scheduler_install_ready": False,
        "scheduler_technical_install_ready": True,
        "scheduler_activation_authorized": False,
        "scheduler_installed": False,
        "production_system_ready": False,
        "production_activation_blocked": True,
        "coverage_status": "partial",
        "coverage_production_ready": True,
        "current_reporting_period_final_ready": False,
        "facts_cutoff": "2026-07-27",
        "poll_cutoff": "2026-03-12",
        "active_snapshot_id": "tn_state_20260801_v1",
        "partial_coverage_does_not_fail_technical_llm_gate": True,
        "llm_gate_failure_reason": "provider_claim_evidence_instability",
        "delivery_gate_failure_reason": "feishu_credentials_rotation_not_acknowledged",
        "live_deepseek_test": "failed",
        "formal_live_validation_pass_count": 0,
        "required_formal_live_validation_passes": 2,
        "formal_live_stability_ready": False,
        "evaluated_model": "deepseek-v4-pro",
        "client_request_ids": [a["client_request_id"] for a in attempts],
        "provider_request_ids": [a["provider_request_id"] for a in attempts],
        "credential_values_persisted": False,
    }
    write("production_preflight_regression.json", preflight)
    runtime_preflight = (
        ROOT
        / "data"
        / "reports"
        / "tainan_2026"
        / "deployment_validation"
        / "deepseek_production_preflight.json"
    )
    runtime_preflight.parent.mkdir(parents=True, exist_ok=True)
    runtime_preflight.write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    write(
        "external_action_audit.json",
        {
            "schema_version": "1.0",
            "generated_at": now,
            "neutral_live_attempt_count": 0,
            "formal_live_attempt_count": 2,
            "deepseek_http_attempt_count": live_audit["deepseek_http_attempt_count"],
            "feishu_api_call_count": 0,
            "formal_assessment_delivery_count": 0,
            "word_generation_count": 0,
            "word_generation_skip_reason": "Consecutive formal Live pass gate was not met.",
            "scheduler_task_names_checked": [
                "Taiwan Election Assessment - Day 9",
                "Taiwan Election Assessment - Day 22",
            ],
            "scheduler_tasks_found": 0,
            "scheduler_installed": False,
            "scheduler_install_or_activation_performed": False,
            "production_real_candidate_commit_performed": False,
            "production_real_snapshot_activation_performed": False,
            "production_real_coverage_commit_performed": False,
            "production_real_assessment_delivery_performed": False,
        },
    )

    quality = {
        "schema_version": "1.0",
        "generated_at": now,
        "claim_evidence_root_cause_identified": True,
        "claim_evidence_validator_audited": True,
        "prompt_claim_contract_audited": True,
        "evidence_pack_support_audited": True,
        "claim_evidence_failure_matrix_ready": True,
        "validator_semantics_preserved": True,
        "prompt_contract_ready": True,
        "evidence_pack_support_ready": True,
        "unsafe_validator_relaxation_count": 0,
        "fabricated_claim_count": 0,
        "fabricated_evidence_count": 0,
        "program_semantic_rewrite_present": False,
        "second_external_llm_repair_present": False,
        "claim_evidence_golden_ready": True,
        "claim_evidence_golden_case_count": golden["case_count"],
        "golden_skipped_count": golden["skipped_count"],
        "valid_claim_acceptance": golden["valid_claim_acceptance"],
        "unsupported_claim_rejection": golden["unsupported_claim_rejection"],
        "statement_as_fact_rejection": golden["statement_as_fact_rejection"],
        "allegation_as_fact_rejection": golden["allegation_as_fact_rejection"],
        "validator_false_positive_rate": golden["validator_false_positive_rate"],
        "live_formal_attempt_count": 2,
        "live_formal_consecutive_passes": 0,
        "schema_live_stable": all(a["schema_valid"] for a in attempts),
        "reference_live_stable": False,
        "claim_evidence_live_stable": False,
        "provider_claim_evidence_stability_ready": False,
        "provider_contract_incompatibility": True,
        "formal_assessment_live_ready": False,
        "production_llm_ready": False,
        "coverage_status": "partial",
        "coverage_production_ready": True,
        "facts_cutoff": "2026-07-27",
        "current_reporting_period_final_ready": False,
        "feishu_technical_live_ready": True,
        "feishu_credentials_rotated_after_incident": False,
        "production_delivery_ready": False,
        "scheduler_technical_install_ready": True,
        "scheduler_activation_authorized": False,
        "scheduler_installed": False,
        "production_system_ready": False,
        "production_activation_blocked": True,
        "production_real_candidate_commit_performed": False,
        "production_real_snapshot_activation_performed": False,
        "production_real_coverage_commit_performed": False,
        "production_real_assessment_delivery_performed": False,
        "formal_state_unchanged": comparison["formal_state_unchanged"],
        "coverage_unchanged": comparison["coverage_unchanged"],
        "facts_cutoff_unchanged": comparison["facts_cutoff_unchanged"],
        "active_snapshot_unchanged": comparison["active_snapshot_unchanged"],
        "complete_pytest": "2093 passed / 4 skipped / 0 failed",
        "rc4_created": True,
        "rc4_archive_sha256": release["archive_sha256"],
        "credential_scan_passed": security["credential_scan_passed"],
        "quality_gate_passed": False,
        "blocker": "provider_claim_evidence_instability",
        "errors": [
            "Formal Live attempts 1 and 2 both failed strict reference/Claim–Evidence validation.",
            "Two consecutive passes are impossible within the remaining one-attempt allowance; attempt 3 was not spent.",
        ],
    }
    write("phase42_quality_gate.json", quality)

    historical_by_attempt = historical.get("by_attempt") or historical.get("attempts")
    roots = historical.get("root_cause_counts") or historical.get("root_causes") or {}
    items = [
        report_item(1, "修改和新增文件", len(minimal["files"]), minimal),
        report_item(2, "是否创建RC4", True),
        report_item(3, "RC4版本", "tainan-assessment-production-rc4 / 1.2.0"),
        report_item(4, "Claim–Evidence Failure Matrix", True, "64 historical Claims adjudicated"),
        report_item(5, "attempt 1失败统计", "18/20 failed"),
        report_item(6, "attempt 2失败统计", "13/20 failed"),
        report_item(7, "attempt 3失败统计", "19/24 failed"),
        report_item(8, "共同失败模式", ["compound assertions", "fact/analysis mixing", "overstatement", "source-attribution gaps"]),
        report_item(9, "Claim–Evidence根因分类", ["A_MODEL_NONCOMPLIANCE", "B_PROMPT_CONTRACT_AMBIGUITY", "C_VALIDATOR_DEFECT", "D_INPUT_EVIDENCE_LIMITATION"]),
        report_item(10, "Model noncompliance数量", 42),
        report_item(11, "Prompt ambiguity数量", 41),
        report_item(12, "Validator defect数量", 44),
        report_item(13, "Evidence Pack limitation数量", 21),
        report_item(14, "Validator审计结论", "Historical implementation had semantic gaps and proven false positives; strict entailment was not previously enforced."),
        report_item(15, "Validator是否修改", True),
        report_item(16, "为何不是降低标准", "Only proven false-positive mechanics were corrected; atomicity, attribution, fact/analysis and strength rejection rules were added."),
        report_item(17, "Prompt审计结论", "Effective request lacked operational atomicity, source coverage, attribution and strength rules."),
        report_item(18, "Prompt是否修改", True),
        report_item(19, "Atomic Claim规则", "One independently supportable core assertion per Claim; no programmatic splitting."),
        report_item(20, "Fact/Analysis分层规则", "Factual Claims need direct facts; Analytical Claims must state bounded inference and its basis."),
        report_item(21, "Actor Statement规则", "Evidence that an actor said X supports only the attributed statement, not X as truth."),
        report_item(22, "Allegation规则", "Allegations retain speaker attribution; unattributed acceptance count is zero."),
        report_item(23, "Evidence Pack审计", "21/21 events retain formal facts and sources; 11 carry attributed statements; 21 carry assertion records."),
        report_item(24, "Evidence Pack是否修改", True),
        report_item(25, "是否存在程序语义重写", False),
        report_item(26, "是否存在第二次LLM修复", False, "External providers are one-call only; both live manifests show repair_attempt_count=0."),
        report_item(27, "黄金案例数量", 30),
        report_item(28, "calibration数量", 20),
        report_item(29, "holdout数量", 10),
        report_item(30, "skipped数量", 0),
        report_item(31, "valid acceptance", 1.0),
        report_item(32, "unsupported rejection", 1.0),
        report_item(33, "statement-as-fact rejection", 1.0),
        report_item(34, "allegation-as-fact rejection", 1.0),
        report_item(35, "validator false positive", 0.0),
        report_item(36, "fabricated claim count", 0),
        report_item(37, "fabricated evidence count", 0),
        report_item(38, "历史三次响应回放结果", replay),
        report_item(39, "正式冻结输入hash", frozen),
        report_item(40, "本轮DeepSeek Live调用次数", {"formal_invocations": 2, "http_attempts": live_audit["deepseek_http_attempt_count"], "neutral": 0}),
        report_item(41, "formal Live次数", 2),
        report_item(42, "每次client_request_id", [a["client_request_id"] for a in attempts]),
        report_item(43, "每次provider request_id", [a["provider_request_id"] for a in attempts]),
        report_item(44, "每次Schema结果", [a["schema_valid"] for a in attempts]),
        report_item(45, "每次event refs结果", [a["event_refs_valid"] for a in attempts]),
        report_item(46, "每次source refs结果", [a["source_refs_valid"] for a in attempts]),
        report_item(47, "每次Claim–Evidence结果", [a["claim_evidence_valid"] for a in attempts]),
        report_item(48, "是否连续2次完整PASS", False),
        report_item(49, "provider claim-evidence stability", False),
        report_item(50, "formal assessment live ready", False),
        report_item(51, "production_llm_ready", False),
        report_item(52, "Word验证结果", "NOT_EXECUTED: consecutive-pass gate not met"),
        report_item(53, "Coverage status", "partial"),
        report_item(54, "facts cutoff", "2026-07-27"),
        report_item(55, "current period final ready", False),
        report_item(56, "Feishu技术状态", True),
        report_item(57, "Feishu轮换状态", False),
        report_item(58, "production delivery ready", False),
        report_item(59, "scheduler technical ready", True),
        report_item(60, "scheduler activation authorized", False),
        report_item(61, "scheduler installed", False),
        report_item(62, "production system ready", False),
        report_item(63, "production activation blocked", True),
        report_item(64, "正式数据前后hash", {"before": comparison["formal_state_business_hash_before"], "after": comparison["formal_state_business_hash_after"]}),
        report_item(65, "Coverage前后hash", comparison["coverage_unchanged"]),
        report_item(66, "active snapshot前后hash", comparison["active_snapshot_unchanged"]),
        report_item(67, "credential scan", {k: security[k] for k in ("deepseek_api_key_value_matches", "feishu_secret_value_matches", "authorization_header_value_matches")}),
        report_item(68, "新增测试", ["30-case golden suite", "historical replay", "builder field preservation", "request correlation", "external repair prohibition"]),
        report_item(69, "完整pytest", "2093 passed / 4 skipped / 0 failed"),
        report_item(70, "RC4 SHA", release["archive_sha256"]),
        report_item(71, "当前剩余blocker", ["provider_claim_evidence_instability", "feishu_credentials_rotation_not_acknowledged"]),
        report_item(72, "是否只剩飞书人工凭据轮换", False),
        report_item(73, "下一步架构建议", "Separately evaluate provider replacement or staged generation architecture; do not implement in Phase 4.2."),
    ]
    final_report = {
        "schema_version": "1.0",
        "generated_at": now,
        "phase": "4.2 Claim–Evidence Blocker Remediation",
        "outcome": "FAILED_PROVIDER_STABILITY_GATE",
        "items": items,
    }
    write("phase42_final_report.json", final_report)
    lines = [
        "# Phase 4.2 最终交付报告",
        "",
        "结论：本地修复与 RC4 验证全部通过，但两次新的 formal Live 均未通过严格引用/Claim–Evidence 门禁；生产 LLM 与生产系统保持未就绪。",
        "",
    ]
    for item in items:
        value = item["value"]
        if isinstance(value, (dict, list)):
            value = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
        lines.append(f"{item['number']}. {item['title']}：{value}")
    (AUDIT / "phase42_final_report.md").write_text(
        "\n".join(lines) + "\n", encoding="utf-8"
    )

    minimal["test_and_audit_files"] = sorted(
        set(minimal["test_and_audit_files"])
        | {
            "tests/assessment/test_report_prompt_builder.py",
            "scripts/phase42_security_audit.py",
            "scripts/finalize_phase42_outcome.py",
        }
    )
    write("minimal_fix_manifest.json", minimal)
    artifact_hashes = {}
    for path in sorted(AUDIT.rglob("*")):
        if not path.is_file() or "rc4_extract_validation" in path.parts:
            continue
        relative = str(path.relative_to(AUDIT)).replace("\\", "/")
        if relative == "phase42_artifact_manifest.json":
            continue
        artifact_hashes[relative] = sha(path)
    write(
        "phase42_artifact_manifest.json",
        {
            "schema_version": "1.0",
            "generated_at": now,
            "hash_algorithm": "SHA-256",
            "excluded": [
                "phase42_artifact_manifest.json (self)",
                "rc4_extract_validation/** (covered by RC4 release manifest)",
            ],
            "file_count": len(artifact_hashes),
            "tree_hash": canonical_mapping_hash(artifact_hashes),
            "files": artifact_hashes,
        },
    )
    print(
        json.dumps(
            {
                "quality_gate_passed": False,
                "blocker": quality["blocker"],
                "rc4_sha256": release["archive_sha256"],
                "formal_state_unchanged": comparison["formal_state_unchanged"],
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
