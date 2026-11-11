"""Single source of truth for coverage acceptance rules (Phase 3.5)."""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import yaml


DEFAULT_RULES: dict[str, Any] = {
    "coverage": {
        "full_requires": {
            "facts_cutoff_reaches_period_end": True,
            "formal_state_ready": True,
            "blocking_gaps_empty": True,
        },
        "no_event_day_is_gap": False,
        "poll": {"absence_of_new_poll_is_blocking": False},
        "gap_kinds": {
            "blocking": [
                "unreviewed_period",
                "missing_required_source",
                "missing_required_dimension",
                "unresolved_conflict",
                "genuinely_uncovered",
            ],
            "non_blocking": [
                "no_event",
                "no_poll",
                "reviewed_no_material_event",
                "soft_limitation",
                "non_blocking_gap",
            ],
        },
        "reviewed_no_material_event_semantics": "reviewed_no_material_event",
        "facts_cutoff": {
            "derivation": "authoritative_input_only",
            "fallback_when_missing": "partial_without_cutoff",
        },
        "latest_event_date": {"derivation": "max_event_date_in_period"},
    }
}


def _rules_path(config) -> Path:
    return config.root / "config" / "coverage_acceptance_rules.yaml"


def load_acceptance_rules(config) -> dict[str, Any]:
    """Load the unified coverage acceptance rules (file wins, defaults fallback)."""
    path = _rules_path(config)
    if path.exists():
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return {**DEFAULT_RULES, **raw}
    return DEFAULT_RULES


def rules_hash(rules: dict[str, Any] | None = None) -> str:
    rules = rules or DEFAULT_RULES
    return hashlib.sha256(
        __import__("json").dumps(rules, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()


def full_requires_facts_cutoff_reaches_period_end(rules: dict[str, Any]) -> bool:
    return bool(
        (rules.get("coverage") or {}).get("full_requires", {}).get(
            "facts_cutoff_reaches_period_end", True
        )
    )


def full_requires_formal_state_ready(rules: dict[str, Any]) -> bool:
    return bool(
        (rules.get("coverage") or {}).get("full_requires", {}).get(
            "formal_state_ready", True
        )
    )


def no_event_day_is_gap(rules: dict[str, Any]) -> bool:
    return bool((rules.get("coverage") or {}).get("no_event_day_is_gap", False))


def poll_absence_is_blocking(rules: dict[str, Any]) -> bool:
    return bool(
        (rules.get("coverage") or {}).get("poll", {}).get(
            "absence_of_new_poll_is_blocking", False
        )
    )


def blocking_gap_kinds(rules: dict[str, Any]) -> set[str]:
    return set((rules.get("coverage") or {}).get("gap_kinds", {}).get("blocking", []))


def non_blocking_gap_kinds(rules: dict[str, Any]) -> set[str]:
    return set((rules.get("coverage") or {}).get("gap_kinds", {}).get("non_blocking", []))


def gap_kind_is_blocking(kind: str, rules: dict[str, Any]) -> bool:
    return kind in blocking_gap_kinds(rules)
