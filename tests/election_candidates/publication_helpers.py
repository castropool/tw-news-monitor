"""Helpers for isolated Phase 2 publication tests."""

from __future__ import annotations

import json
import shutil
import sqlite3
import hashlib
from pathlib import Path
from typing import Any

from app.election_candidates.config import load_config
from app.election_candidates.candidate_repository import CandidateRepository
from app.election_candidates.review_workflow import export_review_template, save_review_decision


FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "election_publication"


def make_publication_config(tmp_path: Path):
    seed_dst = tmp_path / "seed"
    if seed_dst.exists():
        shutil.rmtree(seed_dst)
    shutil.copytree(FIXTURE / "seed", seed_dst)
    _write_seed_manifest(seed_dst)
    formal_db = tmp_path / "election_context.db"
    from app.election_context.bootstrap import run_bootstrap

    run_bootstrap(str(seed_dst), str(formal_db), reset=True)
    (tmp_path / "news.db").write_bytes(b"")
    (tmp_path / "election_watch.db").write_bytes(b"")
    (tmp_path / "release.zip").write_bytes(b"fixture")
    config = load_config("config/election_candidate_pipeline.yaml")
    config.raw["paths"].update(
        {
            "news_db": str(tmp_path / "news.db"),
            "match_db": str(tmp_path / "election_watch.db"),
            "formal_db": str(formal_db),
            "candidate_db": str(tmp_path / "candidate.db"),
            "output_root": str(tmp_path / "out"),
            "events_seed": str(seed_dst / "events.jsonl"),
            "sources_seed": str(seed_dst / "sources.jsonl"),
            "initial_snapshot": str(seed_dst / "initial_snapshot.json"),
            "snapshot_history": str(seed_dst / "snapshot_history.jsonl"),
            "coverage_root": str(seed_dst),
            "poll_seeds": [str(seed_dst / "polls.jsonl"), str(seed_dst / "poll_source_links.jsonl")],
            "post_publication_root": str(tmp_path / "post_publication"),
            "backup_root": str(tmp_path / "backups"),
            "lock_root": str(tmp_path / "locks"),
            "frozen_release_zip": str(tmp_path / "release.zip"),
        }
    )
    config.raw["test_mode"] = True
    return config


