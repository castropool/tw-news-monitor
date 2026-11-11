"""Human review decision workflow (append-only, stale-protected)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.time_utils import TAIPEI

from .candidate_repository import CandidateRepository
from .formal_id_allocator import payload_hash
from .state_machine import apply_status


ALLOWED_DECISIONS = {
    "approve_new_event",
    "attach_to_existing_event",
    "approve_as_subevent",
    "reject",
    "hold",
    "needs_edit",
}


def candidate_business_hash(repo: CandidateRepository, candidate_id: str) -> str:
    """Deterministic business hash of candidate + attached data (excluding timestamps/run ids)."""
    candidate = repo.get_candidate(candidate_id)
    if not candidate:
        raise ValueError(f"candidate not found: {candidate_id}")
    articles = repo.get_articles(candidate_id)
    assertions = repo.get_assertions(candidate_id)
    sources = repo.get_sources(candidate_id)
    suggestions = repo.get_duplicate_suggestions(candidate_id)
    payload = {
        "candidate": {k: v for k, v in candidate.items() if k not in (
            "first_seen_at", "last_updated_at", "created_run_id", "updated_run_id",
            "review_status", "status_reason_codes_json",
        )},
        "articles": [
            {k: v for k, v in a.items() if k != "attached_run_id"} for a in articles
        ],
        "assertions": [
            {k: v for k, v in a.items() if k not in ("created_run_id",)} for a in assertions
        ],
        "sources": [
            {k: v for k, v in s.items() if k not in ("first_seen_at", "last_seen_at")}
            for s in sources
        ],
        "suggestions": [
            {k: v for k, v in s.items() if k not in ("created_run_id",)} for s in suggestions
        ],
    }
    return hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def export_review_template(repo: CandidateRepository, candidate_id: str, config) -> dict[str, Any]:
    candidate = repo.get_candidate(candidate_id)
    if not candidate:
        raise ValueError(f"candidate not found: {candidate_id}")
    articles = repo.get_articles(candidate_id)
    assertions = repo.get_assertions(candidate_id)
    sources = repo.get_sources(candidate_id)
    observed = [a.get("assertion_text") for a in assertions if a["assertion_kind"] == "observed_fact"]
    statements = [a.get("assertion_text") for a in assertions if a["assertion_kind"] == "actor_statement"]
    allegations = [a.get("assertion_text") for a in assertions if a["assertion_kind"] == "allegation"]
    limitations = [
        a.get("assertion_text")
        for a in assertions
        if a["assertion_kind"] in ("uncertain_report", "unknown", "media_interpretation")
    ]
    template = {
        "candidate_id": candidate_id,
        "candidate_business_hash": candidate_business_hash(repo, candidate_id),
        "decision": "",
        "reviewer": "",
        "review_reason": "",
        "target_formal_event_id": None,
        "event": {
            "event_date": candidate.get("canonical_event_date", ""),
            "event_date_precision": candidate.get("event_date_precision", ""),
            "event_type": candidate.get("candidate_event_type", ""),
            "title": candidate.get("candidate_title", ""),
            "summary": candidate.get("candidate_summary", ""),
            "actors": json.loads(candidate.get("secondary_actors_json", "[]") or "[]"),
            "themes": json.loads(candidate.get("themes_json", "[]") or "[]"),
            "locations": json.loads(candidate.get("locations_json", "[]") or "[]"),
            "observed_facts": observed,
            "attributed_statements": statements,
            "allegations": allegations,
            "limitations": limitations,
        },
        "sources": [
            {
                "source_name": s.get("normalized_source_name", ""),
                "domain": s.get("normalized_domain", ""),
                "formal_source_id": s.get("formal_source_id", ""),
                "formal_match_status": s.get("formal_match_status", ""),
                "approve_new_source": False,
            }
            for s in sources
        ],
    }
    out_dir = Path(config.get("paths.output_root", "data/election_candidates/tainan_2026")) / "review_templates"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{candidate_id}.json"
    path.write_text(json.dumps(template, ensure_ascii=False, indent=2), encoding="utf-8")
    template["_template_path"] = str(path)
    return template


def save_review_decision(
    repo: CandidateRepository,
    decision_file: str | Path,
    reviewer: str,
    config,
    decision_id: str | None = None,
) -> dict[str, Any]:
    if not reviewer or reviewer.strip().lower() == "system":
        raise ValueError("reviewer is required and must not be 'system'")
    payload = json.loads(Path(decision_file).read_text(encoding="utf-8"))
    candidate_id = payload["candidate_id"]
    decision = payload.get("decision", "")
    if decision not in ALLOWED_DECISIONS:
        raise ValueError(f"invalid decision: {decision}")
    if decision in ("attach_to_existing_event", "approve_as_subevent"):
        if not payload.get("target_formal_event_id"):
            raise ValueError(f"{decision} requires target_formal_event_id")
    current_hash = candidate_business_hash(repo, candidate_id)
    template_hash = payload.get("candidate_business_hash", "")
    if template_hash and template_hash != current_hash:
        raise ValueError("candidate drifted since template export; re-export and re-review")
    now = datetime.now(TAIPEI).isoformat()
    seq = repo.conn.execute("SELECT COUNT(*) FROM review_decisions").fetchone()[0] + 1
    rid = decision_id or (
        f"rev_{seq:06d}_"
        + hashlib.sha256(f"{candidate_id}|{now}|{decision}".encode("utf-8")).hexdigest()[:12]
    )
    record = {
        "review_decision_id": rid,
        "candidate_id": candidate_id,
        "decision": decision,
        "reviewer": reviewer,
        "reviewed_at": now,
        "review_reason": payload.get("review_reason", ""),
        "edited_event_payload_json": json.dumps(payload.get("event", {}), ensure_ascii=False),
        "target_formal_event_id": payload.get("target_formal_event_id") or "",
        "source_resolution_json": json.dumps(payload.get("sources", []), ensure_ascii=False),
        "decision_version": "0.1.0",
        "candidate_business_hash": current_hash,
        "created_at": now,
    }
    repo.insert_review_decision(record)

    candidate = repo.get_candidate(candidate_id)
    current_status = candidate["review_status"]
    apply_status(repo, candidate_id, "under_review", updated_run_id=f"review:{rid}")
    if decision in ("approve_new_event", "attach_to_existing_event", "approve_as_subevent"):
        apply_status(repo, candidate_id, "review_approved", updated_run_id=f"review:{rid}")
    elif decision == "reject":
        apply_status(repo, candidate_id, "review_rejected", updated_run_id=f"review:{rid}")
    else:  # hold / needs_edit
        apply_status(repo, candidate_id, "hold", updated_run_id=f"review:{rid}")
    return record


def is_review_stale(repo: CandidateRepository, decision: dict[str, Any]) -> bool:
    return candidate_business_hash(repo, decision["candidate_id"]) != decision.get("candidate_business_hash", "")
