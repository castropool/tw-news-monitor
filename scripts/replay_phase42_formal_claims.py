"""Replay frozen Phase 4.1 formal outputs through current local validators."""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.assessment.claim_evidence_validator import (
    build_evidence_context,
    validate_structured_report,
)
from app.assessment.evidence_pack_builder import load_yaml


def _load(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def replay(root: Path) -> dict[str, Any]:
    remediation = root / "deployment/phase4/claim_evidence_remediation"
    config = load_yaml(root / "config/election_assessment.yaml")
    attempts: list[dict[str, Any]] = []

    for attempt in range(1, 4):
        attempt_dir = remediation / f"formal_attempt_{attempt:02d}"
        report_path = attempt_dir / "normalized_response.json"
        before_hash = _sha256(report_path)
        report = _load(report_path)
        contract = _load(attempt_dir / "frozen_request_payload.json")
        legacy = _load(attempt_dir / "claim_evidence_validation.json")
        context = build_evidence_context(
            contract, evidence_pack=None, config=config
        )
        validation = validate_structured_report(
            report,
            context,
            expected_mode=report.get("generation_mode") or "draft_with_data_gap",
        )
        after_hash = _sha256(report_path)
        event_refs_valid = bool(
            validation.get("all_event_ids_exist")
            and validation.get("all_poll_ids_exist")
        )
        source_refs_valid = bool(
            validation.get("all_source_ids_exist")
            and validation.get("source_reference_coverage_valid")
        )
        attempts.append(
            {
                "attempt": attempt,
                "schema_valid": bool(validation.get("output_schema_valid")),
                "event_references_valid": event_refs_valid,
                "source_references_valid": source_refs_valid,
                "claim_evidence_valid": bool(
                    validation.get("all_claims_validated")
                ),
                "report_status": "accepted"
                if validation.get("all_claims_validated")
                else "rejected",
                "legacy_claim_evidence_valid": bool(
                    legacy.get("all_claims_validated")
                ),
                "legacy_error_count": len(legacy.get("errors") or []),
                "replay_error_count": len(validation.get("errors") or []),
                "replay_errors": validation.get("errors") or [],
                "new_rule_results": {
                    key: validation.get(key)
                    for key in (
                        "source_reference_coverage_valid",
                        "atomic_claims_valid",
                        "fact_analysis_separation_valid",
                        "attribution_rules_valid",
                        "claim_strength_evidence_valid",
                    )
                },
                "historical_response_sha256_before": before_hash,
                "historical_response_sha256_after": after_hash,
                "historical_response_unchanged": before_hash == after_hash,
                "model_recall_performed": False,
            }
        )

    return {
        "schema_version": "1.0",
        "generated_at": datetime.now().astimezone().isoformat(),
        "historical_attempt_count": 3,
        "historical_outputs_modified": False,
        "model_recall_performed": False,
        "attempts": attempts,
        "all_historical_responses_unchanged": all(
            item["historical_response_unchanged"] for item in attempts
        ),
        "interpretation": (
            "Prompt changes cannot alter historical model output. Validator replay "
            "separately identifies old false positives and newly enforced safety rules."
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output or (
        root
        / "deployment/phase4/claim_evidence_remediation/historical_formal_replay.json"
    )
    result = replay(root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
