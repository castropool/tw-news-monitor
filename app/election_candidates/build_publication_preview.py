"""CLI: build a publication preview from review decisions."""

from __future__ import annotations

import argparse
import json

from .candidate_repository import CandidateRepository
from .config import load_config
from .publication_preview import build_preview


def main():
    parser = argparse.ArgumentParser(description="Build publication preview")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--candidate-db", default=None)
    parser.add_argument("--election-id", default=None)
    parser.add_argument("--review-decision-id", action="append", required=True)
    parser.add_argument("--reviewer", required=True)
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args()
    config = load_config(args.config)
    election_id = config.resolve_election_id(args.election_id)
    repo = CandidateRepository(args.candidate_db or config.path("candidate_db"))
    repo.connect()
    repo.create_tables()
    try:
        preview = build_preview(
            repo,
            config,
            election_id,
            args.reviewer,
            args.review_decision_id,
            output_root=args.output_root,
        )
        print(json.dumps(preview, ensure_ascii=False, indent=2))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
