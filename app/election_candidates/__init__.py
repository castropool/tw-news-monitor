"""Candidate fact pipeline (phase 1): read-only candidate generation.

This package never writes to news.db, election_watch.db article_matches,
election_context.db, or any formal seed/release artifact.
"""

from .config import CandidatePipelineConfig, load_config

__all__ = ["CandidatePipelineConfig", "load_config"]

CANDIDATE_PIPELINE_VERSION = "0.1.0"
CANDIDATE_SCHEMA_VERSION = "1.0"
