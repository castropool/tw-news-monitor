"""Read-only candidate listing CLI."""

from __future__ import annotations

import argparse
import json

from .candidate_repository import CandidateRepository
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="List candidate events (read-only)")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--candidate-db", default=None)
    parser.add_argument("--election-id", default=None)
    parser.add_argument("--status", default=None)
    parser.add_argument("--event-date", default=None)
    parser.add_argument("--actor", default=None)
    parser.add_argument("--event-type", default=None)
    parser.add_argument("--risk-level", default=None)
    parser.add_argument("--formal-duplicate-status", default=None)
    parser.add_argument("--limit", type=int, default=200)
    args = parser.parse_args()

    config = load_config(args.config)
    db = args.candidate_db or config.path("candidate_db")
    repo = CandidateRepository(db)
    repo.connect()
    try:
        candidates = repo.list_candidates(
            status=args.status,
            event_date=args.event_date,
            actor=args.actor,
            event_type=args.event_type,
            risk_level=args.risk_level,
            formal_duplicate_status=args.formal_duplicate_status,
            limit=args.limit,
        )
        for c in candidates:
            print(
                f"{c['candidate_id']} | {c['review_status']} | {c.get('canonical_event_date', '')} | "
                f"{c.get('primary_actor', '')} | {c.get('candidate_title', '')[:60]}"
            )
        print(f"count={len(candidates)}")
    finally:
        repo.close()


if __name__ == "__main__":
    main()
