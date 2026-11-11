"""Conservative policy gate for low-risk automated candidate publication.

Decision origin is ``automated_policy``.  Only candidates inside an extremely
conservative low-risk subset may be auto-published; every other candidate
(mid/high risk, statements, allegations, inferred dates, possible duplicates,
new/unresolved sources, unsafe fact profiles, any terminal/approve history)
stays in the human review queue.

This module is READ-ONLY: it never writes to the candidate DB, the formal DB
or any manifest.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .candidate_repository import CandidateRepository, TERMINAL_REVIEW_STATUSES
from .formal_duplicate_checker import check_candidate_duplicates, load_formal_events
from .review_workflow import candidate_business_hash

APPROVE_DECISIONS = {
    "approve_new_event",
    "attach_to_existing_event",
    "approve_as_subevent",
}

# Review statuses a candidate must currently be in to enter auto evaluation.
# `under_review` and beyond mean a human (or an in-flight auto run) is already
# acting on the candidate; auto approval only picks untouched queue items.
ELIGIBLE_INPUT_STATUSES = {"new", "review_required"}

ALLOWED_ASSERTION_KINDS = {
    "observed_fact",
    "actor_statement",
    "allegation",
    "media_interpretation",
    "planned_action",
    "uncertain_report",
    "unknown",
}


@dataclass(slots=True)
class AutoPublishPolicy:
    """Configuration snapshot for the automated publication policy (v1)."""

    enabled: bool = False
    policy_version: str = "1.0"
    max_per_run: int = 3
    max_daily: int = 10
    consecutive_failure_limit: int = 2
    auto_approver: str = "auto_approver_v1"
    kill_switch_file: Path = Path("data/locks/auto_publish_disabled")
    circuit_break_file: Path = Path("data/locks/auto_publish_circuit_open")
    manifest_dir: Path = Path("data/election_candidates/tainan_2026/auto_publish")
    allowed_risk_levels: tuple[str, ...] = ("low",)
    allowed_relevance_labels: tuple[str, ...] = ("direct_event",)
    forbidden_event_date_basis: tuple[str, ...] = ("unknown",)
    allowed_source_match_statuses: tuple[str, ...] = ("exact", "normalized_match")
    required_formal_duplicate_status: str = "no_match"
    required_validation_ready: bool = True
    # Post-publication wiring (downstream snapshot/coverage activation).
    # Production default refuses --skip-downstream and keeps real snapshot
    # activation off; tests enable both explicitly.
    allow_skip_downstream: bool = False
    auto_activate_snapshots: bool = False
    # Runtime flag set from the CLI (never persisted).
    skip_downstream: bool = False

    @classmethod
    def from_config(cls, config) -> "AutoPublishPolicy":
        raw = config.get("auto_publish", {}) or {}
        if not isinstance(raw, dict):
            raw = {}

        def path_value(key: str, default_rel: str) -> Path:
            v = config.get(f"auto_publish.{key}")
            if v is None:
                return Path(config.root) / default_rel
            return Path(v)

        return cls(
            enabled=bool(raw.get("enabled", False)),
            policy_version=str(raw.get("policy_version", "1.0")),
            max_per_run=int(raw.get("max_per_run", 3)),
            max_daily=int(raw.get("max_daily", 10)),
            consecutive_failure_limit=int(raw.get("consecutive_failure_limit", 2)),
            auto_approver=str(raw.get("auto_approver", "auto_approver_v1")),
            kill_switch_file=path_value("kill_switch_file", "data/locks/auto_publish_disabled"),
            circuit_break_file=path_value("circuit_break_file", "data/locks/auto_publish_circuit_open"),
            manifest_dir=path_value("manifest_dir", "data/election_candidates/tainan_2026/auto_publish"),
            allowed_risk_levels=tuple(str(x) for x in (raw.get("allowed_risk_levels") or ("low",))),
            allowed_relevance_labels=tuple(
                str(x) for x in (raw.get("allowed_relevance_labels") or ("direct_event",))
            ),
            forbidden_event_date_basis=tuple(
                str(x) for x in (raw.get("forbidden_event_date_basis") or ("unknown",))
            ),
            allowed_source_match_statuses=tuple(
                str(x) for x in (raw.get("allowed_source_match_statuses") or ("exact", "normalized_match"))
            ),
            required_formal_duplicate_status=str(
                raw.get("required_formal_duplicate_status", "no_match")
            ),
            required_validation_ready=bool(raw.get("required_validation_ready", True)),
            allow_skip_downstream=bool(raw.get("allow_skip_downstream", False)),
            auto_activate_snapshots=bool(raw.get("auto_activate_snapshots", False)),
        )


def _load_json(value: Any, default: Any) -> Any:
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except (TypeError, json.JSONDecodeError):
        return default


def _recheck_duplicate_issues(
    repo: CandidateRepository,
    config,
    candidate: dict[str, Any],
    formal_db_override: str | Path | None,
) -> list[str]:
    """Fresh read-only duplicate check against the current formal database.

    Any suggestion other than ``no_material_match`` (including manual_review)
    makes the candidate ineligible for automated publication.
    """
    formal_db = formal_db_override or config.path("formal_db")
    if not Path(formal_db).exists():
        return ["formal_db_missing"]
    election_id = candidate.get("election_id") or config.canonical_election_id
    formal_events = load_formal_events(formal_db, election_id, config)
    sources = repo.get_sources(candidate["candidate_id"])
    domains = {str(s.get("normalized_domain") or "") for s in sources}
    domains.discard("")
    suggestions = check_candidate_duplicates(
        candidate, formal_events, config, run_id="auto_gate", candidate_source_domains=domains
    )
    issues = []
    for s in suggestions:
        action = s.get("suggested_action", "")
        if action != "no_material_match":
            issues.append(
                f"{action}:{s.get('formal_event_id', '?')}(score={s.get('similarity_score', 0)})"
            )
    return issues


def evaluate_candidate(
    repo: CandidateRepository,
    config,
    candidate_id: str,
    policy: AutoPublishPolicy,
    *,
    formal_db_override: str | Path | None = None,
) -> dict[str, Any]:
    """Evaluate one candidate against the automated publication policy.

    Returns an audit-friendly dict with per-gate results; never writes.
    """
    candidate = repo.get_candidate(candidate_id)
    if candidate is None:
        return {
            "candidate_id": candidate_id,
            "decision": "rejected",
            "candidate_business_hash": "",
            "gate_results": [
                {"gate": "candidate_exists", "passed": False, "reason": "candidate_not_found"}
            ],
            "reasons": ["candidate_not_found"],
        }

    gates: list[dict[str, Any]] = []

    def check(name: str, passed: bool, detail: str = ""):
        gates.append(
            {"gate": name, "passed": bool(passed), "reason": "" if passed else f"{name}:{detail}"}
        )

    # 2) risk level must be the most conservative one
    risk = candidate.get("risk_level", "")
    check("risk_level_low", risk in policy.allowed_risk_levels, f"risk_level={risk!r}")

    # 2) relevance must be a direct event, not a statement/context
    rel = candidate.get("relevance_label", "")
    check(
        "relevance_direct_event",
        rel in policy.allowed_relevance_labels,
        f"relevance_label={rel!r}",
    )

    # 2) observed fact must be present in the assertion profile
    profile = _load_json(candidate.get("assertion_profile_json", "{}"), {})
    profile_has_fact = bool(
        (profile.get("has_observed_fact") if isinstance(profile, dict) else None)
    )
    assertion_facts = [
        a
        for a in repo.get_assertions(candidate_id)
        if a.get("assertion_kind") == "observed_fact"
    ]
    check(
        "has_observed_fact",
        profile_has_fact or bool(assertion_facts),
        "no observed_fact assertion",
    )

    # 3) explicit date: unknown / inferred-from-publication dates are banned
    date = candidate.get("canonical_event_date") or ""
    basis = candidate.get("event_date_basis") or ""
    precision = candidate.get("event_date_precision") or ""
    confidence = candidate.get("event_date_confidence") or ""
    date_ok = bool(date) and basis not in policy.forbidden_event_date_basis
    check(
        "date_explicit",
        date_ok and precision != "unknown" and confidence != "unknown",
        f"date={date!r} basis={basis!r} precision={precision!r} confidence={confidence!r}",
    )

    # 3) event type must be known
    etype = candidate.get("candidate_event_type") or ""
    check("event_type_known", bool(etype) and etype != "unknown", f"event_type={etype!r}")

    # 3) candidate must pass the existing validator
    validation = repo.get_validation(candidate_id) or {}
    if policy.required_validation_ready:
        check(
            "validation_ready",
            bool(validation.get("validation_ready")),
            "validation_ready != 1",
        )
    else:
        check("validation_ready", True)

    # 4) persisted duplicate status must be no_match
    dup_status = candidate.get("formal_duplicate_status") or ""
    check(
        "duplicate_status_no_match",
        dup_status == policy.required_formal_duplicate_status,
        f"formal_duplicate_status={dup_status!r}",
    )

    # 4) fresh duplicate re-check against the current formal database
    dup_issues = _recheck_duplicate_issues(repo, config, candidate, formal_db_override)
    check("duplicate_recheck_clean", not dup_issues, "; ".join(dup_issues))

    # 4) all sources must be resolved to an existing formal source
    sources = repo.get_sources(candidate_id)
    source_issues = []
    if not sources:
        source_issues.append("no_sources")
    for s in sources:
        status = s.get("formal_match_status", "")
        formal_id = s.get("formal_source_id", "")
        if not formal_id or status not in policy.allowed_source_match_statuses:
            source_issues.append(
                f"{s.get('normalized_source_name', '?')}:{status}(formal_id={formal_id!r})"
            )
    check("sources_resolved", not source_issues, "; ".join(source_issues))

    # 5) no terminal status / publication history
    check(
        "no_terminal_status",
        candidate.get("review_status") not in TERMINAL_REVIEW_STATUSES,
        f"review_status={candidate.get('review_status')!r}",
    )

    # 5) no approve-family decision anywhere in the candidate history
    approve_history = [
        d for d in repo.list_review_decisions(candidate_id)
        if d.get("decision") in APPROVE_DECISIONS
    ]
    check(
        "no_approve_decision",
        not approve_history,
        f"history={','.join(d['decision'] for d in approve_history)}",
    )

    passed = all(g["passed"] for g in gates)
    try:
        business_hash = candidate_business_hash(repo, candidate_id)
    except ValueError:
        business_hash = ""
    return {
        "candidate_id": candidate_id,
        "decision": "eligible" if passed else "rejected",
        "candidate_business_hash": business_hash,
        "gate_results": gates,
        "reasons": [g["reason"] for g in gates if not g["passed"]],
    }
