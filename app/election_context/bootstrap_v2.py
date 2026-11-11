"""Bootstrap v2: full formal state rebuild from authoritative seed.

Extends bootstrap v1 with poll questions/results/source links import so that the
complete election_context.db business state can be reproduced from seed files.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml

from app.election_context.repository import ElectionContextRepository


def _read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def run_bootstrap_v2(seed_dir: str | Path, db_path: str | Path, reset: bool = True):
    seed = Path(seed_dir)
    dbp = Path(db_path)
    if reset and dbp.exists():
        try:
            dbp.unlink()
        except PermissionError:
            pass
    repo = ElectionContextRepository(str(dbp))
    repo.connect()
    repo.create_tables()
    stats: dict = {}

    el_path = seed / "election.json"
    if el_path.exists():
        repo.save_election(json.loads(el_path.read_text(encoding="utf-8")))
        stats["elections"] = 1

    act_path = seed / "actors.yaml"
    if act_path.exists():
        act_data = yaml.safe_load(act_path.read_text(encoding="utf-8"))
        for a in act_data.get("actors", []):
            repo.conn.execute(
                "INSERT OR IGNORE INTO actors (actor_id,canonical_name,actor_type,party,aliases_json) VALUES (?,?,?,?,?)",
                (a["actor_id"], a["canonical_name"], a["actor_type"], a.get("party", ""),
                 json.dumps(a.get("aliases", []), ensure_ascii=False)),
            )
        repo.conn.commit()
        stats["actors"] = len(act_data.get("actors", []))

    sources = _read_jsonl(seed / "sources.jsonl")
    for s in sources:
        repo.save_source(s)
    stats["sources"] = len(sources)

    events = _read_jsonl(seed / "events.jsonl")
    for e in events:
        eid = repo.save_event(e)
        for src in e.get("sources", []):
            sid = repo.save_source(src)
            repo.link_event_source(eid, sid, src.get("is_primary", False))
    stats["events"] = len(events)

    # ---- Polls (Phase 2.5 governance) ----
    poll_sources = _read_jsonl(seed / "poll_sources.jsonl")
    for s in poll_sources:
        repo.save_source(s)
    polls = _read_jsonl(seed / "polls.jsonl")
    for p in polls:
        repo.save_poll(p)
    questions = _read_jsonl(seed / "poll_questions.jsonl")
    for q in questions:
        repo.save_poll_question(q)
    results = _read_jsonl(seed / "poll_results.jsonl")
    for r in results:
        repo.save_poll_result(r)
    links = _read_jsonl(seed / "poll_source_links.jsonl")
    for lk in links:
        repo.link_poll_source(lk["poll_id"], lk["source_id"])
    stats["polls"] = len(polls)
    stats["poll_questions"] = len(questions)
    stats["poll_results"] = len(results)
    stats["poll_source_links"] = len(links)

    # ---- Snapshots ----
    history = _read_jsonl(seed / "snapshot_history.jsonl")
    for raw in history:
        repo.save_snapshot(_snapshot(raw, status="superseded"))
    active_path = seed / "initial_snapshot.json"
    if active_path.exists():
        raw = json.loads(active_path.read_text(encoding="utf-8"))
        repo.save_snapshot(_snapshot(raw, status="active"))
    stats["snapshots"] = len(history) + (1 if active_path.exists() else 0)

    repo.conn.commit()
    repo.rebuild_fts()
    from app.election_context.audit import audit_database

    audit = audit_database(repo)
    stats["audit_ok"] = audit.get("ok", False)
    stats["audit_errors"] = len(audit.get("errors", []))
    ok = audit.get("ok", False)
    repo.close()
    return ok, stats


def _snapshot(raw: dict, status: str) -> dict:
    if "state_json" in raw:
        d = dict(raw)
        if isinstance(d.get("state_json"), str):
            try:
                d["state_json"] = json.loads(d["state_json"])
            except json.JSONDecodeError:
                d["state_json"] = {}
        if isinstance(d.get("supporting_event_ids_json"), str):
            try:
                d["supporting_event_ids"] = json.loads(d["supporting_event_ids_json"])
            except json.JSONDecodeError:
                d["supporting_event_ids"] = []
        d["snapshot_status"] = raw.get("snapshot_status", status)
        return d
    state_keys = ["coverage", "candidate_status", "structural_lean", "competitiveness",
                  "dpp_integration", "kmt_organization", "kmt_tpp_cooperation",
                  "core_issues", "key_risks", "milestone_events",
                  "unresolved_questions", "generated_at"]
    return {
        "snapshot_id": raw.get("snapshot_id", ""),
        "election_id": raw.get("election_id", ""),
        "as_of": raw.get("as_of", ""),
        "state_json": {k: raw.get(k) for k in state_keys if k in raw},
        "supporting_event_ids": raw.get("supporting_event_ids", []),
        "created_at": raw.get("generated_at", ""),
        "snapshot_status": raw.get("snapshot_status", status),
        "superseded_by": raw.get("superseded_by"),
        "superseded_at": raw.get("superseded_at"),
    }


def dry_run_v2(seed_dir: str | Path) -> bool:
    seed = Path(seed_dir)
    required = ["election.json", "actors.yaml", "sources.jsonl", "events.jsonl", "initial_snapshot.json"]
    return all((seed / f).exists() for f in required)
