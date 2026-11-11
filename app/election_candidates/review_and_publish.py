"""Unified operator entry: one human decision -> safe automatic publication.

For approve-family decisions the command runs the existing publication chain
(preview -> validation -> prepare -> commit -> formal validation -> downstream
refresh) without asking the human again.  Reject/hold/needs_edit only record
the decision.  On technical failure the human decision is retained and
publication can be safely retried with --review-decision-id.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from .candidate_repository import CandidateRepository
from .config import load_config
from .publication_pipeline import batch_hash, commit_batch, prepare_batch
from .publication_preview import build_preview
from .review_workflow import save_review_decision
from .state_machine import apply_status


APPROVE_DECISIONS = {"approve_new_event", "attach_to_existing_event", "approve_as_subevent"}
RECORD_ONLY_DECISIONS = {"reject", "hold", "needs_edit"}


def execute_review_and_publish(
    repo: CandidateRepository,
    config,
    *,
    reviewer: str,
    decision_file: str | Path | None = None,
    review_decision_id: str | None = None,
    election_id: str | None = None,
    output_root: str | Path | None = None,
    no_downstream: bool = False,
) -> dict[str, Any]:
    if not reviewer or reviewer.strip().lower() == "system":
        raise ValueError("reviewer is required and must not be 'system'")
    if bool(decision_file) == bool(review_decision_id):
        raise ValueError("exactly one of --decision-file or --review-decision-id is required")

    rid: str | None = None
    decision_recorded = False
    if decision_file:
        record = save_review_decision(repo, decision_file, reviewer, config)
        rid = record["review_decision_id"]
        decision_recorded = True
    else:
        rid = review_decision_id

    decision = repo.get_review_decision(rid)
    if not decision:
        raise ValueError(f"review decision not found: {rid}")
    candidate_id = decision["candidate_id"]
    decision_type = decision["decision"]
    election_id = config.resolve_election_id(election_id)

    if decision_type in RECORD_ONLY_DECISIONS:
        return {
            "review_decision_recorded": decision_recorded,
            "review_decision_id": rid,
            "candidate_id": candidate_id,
            "decision": decision_type,
            "publication_attempted": False,
            "publication_status": "not_attempted",
            "formal_validation_status": "not_run",
            "downstream_refresh_status": "not_run",
            "errors": [],
        }
    if decision_type not in APPROVE_DECISIONS:
        raise ValueError(f"unsupported decision: {decision_type}")

    candidate = repo.get_candidate(candidate_id)
    if not candidate:
        raise ValueError(f"candidate not found: {candidate_id}")

    # Safe retry: a failed publication may be retried without a new approval.
    if candidate["review_status"] == "publication_failed":
        apply_status(repo, candidate_id, "publication_prepared", updated_run_id=f"retry:{rid}")
    elif candidate["review_status"] == "published":
        batches = [
            b for b in repo.list_publication_batches(limit=1000)
            if rid in json.loads(b.get("review_decision_ids_json", "[]") or "[]")
        ]
        if not any(b.get("status") == "rolled_back" for b in batches):
            raise ValueError(
                "candidate already published and no rolled-back batch for this decision; "
                "rollback is required before retry"
            )
        apply_status(repo, candidate_id, "rolled_back", updated_run_id=f"retry:{rid}")
        apply_status(repo, candidate_id, "under_review", updated_run_id=f"retry:{rid}")
        apply_status(repo, candidate_id, "review_approved", updated_run_id=f"retry:{rid}")

    result: dict[str, Any] = {
        "review_decision_recorded": decision_recorded,
        "review_decision_id": rid,
        "candidate_id": candidate_id,
        "decision": decision_type,
        "publication_attempted": True,
        "publication_status": "started",
        "formal_validation_status": "not_run",
        "downstream_refresh_status": "not_run",
        "batch_id": "",
        "errors": [],
    }
    try:
        preview = build_preview(
            repo, config, election_id, reviewer, [rid],
            output_root=output_root,
        )
        if preview["errors"]:
            result["publication_status"] = "preview_failed"
            result["errors"] = preview["errors"]
            return result
        batch_id = preview["batch_id"]
        result["batch_id"] = batch_id

        prepare_batch(repo, config, election_id, batch_id, preview, reviewer)
        current_status = repo.get_candidate(candidate_id)["review_status"]
        if current_status != "publication_prepared":
            apply_status(
                repo, candidate_id, "publication_prepared", updated_run_id=f"pub:{batch_id}"
            )
        result["publication_status"] = "prepared"

        commit = commit_batch(
            repo, config, election_id, batch_id, reviewer,
            batch_hash(preview), preview,
        )
        apply_status(repo, candidate_id, "published", updated_run_id=f"pub:{batch_id}")
        post = commit.get("post_commit_validation", {})
        result["publication_status"] = "committed"
        result["formal_validation_status"] = (
            "passed" if post.get("post_commit_ready") else "failed"
        )
        if not post.get("post_commit_ready"):
            result["errors"].extend(post.get("errors", []))
            return result

        if no_downstream:
            result["downstream_refresh_status"] = "skipped_by_flag"
        elif preview.get("new_events") or preview.get("new_sources"):
            batch_dir = config.path("output_root") / "publication_batches" / batch_id
            request_path = batch_dir / "downstream_refresh_request.json"
            from app.election_context.run_post_publication_pipeline import (
                run_post_publication_pipeline,
            )

            downstream = run_post_publication_pipeline(
                repo,
                config,
                publication_batch_id=batch_id,
                request_path=request_path,
                run_date=None,
                manual=True,
                allow_real_snapshot=False,
            )
            result["downstream_refresh_status"] = downstream.get(
                "snapshot", {}
            ).get("status", "completed")
            result["refresh_batch_id"] = downstream.get("refresh_batch_id", "")
        else:
            result["downstream_refresh_status"] = "skipped_attach_only_no_new_ids"
        return result
    except Exception as exc:
        current = repo.get_candidate(candidate_id)
        if current and current["review_status"] == "publication_prepared":
            apply_status(
                repo, candidate_id, "publication_failed", updated_run_id=f"fail:{rid}"
            )
        result["publication_status"] = "failed"
        result["errors"] = [str(exc)]
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Human review + safe auto publication")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--candidate-db", default=None)
    parser.add_argument("--election-id", default=None)
    parser.add_argument("--decision-file", default=None)
    parser.add_argument("--review-decision-id", default=None)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--no-downstream", action="store_true")
    args = parser.parse_args()

    config = load_config(args.config)
    repo = CandidateRepository(args.candidate_db or config.path("candidate_db"))
    repo.connect()
    repo.create_tables()
    try:
        result = execute_review_and_publish(
            repo,
            config,
            reviewer=args.reviewer,
            decision_file=args.decision_file,
            review_decision_id=args.review_decision_id,
            election_id=args.election_id,
            output_root=args.output_root,
            no_downstream=args.no_downstream,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
