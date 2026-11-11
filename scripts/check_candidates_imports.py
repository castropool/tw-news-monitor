"""Compile and import the candidate pipeline package for a quick sanity check."""

from __future__ import annotations

import importlib
import py_compile
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "app" / "election_candidates"
sys.path.insert(0, str(ROOT))


def main():
    modules = [
        "config", "candidate_models", "input_inspector", "inspect_inputs",
        "news_reader", "match_reader", "article_normalizer", "candidate_id",
        "event_clusterer", "assertion_classifier", "source_resolver",
        "formal_duplicate_checker", "candidate_scorer", "candidate_router",
        "candidate_validator", "candidate_repository", "preview_renderer",
        "build_candidate_queue", "list_candidates", "show_candidate",
    ]
    errors = []
    for name in modules:
        path = PACKAGE / f"{name}.py"
        try:
            py_compile.compile(str(path), doraise=True)
            importlib.import_module(f"app.election_candidates.{name}")
            print("OK", name)
        except Exception as exc:
            errors.append(f"{name}: {exc}")
            print("FAIL", name, exc)
    if errors:
        raise SystemExit("\n".join(errors))
    print("all candidate modules import OK")


if __name__ == "__main__":
    main()