def _write_seed_manifest(seed_dir: Path):
    from app.election_context.authority_map import AUTHORITY_MAP

    entity_files = {
        "election": "election.json", "actors": "actors.yaml", "sources": "sources.jsonl",
        "events": "events.jsonl", "event_sources": "events.jsonl", "polls": "polls.jsonl",
        "poll_questions": "poll_questions.jsonl", "poll_results": "poll_results.jsonl",
        "poll_sources": "poll_sources.jsonl", "poll_source_links": "poll_source_links.jsonl",
        "snapshots": "initial_snapshot.json", "snapshot_history": "snapshot_history.jsonl",
    }
    manifest = {
        "election_id": "tainan_mayoral_2026",
        "seed_manifest_version": "1.0",
        "generated_at": "2026-08-07T00:00:00",
        "entities": {
            name: {
                "path": path,
                "schema_version": "1.0",
                "record_count": (
                    sum(1 for _ in (seed_dir / path).read_text(encoding="utf-8").splitlines() if _.strip())
                    if path.endswith(".jsonl") else 1
                ),
                "sha256": hashlib.sha256((seed_dir / path).read_bytes()).hexdigest(),
                "business_hash": hashlib.sha256((seed_dir / path).read_bytes()).hexdigest(),
                "authority": (
                    AUTHORITY_MAP.get(name) or AUTHORITY_MAP.get(name + "s", {})
                ).get("authority", "unknown"),
            }
            for name, path in entity_files.items()
        },
        "schema_versions": {"seed_manifest": "1.0"},
        "business_hashes": {},
    }
    (seed_dir / "seed_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def open_candidate_repo(config) -> CandidateRepository:
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    repo.create_tables()
    return repo


def seed_candidate(repo, cid="cand_tnn_abc123", status="review_required", **over):
    candidate = {
        "candidate_id": cid,
        "election_id": "TW-2026-TNN-MAYOR",
        "anchor_article_id": "1",
        "cluster_fingerprint": cid,
        "canonical_event_date": "2026-08-10T10:00:00",
        "event_date_precision": "day",
        "event_date_basis": "explicit_in_title",
        "event_date_confidence": "high",
        "candidate_event_type": "campaign_event",
        "candidate_title": "謝龍介舉辦台南市長選舉造勢晚會",
        "candidate_summary": "據1篇報導",
        "primary_actor": "謝龍介",
        "secondary_actors_json": "[]",
        "locations_json": "[]",
        "themes_json": '["造勢"]',
        "keywords_json": '["謝龍介","造勢"]',
        "assertion_profile_json": '{"has_observed_fact": true}',
        "article_count": 1,
        "source_count": 1,
        "relevance_score": 0.8,
        "completeness_score": 0.9,
        "cluster_confidence": 0.9,
        "date_confidence": 1.0,
        "source_confidence": 0.8,
        "assertion_risk_score": 0.1,
        "formal_duplicate_score": 0.1,
        "formal_duplicate_status": "no_match",
        "risk_level": "medium",
        "review_status": status,
        "status_reason_codes_json": '["eligible"]',
        "first_seen_at": "2026-08-01T00:00:00",
        "last_updated_at": "2026-08-01T00:00:00",
        "created_run_id": "run_1",
        "updated_run_id": "run_1",
        "relevance_label": "direct_event",
        "date_flagged_inferred": 0,
        "candidate_schema_version": "1.1",
        "region_match": True,
        "has_candidate_actor": True,
    }
    candidate.update(over)
    repo.upsert_candidate(candidate)
    repo.attach_article(
        {
            "candidate_id": cid,
            "news_article_id": "1",
            "relationship_type": "same_event",
            "is_anchor": 1,
            "article_title": candidate["candidate_title"],
            "article_url": "https://www.cna.com.tw/news/aipl/1.aspx",
            "source_name": "中央社",
            "published_at": candidate["canonical_event_date"],
            "event_date_candidate": candidate["canonical_event_date"],
            "event_date_basis": "explicit_in_title",
            "match_score": 1.0,
            "attached_run_id": "run_1",
        }
    )
    repo.upsert_assertion(
        {
            "assertion_id": "asrt_1",
            "candidate_id": cid,
            "assertion_kind": "observed_fact",
            "assertion_text": "謝龍介舉辦造勢晚會",
            "subject": "謝龍介",
            "predicate": "舉辦",
            "object_text": "造勢晚會",
            "speaker": "",
            "evidence_article_id": "1",
            "evidence_field": "title",
            "evidence_text": "謝龍介舉辦造勢晚會",
            "confidence": 0.8,
            "risk_flags_json": "[]",
            "source_clause": "謝龍介舉辦造勢晚會",
            "classification_reasons_json": '["observed_marker:舉辦"]',
            "created_run_id": "run_1",
        }
    )
    repo.upsert_source(
        {
            "candidate_source_id": "csrc_cna",
            "normalized_source_name": "中央社",
            "normalized_domain": "cna.com.tw",
            "original_source_names_json": '["中央社"]',
            "formal_source_id": "src_fix_cna",
            "formal_match_status": "exact",
            "formal_match_basis": "domain_exact",
            "first_seen_at": "2026-08-01T00:00:00",
            "last_seen_at": "2026-08-01T00:00:00",
        }
    )
    repo.link_event_source(
        {
            "candidate_id": cid,
            "candidate_source_id": "csrc_cna",
            "news_article_id": "1",
            "relationship_type": "reported_by",
        }
    )
    repo.upsert_validation(
        {
            "candidate_id": cid,
            "validation_ready": 1,
            "errors_json": "[]",
            "warnings_json": "[]",
            "checked_at": "2026-08-01T00:00:00",
            "validator_version": "0.1.0",
        }
    )
    return candidate


def make_and_save_decision(
    repo,
    config,
    tmp_path: Path,
    candidate_id: str,
    decision: str,
    *,
    event: dict[str, Any] | None = None,
    sources: list[dict[str, Any]] | None = None,
    target: str | None = None,
    reviewer: str = "local_reviewer",
    reason: str = "human review",
) -> dict[str, Any]:
    template = export_review_template(repo, candidate_id, config)
    if event is not None:
        template["event"] = event
    if sources is not None:
        template["sources"] = sources
    template["decision"] = decision
    template["reviewer"] = reviewer
    template["review_reason"] = reason
    template["target_formal_event_id"] = target
    path = tmp_path / f"decision_{candidate_id}_{decision}.json"
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    return save_review_decision(repo, path, reviewer, config)


def default_event_payload(**over):
    payload = {
        "event_date": "2026-08-10T10:00:00+08:00",
        "event_date_precision": "day",
        "event_type": "campaign_event",
        "title": "謝龍介舉辦台南市長選舉造勢晚會",
        "summary": "謝龍介舉辦造勢晚會",
        "actors": ["謝龍介"],
        "themes": ["造勢"],
        "locations": ["台南"],
        "observed_facts": ["謝龍介舉辦造勢晚會"],
        "attributed_statements": [],
        "allegations": [],
        "limitations": [],
    }
    payload.update(over)
    return payload


def default_sources(reuse=True):
    if reuse:
        return [
            {
                "source_name": "中央社",
                "domain": "cna.com.tw",
                "formal_source_id": "src_fix_cna",
                "formal_match_status": "exact",
                "approve_new_source": False,
            }
        ]
    return [
        {
            "source_name": "新媒體",
            "domain": "newmedia.tw",
            "formal_source_id": "",
            "formal_match_status": "new_candidate_source",
            "approve_new_source": True,
            "title": "新媒體報導",
            "url": "https://newmedia.tw/news/1",
        }
    ]
