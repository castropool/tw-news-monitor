"""CLI: save a human review decision (append-only)."""

from __future__ import annotations

import argparse
import json

from .candidate_repository import CandidateRepository
from .config import load_config
from .review_workflow import save_review_decision


def main():
    parser = argparse.ArgumentParser(description="Record human review decision")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--candidate-db", default=None)
    parser.add_argument("--candidate-id", required=True)
    parser.add_argument("--decision", required=True)
    parser.add_argument("--decision-file", required=True)
    parser.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    repo = CandidateRepository(args.candidate_db or config.path("candidate_db"))
    repo.connect()
    repo.create_tables()
    try:
        record = save_review_decision(
            repo, args.decision_file, args.reviewer, config
        )
        print(json.dumps(record, ensure_ascii=False, indent=2))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
