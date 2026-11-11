"""CLI: prepare or commit a reviewed publication batch."""

from __future__ import annotations

import argparse
import json

from .candidate_repository import CandidateRepository
from .config import load_config
from .publication_pipeline import (
    batch_hash,
    commit_batch,
    detect_recovery_required,
    prepare_batch,
)
from .publication_preview import build_preview


def main():
    parser = argparse.ArgumentParser(description="Publish reviewed candidates")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--candidate-db", default=None)
    parser.add_argument("--election-id", default=None)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--prepare", action="store_true")
    parser.add_argument("--commit", action="store_true")
    parser.add_argument("--expected-batch-hash", default=None)
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args()
    if args.prepare == args.commit:
        parser.error("exactly one of --prepare or --commit is required")
    config = load_config(args.config)
    election_id = config.resolve_election_id(args.election_id)
    repo = CandidateRepository(args.candidate_db or config.path("candidate_db"))
    repo.connect()
    repo.create_tables()
    try:
        batch = repo.get_publication_batch(args.batch_id)
        if not batch:
            raise SystemExit(f"batch not found: {args.batch_id}")
        decision_ids = json.loads(batch.get("review_decision_ids_json", "[]"))
        preview = build_preview(
            repo, config, election_id, args.reviewer, decision_ids,
            batch_id=args.batch_id, output_root=args.output_root,
        )
        recovery = detect_recovery_required(config, args.batch_id)
        if recovery["recovery_required"]:
            raise SystemExit("recovery_required=true; do not start a new publish")
        if args.prepare:
            result = prepare_batch(
                repo, config, election_id, args.batch_id, preview, args.reviewer
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            if not args.expected_batch_hash:
                parser.error("--expected-batch-hash is required for --commit")
            result = commit_batch(
                repo, config, election_id, args.batch_id, args.reviewer,
                args.expected_batch_hash, preview,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
