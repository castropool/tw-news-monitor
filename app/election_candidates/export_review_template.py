"""CLI: export a review template for a candidate."""

from __future__ import annotations

import argparse
import json

from .candidate_repository import CandidateRepository
from .config import load_config
from .review_workflow import export_review_template


def main():
    parser = argparse.ArgumentParser(description="Export review template")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--candidate-db", default=None)
    parser.add_argument("--candidate-id", required=True)
    args = parser.parse_args()
    config = load_config(args.config)
    repo = CandidateRepository(args.candidate_db or config.path("candidate_db"))
    repo.connect()
    repo.create_tables()
    try:
        template = export_review_template(repo, args.candidate_id, config)
        print(json.dumps(template, ensure_ascii=False, indent=2))
    finally:
        repo.close()


if __name__ == "__main__":
    main()
