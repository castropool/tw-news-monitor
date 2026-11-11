"""Deterministic coverage builder over authoritative formal state (Phase 3.5).

Authoritative semantics:
  - facts_cutoff       = research/review cutoff, supplied by authoritative input
                         (coverage preflight / refresh request). NEVER derived
                         from MAX(event_date).
  - latest_event_date  = MAX(occurred_at) of formal events inside the period.
  - coverage_status    = full only when the unified acceptance rules are met
                         (cutoff reaches period end, formal state ready, no
                         blocking gaps); otherwise partial.
  - no-event days and missing new polls are NOT coverage gaps by themselves.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from app.election_context.coverage_rules import (
    DEFAULT_RULES,
    blocking_gap_kinds,
    full_requires_facts_cutoff_reaches_period_end,
    gap_kind_is_blocking,
    load_acceptance_rules,
    non_blocking_gap_kinds,
    no_event_day_is_gap,
    poll_absence_is_blocking,
    rules_hash,
)
from app.election_context.formal_state_hash import formal_state_business_hash_from_db


COVERAGE_SCHEMA_VERSION = "1.1"
VERSION_PATTERN = re.compile(r"^fact_coverage_\d{8}_v\d{3}$")
ALLOWED_COVERAGE_STATUS = {"full", "partial", "no_formal_event_recorded"}


def _d(v: str | None) -> date | None:
    if not v:
        return None
    s = str(v)[:10]
    try:
        return datetime.fromisoformat(s).date()
    except ValueError:
        return None


def _config_hash(config) -> str:
    cfg_path = config.root / "config" / "election_candidate_pipeline.yaml"
    raw = cfg_path.read_bytes() if cfg_path.exists() else b""
    return hashlib.sha256(raw).hexdigest()


def _normalize_blocking_gaps(gaps: list[Any] | None) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for g in gaps or []:
        if isinstance(g, str):
            out.append({"kind": "genuinely_uncovered", "start": None, "end": None, "reason": g})
        elif isinstance(g, dict):
            out.append(
                {
                    "kind": g.get("kind", "genuinely_uncovered"),
                    "start": g.get("start"),
                    "end": g.get("end"),
                    "reason": g.get("reason", ""),
                }
            )
    return out


def _unreviewed_range(facts_cutoff: date | None, period_end: date) -> dict[str, Any] | None:
    if facts_cutoff is None:
        return {
            "kind": "unreviewed_period",
            "start": None,
            "end": None,
            "reason": "facts_cutoff_not_provided; review state unknown",
        }
    if facts_cutoff < period_end:
        return {
            "kind": "unreviewed_period",
            "start": (facts_cutoff + timedelta(days=1)).isoformat(),
            "end": period_end.isoformat(),
            "reason": "research cutoff before requested period end",
        }
    return None


def compute_coverage_payload(
    *,
    events: list[dict[str, Any]],
    polls: list[dict[str, Any]],
    event_source_ids: list[dict[str, str]] | None = None,
    poll_source_ids: list[dict[str, str]] | None = None,
    source_count: int,
    requested_start: str,
    requested_end: str,
    facts_cutoff: str | None = None,
    facts_cutoff_provenance: str = "authoritative_input",
    blocking_gaps: list[Any] | None = None,
    known_gaps: list[str] | None = None,
    dimensions: list[str] | None = None,
    formal_state_hash: str = "",
    configuration_hash: str = "",
    election_id: str = "",
    acceptance_rules: dict[str, Any] | None = None,
    formal_state_ready: bool = True,
) -> dict[str, Any]:
    ps = _d(requested_start)
    pe = _d(requested_end)
    if ps is None or pe is None:
        raise ValueError("requested_start/requested_end must be ISO dates")
    if pe < ps:
        raise ValueError("requested_end must not be earlier than requested_start")
    if facts_cutoff_provenance == "derived_from_latest_event":
        raise ValueError("facts_cutoff must not be derived from latest_event_date")

    in_period: list[dict[str, Any]] = []
    for e in events:
        d = _d(e.get("occurred_at"))
        if d and ps <= d <= pe:
            in_period.append(e)
    in_period.sort(key=lambda e: (e.get("occurred_at") or "", e.get("event_id") or ""))
    event_dates = sorted({_d(e["occurred_at"]) for e in in_period})
    latest_event_date = max(event_dates).isoformat() if event_dates else None
    covered_event_ids = [e["event_id"] for e in in_period]

    event_source_ids = event_source_ids or []
    linked_sources = {
        row["source_id"]
        for row in event_source_ids
        if row.get("event_id") in set(covered_event_ids)
    }
    covered_source_ids = sorted(linked_sources)

    poll_field_ends = []
    covered_poll_ids = []
    for p in polls:
        fw = json.loads(p.get("fieldwork_json") or "{}")
        d = _d(fw.get("field_end"))
        if d and ps <= d <= pe:
            poll_field_ends.append(d)
            covered_poll_ids.append(p["poll_id"])
    poll_cutoff = max(poll_field_ends).isoformat() if poll_field_ends else None
    covered_poll_ids = sorted(set(covered_poll_ids))

    # facts_cutoff: authoritative research cutoff, never MAX(event_date)
    fc = _d(facts_cutoff)

    rules = acceptance_rules or DEFAULT_RULES
    gaps = _normalize_blocking_gaps(blocking_gaps)
    unreviewed = _unreviewed_range(fc, pe)
    if unreviewed is not None:
        gaps.append(unreviewed)

    # Required research dimensions, when explicitly supplied.
    dimensions = list(dimensions or [])
    if dimensions:
        covered_types = {e.get("event_type") for e in in_period if e.get("event_type")}
        missing_dims = [d for d in dimensions if d not in covered_types]
        for d in missing_dims:
            gaps.append(
                {
                    "kind": "missing_required_dimension",
                    "start": None,
                    "end": None,
                    "reason": f"required dimension without formal evidence: {d}",
                }
            )
        dimension_gaps = missing_dims
    else:
        dimension_gaps = []

    known_kinds = blocking_gap_kinds(rules) | non_blocking_gap_kinds(rules)
    for g in gaps:
        if g.get("kind") not in known_kinds:
            raise ValueError(f"unknown coverage gap kind: {g.get('kind')}")
    blocking = [g for g in gaps if gap_kind_is_blocking(g.get("kind", ""), rules)]
    non_blocking = [g for g in gaps if not gap_kind_is_blocking(g.get("kind", ""), rules)]
    uncovered_ranges = [
        {"start": g["start"], "end": g["end"], "reason": g["kind"]}
        for g in blocking
        if g.get("start") and g.get("end")
    ]
    known_gaps = list(known_gaps or [])

    cutoff_ok = fc is not None and fc >= pe
    status = "partial"
    if (
        cutoff_ok
        and formal_state_hash
        and formal_state_ready
        and not blocking
        and full_requires_facts_cutoff_reaches_period_end(rules)
    ):
        status = "full"

    content_key = hashlib.sha256(
        (
            formal_state_hash
            + "|"
            + COVERAGE_SCHEMA_VERSION
            + "|"
            + configuration_hash
            + "|"
            + rules_hash(rules)
        ).encode("utf-8")
    ).hexdigest()[:8]
    version_date = (fc or pe).isoformat().replace("-", "")
    coverage_version = f"fact_coverage_{version_date}_v{int(content_key, 16) % 1000 + 1:03d}"

    payload = {
        "coverage_schema_version": COVERAGE_SCHEMA_VERSION,
        "coverage_version": coverage_version,
        "election_id": election_id,
        "built_from_formal_state_hash": formal_state_hash,
        "requested_period_start": ps.isoformat(),
        "requested_period_end": pe.isoformat(),
        "facts_cutoff": facts_cutoff,
        "facts_cutoff_provenance": facts_cutoff_provenance,
        "latest_event_date": latest_event_date,
        "poll_cutoff": poll_cutoff,
        "latest_poll_field_end": poll_cutoff,
        "event_count": len(in_period),
        "source_count": int(source_count),
        "poll_count": len(polls),
        "covered_event_ids": covered_event_ids,
        "covered_source_ids": covered_source_ids,
        "covered_poll_ids": covered_poll_ids,
        "covered_event_dates": [d.isoformat() for d in event_dates],
        "uncovered_date_ranges": uncovered_ranges,
        "blocking_gaps": blocking,
        "non_blocking_gaps": non_blocking,
        "dimension_gaps": dimension_gaps,
        "known_gaps": known_gaps,
        "coverage_status": status,
        "no_event_day_is_gap": no_event_day_is_gap(rules),
        "poll_absence_is_blocking": poll_absence_is_blocking(rules),
    }
    business_hash = hashlib.sha256(
        json.dumps(payload, ensure_ascii=False, sort_keys=True).encode("utf-8")
    ).hexdigest()
    manifest = {
        "coverage_version": coverage_version,
        "coverage_schema_version": COVERAGE_SCHEMA_VERSION,
        "election_id": election_id,
        "built_from_formal_state_hash": formal_state_hash,
        "facts_cutoff": facts_cutoff,
        "facts_cutoff_provenance": facts_cutoff_provenance,
        "latest_event_date": latest_event_date,
        "poll_cutoff": poll_cutoff,
        "coverage_start": ps.isoformat(),
        "coverage_end": pe.isoformat(),
        "event_count": len(in_period),
        "source_count": int(source_count),
        "poll_count": len(polls),
        "uncovered_date_ranges": uncovered_ranges,
        "blocking_gap_count": len(blocking),
        "non_blocking_gap_count": len(non_blocking),
        "dimension_gaps": dimension_gaps,
        "business_hash": business_hash,
    }
    return {
        "coverage": payload,
        "manifest": manifest,
        "business_hash": business_hash,
        "coverage_version": coverage_version,
        "formal_state_hash": formal_state_hash,
    }


def build_coverage(
    config,
    *,
    requested_start: str,
    requested_end: str,
    facts_cutoff: str | None = None,
    facts_cutoff_provenance: str = "authoritative_input",
    blocking_gaps: list[Any] | None = None,
    known_gaps: list[str] | None = None,
    dimensions: list[str] | None = None,
) -> dict[str, Any]:
    db = config.path("formal_db")
    formal_hash = formal_state_business_hash_from_db(db)
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        events = [
            dict(r)
            for r in conn.execute(
                "SELECT event_id, occurred_at, event_type, title FROM election_events"
            )
        ]
        source_count = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
        polls = [
            dict(r)
            for r in conn.execute(
                "SELECT poll_id, fieldwork_json, publication_json FROM election_polls"
            )
        ]
        event_source_ids = [
            dict(r) for r in conn.execute("SELECT event_id, source_id FROM event_sources")
        ]
        poll_source_ids = [
            dict(r) for r in conn.execute("SELECT poll_id, source_id FROM poll_source_links")
        ]
    finally:
        conn.close()
    rules = load_acceptance_rules(config)
    return compute_coverage_payload(
        events=events,
        polls=polls,
        event_source_ids=event_source_ids,
        poll_source_ids=poll_source_ids,
        source_count=source_count,
        requested_start=requested_start,
        requested_end=requested_end,
        facts_cutoff=facts_cutoff,
        facts_cutoff_provenance=facts_cutoff_provenance,
        blocking_gaps=blocking_gaps,
        known_gaps=known_gaps,
        dimensions=dimensions,
        formal_state_hash=formal_hash,
        configuration_hash=_config_hash(config),
        election_id=config.canonical_election_id,
        acceptance_rules=rules,
    )


def write_coverage(result: dict[str, Any], out_dir: str | Path) -> Path:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    version = result["coverage_version"]
    (out / f"{version}.json").write_text(
        json.dumps(result["coverage"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (out / "coverage_manifest.json").write_text(
        json.dumps(result["manifest"], ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return out / "coverage_manifest.json"
