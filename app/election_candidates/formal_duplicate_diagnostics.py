"""Diagnostics for formal duplicate suggestions (explainable, read-only)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .candidate_repository import CandidateRepository


def build_formal_duplicate_diagnostics(repo: CandidateRepository, config) -> dict[str, Any]:
    candidates = repo.list_candidates(limit=100000)
    all_suggestions: list[dict[str, Any]] = []
    per_candidate_top5: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        suggestions = repo.get_duplicate_suggestions(c["candidate_id"])
        all_suggestions.extend(suggestions)
        per_candidate_top5[c["candidate_id"]] = suggestions[:5]
    component_keys = [
        "date_score", "actor_score", "event_type_score", "keyword_score",
        "source_overlap_score",
    ]
    zero_rates = {}
    means = {}
    for key in component_keys:
        values = [float(s.get(key, 0) or 0) for s in all_suggestions]
        zero_rates[key] = round(sum(1 for v in values if v == 0) / max(1, len(values)), 4)
        means[key] = round(sum(values) / max(1, len(values)), 4)
    action_counts: dict[str, int] = {}
    for s in all_suggestions:
        action_counts[s.get("suggested_action", "missing")] = action_counts.get(
            s.get("suggested_action", "missing"), 0
        ) + 1
    return {
        "candidate_count": len(candidates),
        "suggestion_count": len(all_suggestions),
        "suggestions_per_candidate": round(len(all_suggestions) / max(1, len(candidates)), 2),
        "component_zero_rates": zero_rates,
        "component_means": means,
        "suggested_action_distribution": action_counts,
        "per_candidate_top5": per_candidate_top5,
        "diagnostic_notes": [
            "Top 5 are always retained in formal_duplicate_diagnostics.json; "
            "no suggestion is collapsed to no_material_match without its component scores.",
        ],
    }


def write_diagnostics(diagnostics: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
