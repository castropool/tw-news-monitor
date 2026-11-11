"""Unified publication validator."""

from __future__ import annotations

import json
from typing import Any

from .formal_duplicate_checker import check_candidate_duplicates, load_formal_events
from .article_normalizer import normalize_domain
from .publication_preview import formal_seed_business_hash, read_seed_events, read_seed_sources
from .review_workflow import is_review_stale


UNSAFE_PHRASES = ["勝算", "優勢", "升溫", "已經完成", "成形", "勝選機率", "支持率預測", "未來走勢"]
PLANNED_MARKERS = ["擬", "將於", "預計", "計劃", "計畫", "月底", "號召"]
UNCERTAIN_MARKERS = ["傳", "據悉", "可能", "疑似", "消息人士"]
MEDIA_MARKERS = ["觀點", "分析", "研判", "初探", "評論"]
ALLEGATION_MARKERS = ["指控", "質疑", "批評", "爆料", "抨擊", "砲轟"]


def validate_batch(
    repo,
    config,
    election_id: str,
    batch_id: str,
    preview: dict[str, Any],
) -> dict[str, Any]:
    errors: list[str] = []
    warnings: list[str] = []
    unsafe_count = 0
    unattributed_allegation_count = 0
    uncertain_promoted = 0
    media_promoted = 0
    planned_promoted = 0
    batch = repo.get_publication_batch(batch_id)
    if not batch:
        errors.append("batch_not_found")
    decision_ids = preview.get("review_decision_ids", [])
    decisions = [repo.get_review_decision(rid) for rid in decision_ids]
    for d in decisions:
        if d is None:
            errors.append("review_decision_exists")
            continue
        if not d.get("reviewer") or d["reviewer"].strip().lower() == "system":
            errors.append("reviewer_present")
        if is_review_stale(repo, d):
            errors.append(f"review_decision_stale:{d['review_decision_id']}")
        latest = repo.get_latest_review_decision(d["candidate_id"])
        if not latest or latest["review_decision_id"] != d["review_decision_id"]:
            errors.append("review_decision_latest")
        candidate = repo.get_candidate(d["candidate_id"])
        if candidate and candidate.get("review_status") not in ("review_approved", "publication_prepared"):
            errors.append("candidate_not_approved")
        if not candidate or (repo.get_validation(d["candidate_id"]) or {}).get("validation_ready") != 1:
            errors.append("candidate_validation_ready")
    if preview.get("errors"):
        errors.extend(preview["errors"])

    if preview.get("new_events"):
        seed_events = read_seed_events(config)
        seed_sources = read_seed_sources(config)
        formal_events = load_formal_events(config.path("formal_db"), election_id, config)
        for evt in preview["new_events"]:
            if not evt.get("occurred_at"):
                errors.append(f"event_date_valid:{evt['event_id']}")
            if not evt.get("event_type") or evt["event_type"] == "unknown":
                errors.append(f"event_type_valid:{evt['event_id']}")
            if not evt.get("title"):
                errors.append(f"event_payload_complete:{evt['event_id']}")
            # duplicate recheck
            suggestions = check_candidate_duplicates(
                {
                    "candidate_id": evt["event_id"],
                    "primary_actor": evt["actors"][0] if evt.get("actors") else "",
                    "secondary_actors_json": json.dumps(evt.get("actors", [])[1:], ensure_ascii=False),
                    "themes_json": json.dumps(evt.get("issues", []), ensure_ascii=False),
                    "keywords_json": json.dumps(evt.get("actors", []) + evt.get("issues", []), ensure_ascii=False),
                    "canonical_event_date": evt.get("occurred_at", ""),
                    "candidate_event_type": evt.get("event_type", ""),
                    "candidate_title": evt.get("title", ""),
                    "candidate_summary": evt.get("fact_summary", ""),
                },
                formal_events,
                config,
                batch_id,
                {normalize_domain(s.get("url", "")) for s in evt.get("sources", []) if s.get("url")},
            )
            if suggestions and suggestions[0]["similarity_score"] >= float(
                config.get("duplicate_detection.likely_duplicate_threshold", 0.9)
            ):
                errors.append(f"duplicate_new_event:{evt['event_id']}")
            # safety
            analysis = evt.get("analysis_json", {})
            for kind, flag in (
                ("allegations", "unattributed_allegation_count"),
                ("attributed_statements", None),
            ):
                pass
            if len(analysis.get("allegations", [])) > 0:
                errors.append("unattributed_allegation_count")
                unattributed_allegation_count += len(analysis.get("allegations", []))
            for fact in analysis.get("observed_facts", []):
                if any(p in fact for p in UNSAFE_PHRASES):
                    unsafe_count += 1
                    errors.append("unsafe_fact_promotion_count")
                if any(p in fact for p in UNCERTAIN_MARKERS):
                    uncertain_promoted += 1
                    errors.append("uncertain_report_promoted_to_fact_count")
                if any(p in fact for p in MEDIA_MARKERS):
                    media_promoted += 1
                    errors.append("media_interpretation_promoted_to_fact_count")
                if any(p in fact for p in PLANNED_MARKERS):
                    planned_promoted += 1
                    errors.append("planned_action_promoted_to_completed_fact_count")
                if any(p in fact for p in ALLEGATION_MARKERS):
                    unattributed_allegation_count += 1
                    errors.append("unattributed_allegation_count")

    event_ids = [e["event_id"] for e in preview.get("new_events", [])]
    if len(set(event_ids)) != len(event_ids):
        errors.append("event_id_unique")
    source_ids = [s["source_id"] for s in preview.get("new_sources", [])]
    if len(set(source_ids)) != len(source_ids):
        errors.append("source_id_unique")
    if preview.get("formal_data_hash_before") != formal_seed_business_hash(config):
        errors.append("formal_data_hash_matches_expected")
    if batch and not batch.get("staging_ready"):
        warnings.append("staging_not_ready")

    return {
        "publication_ready": not errors,
        "errors": errors,
        "warnings": warnings,
        "candidate_count": len(decisions),
        "new_event_count": len(preview.get("new_events", [])),
        "attached_event_count": sum(
            1 for i in preview.get("items", []) if i["operation_type"] == "attach_source"
        ),
        "new_source_count": len(preview.get("new_sources", [])),
        "unsafe_fact_promotion_count": unsafe_count,
        "unattributed_allegation_count": unattributed_allegation_count,
        "uncertain_report_promoted_to_fact_count": uncertain_promoted,
        "media_interpretation_promoted_to_fact_count": media_promoted,
        "planned_action_promoted_to_completed_fact_count": planned_promoted,
        "formal_data_hash_matches_expected": errors.count("formal_data_hash_matches_expected") == 0,
    }
