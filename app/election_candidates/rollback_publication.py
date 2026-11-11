"""CLI: rollback a committed publication batch."""

from __future__ import annotations

import argparse
import json

from .candidate_repository import CandidateRepository
from .config import load_config
from .publication_pipeline import rollback_batch


def main():
    parser = argparse.ArgumentParser(description="Rollback a publication batch")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--candidate-db", default=None)
    parser.add_argument("--election-id", default=None)
    parser.add_argument("--batch-id", required=True)
    parser.add_argument("--reviewer", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    election_id = config.resolve_election_id(args.election_id)
    repo = CandidateRepository(args.candidate_db or config.path("candidate_db"))
    repo.connect()
    repo.create_tables()
    try:
        result = rollback_batch(repo, config, election_id, args.batch_id, args.reviewer)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
