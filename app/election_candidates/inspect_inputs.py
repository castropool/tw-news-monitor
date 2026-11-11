"""CLI: read-only input inspection for the candidate pipeline."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .config import load_config
from .input_inspector import run_inspection


def main() -> None:
    parser = argparse.ArgumentParser(description="Inspect candidate pipeline inputs")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--election-id", default=None)
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    result = run_inspection(
        config,
        election_id=args.election_id,
        output_root=args.output_root,
    )
    summary_path = Path(result["output_root"]) / "input_inspection_summary.md"
    print(f"input_inspection_ready=true")
    print(f"summary={summary_path}")
    formal = result["formal_db"]
    print(
        "formal_counts="
        f"events={formal.get('event_count')},"
        f"sources={formal.get('source_count')},"
        f"links={formal.get('event_source_link_count')},"
        f"polls={formal.get('poll_count')}"
    )


if __name__ == "__main__":
    main()
