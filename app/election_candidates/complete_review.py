"""CLI: complete human review through a date (contiguous only).

Usage:
  python -m app.election_candidates.complete_review \
      --through 2026-08-07 --reviewer <name> [--from 2026-08-01]
"""

from __future__ import annotations

import argparse
import json

from .candidate_repository import CandidateRepository
from .config import load_config
from .review_completion import complete_review_through


def main() -> None:
    parser = argparse.ArgumentParser(description="Complete human review through a date")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--candidate-db", default=None)
    parser.add_argument("--election-id", default=None)
    parser.add_argument("--through", required=True, help="YYYY-MM-DD")
    parser.add_argument("--from", dest="from_date", default=None, help="YYYY-MM-DD (default: last completed review day)")
    parser.add_argument("--reviewer", required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    election_id = config.resolve_election_id(args.election_id)
    repo = CandidateRepository(args.candidate_db or config.path("candidate_db"))
    repo.connect()
    repo.create_tables()
    try:
        result = complete_review_through(
            repo,
            config,
            election_id=election_id,
            through_date=args.through,
            reviewer=args.reviewer,
            from_date=args.from_date,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
