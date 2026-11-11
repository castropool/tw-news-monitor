"""Validators for candidate records, the pipeline run and input protection."""

from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any


FORBIDDEN_INFERENCE_PHRASES = [
    "占優勢",
    "勝算提高",
    "選情升溫",
    "整合已經完成",
    "藍白合作已經成形",
    "綠營結構優勢",
    "勝選機率",
    "支持率預測",
    "未來走勢",
    "將勝選",
    "看好",
]


def validate_candidate(
    candidate: dict[str, Any],
    articles: list[dict[str, Any]],
    assertions: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    suggestions: list[dict[str, Any]],
    formal_event_ids: set[str],
    config,
    validator_version: str = "0.1.0",
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    allowed_statuses = set(config.get("validation.allowed_review_statuses", []))
    forbidden_statuses = set(config.get("validation.forbidden_review_statuses", []))

    cid = candidate.get("candidate_id", "")
    if not re.fullmatch(r"cand_[a-z0-9]+_[0-9a-f]{6,16}", cid):
        errors.append("candidate_id_invalid")
    if candidate.get("review_status") not in allowed_statuses:
        errors.append("review_status_invalid")
    if candidate.get("review_status") in forbidden_statuses:
        errors.append("review_status_forbidden")
    if not candidate.get("status_reason_codes_json") or json.loads(candidate["status_reason_codes_json"]) == []:
        errors.append("status_reasons_present")
    if not candidate.get("anchor_article_id"):
        errors.append("anchor_article_exists")
    if candidate.get("article_count", 0) <= 0:
        errors.append("all_articles_exist")
    if not articles:
        errors.append("all_articles_exist")
    if candidate.get("article_count", 0) != len(articles):
        errors.append("all_articles_exist")
    if len({a.get("news_article_id") for a in articles}) != len(articles):
        errors.append("no_duplicate_article_links")
    if candidate.get("canonical_event_date") and candidate.get("event_date_basis", "") == "unknown":
        errors.append("event_date_semantics_valid")
    if candidate.get("event_date_basis") == "inferred_from_publication" and candidate.get("event_date_confidence") == "high":
        errors.append("event_date_semantics_valid")
    if not sources and candidate.get("review_status") != "auto_reject":
        errors.append("source_links_valid")
    if not assertions:
        errors.append("assertion_evidence_valid")
    for a in assertions:
        if a.get("assertion_kind") not in {
            "observed_fact", "actor_statement", "allegation", "media_interpretation",
            "planned_action", "uncertain_report", "unknown",
        }:
            errors.append("assertion_kind_valid")
        if not a.get("evidence_article_id"):
            errors.append("assertion_evidence_valid")
    profile_kinds = {a["assertion_kind"] for a in assertions}
    if "observed_fact" not in profile_kinds and any(
        k in profile_kinds for k in ("allegation", "uncertain_report", "media_interpretation")
    ):
        warnings.append("observed_fact_not_empty")
    for a in assertions:
        if a.get("assertion_kind") == "actor_statement" and not a.get("speaker"):
            errors.append("statement_speaker_present")
    for s in suggestions:
        if s.get("formal_event_id") not in formal_event_ids:
            errors.append("formal_event_ids_exist")
        if s.get("suggested_action") not in {
            "likely_duplicate", "possible_subevent", "possible_related_event",
            "no_material_match", "manual_review",
        }:
            errors.append("duplicate_suggestions_valid")
    for key in ("relevance_score", "completeness_score", "cluster_confidence", "formal_duplicate_score"):
        v = float(candidate.get(key, 0) or 0)
        if v < 0 or v > 1:
            errors.append("scores_in_range")
    text = f"{candidate.get('candidate_title', '')} {candidate.get('candidate_summary', '')}"
    for phrase in FORBIDDEN_INFERENCE_PHRASES:
        if phrase in text:
            errors.append("no_political_inference")

    return {
        "candidate_id": cid,
        "validation_ready": 0 if errors else 1,
        "errors_json": json.dumps(errors, ensure_ascii=False),
        "warnings_json": json.dumps(warnings, ensure_ascii=False),
        "checked_at": datetime.now().isoformat(),
        "validator_version": validator_version,
    }


def scan_package_for_forbidden_writes(package_dir: Path, forbidden_methods: list[str]) -> list[str]:
    hits: list[str] = []
    for path in package_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for method in forbidden_methods:
            if re.search(rf"\b{re.escape(method)}\s*\(", text):
                hits.append(f"{path.name}:{method}")
    return hits


def build_global_validation(
    candidate_count: int,
    valid_count: int,
    status_counts: dict[str, int],
    input_hashes_before: dict[str, str],
    input_hashes_after: dict[str, str],
    formal_write_call_count: int,
    package_dir: Path,
    config,
) -> dict[str, Any]:
    forbidden = list(config.get("guardrails.forbidden_formal_write_methods", []))
    forbidden_imports = scan_package_for_forbidden_writes(package_dir, forbidden)
    errors: list[str] = []
    warnings: list[str] = []
    if formal_write_call_count != 0:
        errors.append("formal_write_method_called")
    if forbidden_imports:
        errors.append("forbidden_formal_write_imports")
    unchanged = True
    for key in ("news_db_unchanged", "article_matches_unchanged", "formal_data_unchanged", "frozen_release_unchanged"):
        before = input_hashes_before.get(key)
        after = input_hashes_after.get(key)
        if before and after and before != after:
            errors.append(f"{key}=false")
            unchanged = False
    if not unchanged:
        errors.append("input_changed")
    return {
        "candidate_pipeline_ready": not errors,
        "errors": errors,
        "warnings": warnings,
        "candidate_count": candidate_count,
        "valid_candidate_count": valid_count,
        "review_required_count": status_counts.get("review_required", 0),
        "hold_count": status_counts.get("hold", 0),
        "duplicate_candidate_count": status_counts.get("duplicate_candidate", 0),
        "auto_reject_count": status_counts.get("auto_reject", 0),
        "formal_data_unchanged": input_hashes_before.get("formal_data_unchanged") == input_hashes_after.get("formal_data_unchanged") if "formal_data_unchanged" in input_hashes_before else True,
        "news_db_unchanged": input_hashes_before.get("news_db_unchanged") == input_hashes_after.get("news_db_unchanged") if "news_db_unchanged" in input_hashes_before else True,
        "formal_database_open_mode": "read_only",
        "formal_write_method_call_count": formal_write_call_count,
    }
