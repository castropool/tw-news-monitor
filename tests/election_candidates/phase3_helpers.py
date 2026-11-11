"""Shared helpers for Phase 3 (post-publication pipeline) tests."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from app.election_candidates.publication_pipeline import (
    batch_hash,
    commit_batch,
    prepare_batch,
)
from app.election_candidates.publication_preview import build_preview
from app.election_context.formal_state_hash import formal_state_business_hash_from_db

from .publication_helpers import (
    default_event_payload,
    default_sources,
    make_and_save_decision,
    make_publication_config,
    open_candidate_repo,
    seed_candidate,
)


FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "post_publication_pipeline"


def load_golden(name: str) -> list[dict]:
    return json.loads((FIXTURE / f"golden_{name}_cases.json").read_text(encoding="utf-8"))


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def make_phase3_env(
    tmp_path: Path,
    *,
    event_type: str = "campaign_event",
    event_date: str = "2026-07-27T12:00:00+08:00",
    new_source: bool = False,
    report_date: str | None = None,
):
    """Build an isolated committed publication batch + downstream refresh request."""
    config = make_publication_config(tmp_path)
    repo = open_candidate_repo(config)
    seed_candidate(repo)
    event = default_event_payload(
        event_type=event_type,
        event_date=event_date,
        title=f"fixture event {event_type}",
        summary=f"fixture event {event_type}",
        observed_facts=[f"fixture event {event_type}"],
    )
    sources = default_sources(reuse=not new_source)
    decision = make_and_save_decision(
        repo,
        config,
        tmp_path,
        "cand_tnn_abc123",
        "approve_new_event",
        event=event,
        sources=sources,
    )
    preview = build_preview(
        repo, config, "TW-2026-TNN-MAYOR", "local_reviewer",
        [decision["review_decision_id"]],
    )
    prepare_batch(
        repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"], preview,
        "local_reviewer",
    )
    commit_batch(
        repo, config, "TW-2026-TNN-MAYOR", preview["batch_id"],
        "local_reviewer", batch_hash(preview), preview,
    )
    batch_dir = config.path("output_root") / "publication_batches" / preview["batch_id"]
    request_path = batch_dir / "downstream_refresh_request.json"
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request["requested_period_start"] = "2026-07-16"
    request["requested_period_end"] = "2026-07-31"
    request["facts_cutoff"] = event_date[:10]
    request.setdefault("blocking_gaps", [])
    request_path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return {
        "config": config,
        "repo": repo,
        "preview": preview,
        "request_path": request_path,
        "batch_id": preview["batch_id"],
        "batch_dir": batch_dir,
    }


def write_request(
    request_path: Path,
    repo,
    config,
    batch_id: str,
    **over,
) -> Path:
    """Rewrite a downstream refresh request with test overrides."""
    request = json.loads(request_path.read_text(encoding="utf-8"))
    request.update(over)
    request_path.write_text(
        json.dumps(request, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return request_path


def current_hashes(config) -> dict[str, str]:
    return {
        "formal_state_hash": formal_state_business_hash_from_db(config.path("formal_db")),
        "news_db": sha256_file(config.path("news_db")),
        "watch_db": sha256_file(config.path("match_db")),
        "rc1": sha256_file(config.path("frozen_release_zip")),
    }


def refresh_batch_id_for(config, batch_id: str) -> str:
    return "dr_" + hashlib.sha256(batch_id.encode("utf-8")).hexdigest()[:16]
