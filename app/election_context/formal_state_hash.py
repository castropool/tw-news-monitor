"""Unified formal state business hash (seed and database share semantics)."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import yaml


def _norm(v: Any) -> Any:
    if isinstance(v, str):
        try:
            return _norm(json.loads(v))
        except (json.JSONDecodeError, TypeError):
            return v
    if isinstance(v, dict):
        return {k: _norm(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [_norm(x) for x in v]
    return v


def _digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def _event_payloads(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append(
            {
                "event_id": r.get("event_id"),
                "election_id": r.get("election_id"),
                "occurred_at": r.get("occurred_at"),
                "event_type": r.get("event_type"),
                "title": r.get("title"),
                "fact_summary": r.get("fact_summary"),
                "fact_status": r.get("fact_status") or "pending_verification",
                "significance_score": int(r.get("significance_score") or 50),
                "actors": _norm(r.get("actors_json") or r.get("actors") or []),
                "issues": _norm(r.get("issues_json") or r.get("issues") or []),
                "affected_dimensions": _norm(r.get("affected_dimensions_json") or r.get("affected_dimensions") or []),
                "analysis": _norm(r.get("analysis_json") or {}),
            }
        )
    return out


def _source_payloads(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append(
            {
                "source_id": r.get("source_id"),
                "publisher": r.get("publisher"),
                "title": r.get("title"),
                "url": r.get("url"),
                "published_at": r.get("published_at"),
                "source_type": r.get("source_type") or "news",
                "evidence_level": r.get("evidence_level") or "normal",
                "content_hash": r.get("content_hash", ""),
                "raw_text": r.get("raw_text", ""),
            }
        )
    return out


def _poll_payloads(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append(
            {
                "poll_id": r.get("poll_id"),
                "election_id": r.get("election_id"),
                "poll_type": r.get("poll_type"),
                "fact_status": r.get("fact_status"),
                "methodology_complete": r.get("methodology_complete"),
                "verification_tier": r.get("verification_tier"),
                "recommended_disposition": r.get("recommended_disposition"),
                "canonical_origin": r.get("canonical_origin"),
                "publication": _norm(r.get("publication_json") or "{}"),
                "fieldwork": _norm(r.get("fieldwork_json") or "{}"),
                "methodology": _norm(r.get("methodology_json") or "{}"),
                "population": _norm(r.get("population_json") or "{}"),
                "limitations": _norm(r.get("limitations_json") or "[]"),
                "usable_for_poll_trend": r.get("usable_for_poll_trend"),
            }
        )
    return out


def _snapshot_payloads(rows: list[dict]) -> list[dict]:
    out = []
    for r in rows:
        out.append(
            {
                "snapshot_id": r.get("snapshot_id"),
                "election_id": r.get("election_id"),
                "as_of": r.get("as_of"),
                "status": r.get("snapshot_status"),
                "superseded_by": r.get("superseded_by"),
                "superseded_at": r.get("superseded_at"),
                "state": _norm(r.get("state_json") or r.get("state") or {}),
                "supporting_event_ids": _norm(r.get("supporting_event_ids_json") or r.get("supporting_event_ids") or []),
            }
        )
    return out


def formal_state_business_hash_from_seed_dir(seed: str | Path) -> str:
    seed = Path(seed)
    events = _read_jsonl(seed / "events.jsonl")
    sources = _read_jsonl(seed / "sources.jsonl")
    polls = _read_jsonl(seed / "polls.jsonl")
    poll_questions = _read_jsonl(seed / "poll_questions.jsonl")
    poll_results = _read_jsonl(seed / "poll_results.jsonl")
    poll_sources = _read_jsonl(seed / "poll_sources.jsonl")
    poll_source_links = _read_jsonl(seed / "poll_source_links.jsonl")
    history = _read_jsonl(seed / "snapshot_history.jsonl")
    active = json.loads((seed / "initial_snapshot.json").read_text(encoding="utf-8")) if (seed / "initial_snapshot.json").exists() else {}
    election = json.loads((seed / "election.json").read_text(encoding="utf-8")) if (seed / "election.json").exists() else {}
    actors = yaml.safe_load((seed / "actors.yaml").read_text(encoding="utf-8")) if (seed / "actors.yaml").exists() else {}

    event_sources = [
        {"event_id": e["event_id"], "source_id": s.get("source_id"), "is_primary": int(bool(s.get("is_primary")))}
        for e in events for s in e.get("sources", [])
    ]
    snapshots = _snapshot_payloads(history + [active])
    payload = {
        "election": election,
        "actors": sorted(actors.get("actors", []), key=lambda a: a.get("actor_id", "")),
        "sources": sorted(_source_payloads(sources), key=lambda s: s["source_id"]),
        "events": sorted(_event_payloads(events), key=lambda e: e["event_id"]),
        "event_sources": sorted(event_sources, key=lambda x: (x["event_id"], x["source_id"])),
        "polls": sorted(_poll_payloads(polls), key=lambda p: p["poll_id"]),
        "poll_questions": sorted(
            [{k: _norm(v) for k, v in q.items()} for q in poll_questions],
            key=lambda q: (q.get("poll_id", ""), q.get("question_id", "")),
        ),
        "poll_results": sorted(
            [{k: _norm(v) for k, v in r.items()} for r in poll_results],
            key=lambda r: (r.get("poll_id", ""), r.get("question_id", ""), r.get("option_id", "")),
        ),
        "poll_sources": sorted(_source_payloads(poll_sources), key=lambda s: s["source_id"]),
        "poll_source_links": sorted(poll_source_links, key=lambda x: (x.get("poll_id", ""), x.get("source_id", ""))),
        "snapshots": sorted(snapshots, key=lambda s: (s.get("as_of", ""), s.get("snapshot_id", ""))),
    }
    return _digest(payload)


def formal_state_business_hash_from_seed(config) -> str:
    return formal_state_business_hash_from_seed_dir(config.path("events_seed").parent)


def formal_state_business_hash_from_db(db_path: str | Path) -> str:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        payload = {
            "election": dict(conn.execute("SELECT * FROM elections ORDER BY election_id").fetchone() or {}),
            "actors": sorted(
                [
                    {
                        "actor_id": r["actor_id"],
                        "canonical_name": r["canonical_name"],
                        "actor_type": r["actor_type"],
                        "party": r["party"] if "party" in r.keys() else "",
                        "aliases": _norm(r["aliases_json"] if "aliases_json" in r.keys() else "[]"),
                    }
                    for r in conn.execute("SELECT * FROM actors ORDER BY actor_id")
                ],
                key=lambda a: a["actor_id"],
            ),
            "sources": sorted(_source_payloads([dict(r) for r in conn.execute("SELECT * FROM sources")]), key=lambda s: s["source_id"]),
            "events": sorted(_event_payloads([dict(r) for r in conn.execute("SELECT * FROM election_events")]), key=lambda e: e["event_id"]),
            "event_sources": sorted(
                [dict(r) for r in conn.execute("SELECT event_id, source_id, is_primary FROM event_sources")],
                key=lambda x: (x["event_id"], x["source_id"]),
            ),
            "polls": sorted(_poll_payloads([dict(r) for r in conn.execute("SELECT * FROM election_polls")]), key=lambda p: p["poll_id"]),
            "poll_questions": sorted(
                [{k: _norm(v) for k, v in dict(r).items()} for r in conn.execute("SELECT * FROM poll_questions")],
                key=lambda q: (q["poll_id"], q["question_id"]),
            ),
            "poll_results": sorted(
                [{k: _norm(v) for k, v in dict(r).items()} for r in conn.execute("SELECT * FROM poll_results")],
                key=lambda r: (r["poll_id"], r["question_id"], r["option_id"]),
            ),
            "poll_sources": sorted(
                _source_payloads(
                    [
                        dict(r)
                        for r in conn.execute(
                            "SELECT s.* FROM sources s JOIN poll_source_links p ON p.source_id=s.source_id"
                        )
                    ]
                ),
                key=lambda s: s["source_id"],
            ),
            "poll_source_links": sorted(
                [dict(r) for r in conn.execute("SELECT poll_id, source_id FROM poll_source_links")],
                key=lambda x: (x["poll_id"], x["source_id"]),
            ),
            "snapshots": sorted(
                _snapshot_payloads([dict(r) for r in conn.execute("SELECT * FROM election_state_snapshots")]),
                key=lambda s: (s.get("as_of", ""), s.get("snapshot_id", "")),
            ),
        }
        return _digest(payload)
    finally:
        conn.close()
