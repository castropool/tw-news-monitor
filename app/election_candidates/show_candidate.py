"""Read-only candidate detail CLI."""

from __future__ import annotations

import argparse
import json

from .candidate_repository import CandidateRepository
from .config import load_config


def main() -> None:
    parser = argparse.ArgumentParser(description="Show candidate detail (read-only)")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--candidate-db", default=None)
    parser.add_argument("--candidate-id", required=True)
    args = parser.parse_args()

    config = load_config(args.config)
    db = args.candidate_db or config.path("candidate_db")
    repo = CandidateRepository(db)
    repo.connect()
    try:
        candidate = repo.get_candidate(args.candidate_id)
        if not candidate:
            raise SystemExit(f"candidate not found: {args.candidate_id}")
        payload = {
            "candidate": candidate,
            "articles": repo.get_articles(args.candidate_id),
            "assertions": repo.get_assertions(args.candidate_id),
            "sources": repo.get_sources(args.candidate_id),
            "formal_duplicate_suggestions": repo.get_duplicate_suggestions(args.candidate_id),
            "validation": repo.get_validation(args.candidate_id),
        }
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
