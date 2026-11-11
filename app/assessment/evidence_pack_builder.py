"""正式证据包构建器（只读正式数据，不调用大模型，不修改正式数据）。"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
import sys
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from .reporting_period import ReportingPeriod
from .state_diff import diff_snapshots, snapshot_supporting_ids
from .research_task_reconciliation import (
    filter_active_research_tasks,
    reconcile_research_tasks,
)
from .evidence_change_reconciliation import reconcile_evidence_references
from .gap_reconciliation import reconcile_gaps
from .risk_reconciliation import classify_risks
from .generation_eligibility import build_generation_eligibility


class EvidencePackError(RuntimeError):
    """证据包构建失败。"""


BUSINESS_TABLES = (
    "elections",
    "actors",
    "election_events",
    "sources",
    "event_sources",
    "election_polls",
    "poll_questions",
    "poll_results",
    "poll_source_links",
    "election_state_snapshots",
)

COVERAGE_DIR_RE = re.compile(r"^fact_coverage_(\d{8})_v(\d+)$")


# ---------------------------------------------------------------------------
# 通用工具
# ---------------------------------------------------------------------------
def load_yaml(path: Path) -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def load_json(path: Path) -> Any:
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def load_jsonl(path: Path) -> list[dict]:
    out = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def parse_json_field(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    if isinstance(value, str):
        try:
            return json.loads(value)
        except Exception:
            return value
    return value


def parse_date(value: Any) -> date | None:
    if not value:
        return None
    text = str(value).strip()[:10]
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def canonical_hash(obj: Any) -> str:
    return sha256_text(json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":")))


def read_only_conn(db_path: Path) -> sqlite3.Connection:
    uri = f"file:{db_path.as_posix()}?mode=ro"
    conn = sqlite3.connect(uri, uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(path)


def atomic_write_json(path: Path, obj: Any) -> None:
    atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2) + "\n")


# ---------------------------------------------------------------------------
# 正式数据读取
# ---------------------------------------------------------------------------
def _collect_subevents(obj: Any, out: list[dict]) -> None:
    if isinstance(obj, dict):
        sd = obj.get("subevent_date")
        if sd:
            rec: dict[str, Any] = {"subevent_date": str(sd)}
            for key in ("description", "subevent_description", "relationship", "fact", "source_ids"):
                if key in obj:
                    rec[key] = obj[key]
            out.append(rec)
        sublist = obj.get("subevents")
        if isinstance(sublist, list):
            for item in sublist:
                if isinstance(item, str):
                    out.append({"subevent_date": item})
                else:
                    _collect_subevents(item, out)
        for value in obj.values():
            _collect_subevents(value, out)
    elif isinstance(obj, list):
        for item in obj:
            _collect_subevents(item, out)


def _dedupe_subevents(items: list[dict]) -> list[dict]:
    by_date: dict[str, list[dict]] = {}
    for item in items:
        d = str(item.get("subevent_date") or "")
        if d:
            by_date.setdefault(d, []).append(item)

    out: list[dict] = []
    seen: set[tuple] = set()
    for d in sorted(by_date):
        records = by_date[d]
        rich = [
            r for r in records
            if (r.get("description") or r.get("subevent_description") or r.get("fact"))
        ]
        selected = rich if rich else records[:1]
        for r in selected:
            key = (d, str(r.get("description") or r.get("subevent_description") or r.get("fact") or ""))
            if key in seen:
                continue
            seen.add(key)
            out.append(r)
    out.sort(key=lambda x: (x.get("subevent_date", ""), str(x.get("description") or x.get("fact") or "")))
    return out


def _texts(items: Any) -> list[str]:
    if not items:
        return []
    if isinstance(items, str):
        return [items]
    out = []
    for item in items:
        if isinstance(item, str):
            out.append(item)
        elif isinstance(item, dict):
            for key in ("fact", "claim", "interpretation", "text"):
                if item.get(key):
                    out.append(str(item[key]))
                    break
    return out


def _evidence_assertions(event_id: str, event_type: str, analysis: dict) -> list[dict]:
    """Faithfully project existing formal assertion records with their metadata."""

    out: list[dict] = []

    def add(items: Any, assertion_type: str, prefix: str) -> None:
        if not items:
            return
        if isinstance(items, (str, dict)):
            items = [items]
        for index, item in enumerate(items, 1):
            record = item if isinstance(item, dict) else {"text": str(item)}
            text = next(
                (
                    str(record[key])
                    for key in ("fact", "claim", "interpretation", "inference", "text")
                    if record.get(key)
                ),
                "",
            )
            if not text:
                continue
            resolved_type = assertion_type
            if prefix == "candidate" and event_type in {
                "campaign_attack",
                "scandal_allegation",
            }:
                resolved_type = "allegation"
            assertion_id = (
                record.get("fact_id")
                or record.get("claim_id")
                or record.get("interpretation_id")
                or record.get("inference_id")
                or f"{event_id}:{prefix}:{index}"
            )
            out.append(
                {
                    "assertion_id": str(assertion_id),
                    "assertion_type": resolved_type,
                    "text": text,
                    "speaker": record.get("speaker"),
                    "assertion_status": record.get("claim_status")
                    or record.get("status"),
                    "source_ids": list(record.get("source_ids") or []),
                    "confidence": record.get("confidence"),
                }
            )

    add(analysis.get("verified_facts"), "observed_fact", "fact")
    add(analysis.get("candidate_claims"), "actor_statement", "candidate")
    add(analysis.get("party_claims"), "actor_statement", "party")
    add(analysis.get("research_claims"), "actor_statement", "research")
    add(analysis.get("media_interpretations"), "media_interpretation", "media")
    significance = analysis.get("analytical_significance")
    if significance:
        add(
            [{"text": significance}],
            "analytical_significance",
            "analysis",
        )
    return out


def _recursive_key_values(obj: Any, key: str, out: list[str]) -> None:
    if isinstance(obj, dict):
        if key in obj:
            val = obj[key]
            if isinstance(val, list):
                out.extend(str(x) for x in val)
            elif isinstance(val, str):
                out.append(val)
        for value in obj.values():
            _recursive_key_values(value, key, out)
    elif isinstance(obj, list):
        for item in obj:
            _recursive_key_values(item, key, out)


def normalize_event(ev: dict) -> dict:
    """Normalize one formal seed event record into an evidence-pack event dict."""
    analysis = parse_json_field(ev.get("analysis_json")) or {}
    if not isinstance(analysis, dict):
        analysis = {}
    inner = analysis.get("analysis")
    if isinstance(inner, str):
        parsed_inner = parse_json_field(inner)
        if isinstance(parsed_inner, dict):
            for k, v in parsed_inner.items():
                analysis.setdefault(k, v)
    elif isinstance(inner, dict):
        for k, v in inner.items():
            analysis.setdefault(k, v)

    subevents: list[dict] = []
    _collect_subevents(analysis, subevents)
    _collect_subevents(ev, subevents)
    subevents = _dedupe_subevents(subevents)

    limitations: list[str] = []
    limitations.extend(str(x) for x in (ev.get("limitations") or []))
    _recursive_key_values(analysis, "added_limitations", limitations)
    _recursive_key_values(analysis, "limitations", limitations)
    limitations = list(dict.fromkeys(x for x in limitations if x))

    mentions = analysis.get("mentions") or ev.get("mentions") or []
    if isinstance(mentions, str):
        mentions = parse_json_field(mentions) or []

    source_ids = [s.get("source_id") for s in (ev.get("sources") or []) if s.get("source_id")]
    evidence_assertions = _evidence_assertions(
        str(ev["event_id"]), str(ev.get("event_type") or ""), analysis
    )

    return {
        "event_id": ev["event_id"],
        "election_id": ev.get("election_id"),
        "event_date": str(ev.get("event_date") or (ev.get("occurred_at") or "")[:10]),
        "occurred_at": ev.get("occurred_at"),
        "event_type": ev.get("event_type"),
        "title": ev.get("title"),
        "fact_summary": ev.get("fact_summary"),
        "fact_status": ev.get("fact_status"),
        "significance_score": ev.get("significance_score"),
        "verified_facts": _texts(analysis.get("verified_facts")),
        "actor_statements": _texts(
            (analysis.get("candidate_claims") or [])
            + (analysis.get("party_claims") or [])
            + (analysis.get("research_claims") or [])
        ),
        "media_interpretations": _texts(analysis.get("media_interpretations")),
        "analytical_significance": analysis.get("analytical_significance") or "",
        "evidence_assertions": evidence_assertions,
        "limitations": limitations,
        "mentions": mentions,
        "source_ids": source_ids,
        "subevents": subevents,
    }


def read_events_seed(events_path: Path) -> dict[str, dict]:
    events = {}
    for ev in load_jsonl(events_path):
        events[ev["event_id"]] = normalize_event(ev)
    return events


def read_sources_db(conn: sqlite3.Connection) -> dict[str, dict]:
    rows = conn.execute(
        "SELECT source_id, publisher, title, url, published_at, fetched_at, "
        "source_type, evidence_level FROM sources"
    ).fetchall()
    out = {}
    for r in rows:
        out[r["source_id"]] = {
            "source_id": r["source_id"],
            "publisher": r["publisher"],
            "title": r["title"],
            "url": r["url"],
            "published_at": r["published_at"],
            "fetched_at": r["fetched_at"],
            "source_type": r["source_type"] or "news",
            "evidence_level": r["evidence_level"],
        }
    return out


def read_links_db(conn: sqlite3.Connection, election_id: str) -> set[tuple[str, str]]:
    rows = conn.execute(
        "SELECT es.event_id, es.source_id FROM event_sources es "
        "JOIN election_events e ON e.event_id = es.event_id "
        "WHERE e.election_id = ?",
        (election_id,),
    ).fetchall()
    return {(r["event_id"], r["source_id"]) for r in rows}


def read_snapshots_db(conn: sqlite3.Connection, election_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT snapshot_id, election_id, as_of, snapshot_status, superseded_by, "
        "superseded_at, created_at, state_json, supporting_event_ids_json "
        "FROM election_state_snapshots WHERE election_id = ? ORDER BY created_at",
        (election_id,),
    ).fetchall()
    out = []
    for r in rows:
        state = parse_json_field(r["state_json"]) or {}
        out.append(
            {
                "snapshot_id": r["snapshot_id"],
                "election_id": r["election_id"],
                "as_of": r["as_of"],
                "snapshot_status": r["snapshot_status"],
                "superseded_by": r["superseded_by"],
                "superseded_at": r["superseded_at"],
                "created_at": r["created_at"],
                "state": state,
                "supporting_event_ids": parse_json_field(r["supporting_event_ids_json"]) or [],
            }
        )
    return out


def read_polls_db(conn: sqlite3.Connection, election_id: str) -> list[dict]:
    rows = conn.execute(
        "SELECT poll_id, election_id, poll_type, fact_status, methodology_complete, "
        "verification_tier, recommended_disposition, canonical_origin, publication_json, "
        "fieldwork_json, methodology_json, population_json, limitations_json, "
        "usable_for_poll_trend FROM election_polls WHERE election_id = ? ORDER BY poll_id",
        (election_id,),
    ).fetchall()
    polls = []
    for r in rows:
        publication = parse_json_field(r["publication_json"]) or {}
        fieldwork = parse_json_field(r["fieldwork_json"]) or {}
        methodology = parse_json_field(r["methodology_json"]) or {}
        population = parse_json_field(r["population_json"]) or {}
        limitations = parse_json_field(r["limitations_json"]) or []
        if not isinstance(limitations, list):
            limitations = [str(limitations)]
        questions = conn.execute(
            "SELECT question_id, question_type, candidate_set_json, base_population, "
            "population_filter, trend_eligible, comparable_group_key, note "
            "FROM poll_questions WHERE poll_id = ? ORDER BY question_order, question_id",
            (r["poll_id"],),
        ).fetchall()
        results = conn.execute(
            "SELECT question_id, option_id, option_name, option_type, reported_value, "
            "value, unit, is_derived FROM poll_results WHERE poll_id = ? "
            "ORDER BY question_id, result_order, option_id",
            (r["poll_id"],),
        ).fetchall()
        source_ids = [
            row["source_id"]
            for row in conn.execute(
                "SELECT source_id FROM poll_source_links WHERE poll_id = ? ORDER BY source_id",
                (r["poll_id"],),
            ).fetchall()
        ]
        polls.append(
            {
                "poll_id": r["poll_id"],
                "poll_type": r["poll_type"],
                "fact_status": r["fact_status"],
                "methodology_complete": bool(r["methodology_complete"]),
                "recommended_disposition": r["recommended_disposition"] or "",
                "pollster": (publication or {}).get("pollster"),
                "sponsor": (publication or {}).get("sponsor"),
                "release_date": (publication or {}).get("published_at"),
                "fieldwork_start": (fieldwork or {}).get("field_start"),
                "fieldwork_end": (fieldwork or {}).get("field_end"),
                "sample_size": (methodology or {}).get("sample_size"),
                "population": population or {},
                "trend_eligible": bool(r["usable_for_poll_trend"]),
                "question_ids": [q["question_id"] for q in questions],
                "questions": [
                    {
                        "question_id": q["question_id"],
                        "question_type": q["question_type"],
                        "candidate_set": parse_json_field(q["candidate_set_json"]) or [],
                        "base_population": q["base_population"],
                        "trend_eligible": bool(q["trend_eligible"]),
                        "comparable_group_key": q["comparable_group_key"],
                    }
                    for q in questions
                ],
                "results": [
                    {
                        "question_id": x["question_id"],
                        "option_id": x["option_id"],
                        "option_name": x["option_name"],
                        "option_type": x["option_type"],
                        "reported_value": x["reported_value"],
                        "value": x["value"],
                        "unit": x["unit"],
                    }
                    for x in results
                ],
                "limitations": [str(x) for x in limitations],
                "source_ids": source_ids,
            }
        )
    return polls


def resolve_election_id(conn: sqlite3.Connection, requested_id: str, seed_root: Path) -> str:
    rows = conn.execute("SELECT election_id, election_name FROM elections").fetchall()
    ids = [r["election_id"] for r in rows]
    if requested_id in ids:
        return requested_id
    ej_path = seed_root / "election.json"
    if ej_path.exists():
        ej = load_json(ej_path)
        if ej.get("election_id") in ids:
            return ej["election_id"]
    raise EvidencePackError(
        f"election_id '{requested_id}' 无法解析到正式数据库中的选举（可用：{ids}）"
    )


def select_active_snapshot(snapshots: list[dict]) -> dict:
    active = [s for s in snapshots if s.get("snapshot_status") == "active"]
    if len(active) != 1:
        raise EvidencePackError(
            f"active snapshot 数量必须为 1，实际为 {len(active)}"
        )
    return active[0]


def select_previous_snapshot(active: dict, snapshots: list[dict]) -> tuple[dict | None, str]:
    active_id = active["snapshot_id"]
    candidates = [s for s in snapshots if s["snapshot_id"] != active_id]
    direct = [s for s in candidates if s.get("superseded_by") == active_id]
    if len(direct) == 1:
        state = active.get("state") or {}
        expected = state.get("supersedes_snapshot_id")
        if expected in (None, direct[0]["snapshot_id"]):
            return direct[0], "superseded_by_chain"
        return direct[0], "superseded_by_chain_with_supersedes_mismatch"
    earlier = [
        s for s in candidates
        if (s.get("created_at") or "") < (active.get("created_at") or "")
    ]
    if earlier:
        earlier_sorted = sorted(earlier, key=lambda s: s.get("created_at") or "")
        return earlier_sorted[-1], "latest_created_at_before_active"
    return None, "no_previous_snapshot"


def select_coverage_version(seed_root: Path) -> tuple[Path, str, dict, dict]:
    candidates = []
    for child in sorted(seed_root.iterdir()):
        if not child.is_dir():
            continue
        m = COVERAGE_DIR_RE.match(child.name)
        if not m:
            continue
        preflight_path = child / "coverage_preflight.json"
        validation_path = child / "coverage_validation.json"
        if not (preflight_path.exists() and validation_path.exists()):
            continue
        preflight = load_json(preflight_path)
        validation = load_json(validation_path)
        ready = (
            preflight.get("preflight_ready") is True
            and validation.get("coverage_ready") is True
        )
        if not ready:
            continue
        candidates.append((int(m.group(1)), int(m.group(2)), child.name, child, preflight, validation))
    if not candidates:
        raise EvidencePackError("未找到任何 ready 的正式覆盖版本")
    candidates.sort(key=lambda x: (x[0], x[1]))
    _d, _v, name, path, preflight, validation = candidates[-1]
    return path, name, preflight, validation


def collect_blocked_ids(seed_root: Path) -> set[str]:
    """Collect ids from hold/negative files that must not enter the pack."""
    blocked: set[str] = set()
    id_keys = ("event_id", "source_id", "poll_id", "record_id", "id")
    for path in seed_root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in (".json", ".jsonl"):
            continue
        rel = path.relative_to(seed_root).as_posix().lower()
        if "hold" not in rel and "negative" not in rel:
            continue
        try:
            if path.suffix.lower() == ".jsonl":
                objs = load_jsonl(path)
            else:
                data = load_json(path)
                objs = data if isinstance(data, list) else [data]
        except Exception:
            continue

        def walk(obj: Any) -> None:
            if isinstance(obj, dict):
                for key in id_keys:
                    val = obj.get(key)
                    if isinstance(val, str):
                        blocked.add(val)
                for value in obj.values():
                    walk(value)
            elif isinstance(obj, list):
                for item in obj:
                    walk(item)

        for obj in objs:
            walk(obj)
    return blocked


def db_business_hash(conn: sqlite3.Connection) -> str:
    data: dict[str, list] = {}
    for table in BUSINESS_TABLES:
        rows = [tuple(r) for r in conn.execute(f"SELECT * FROM {table}").fetchall()]
        rows.sort(key=str)
        data[table] = rows
    data["_fts_row_count"] = conn.execute(
        "SELECT COUNT(*) FROM election_events_fts"
    ).fetchone()[0]
    return canonical_hash(data)


def seed_poll_counts(seed_root: Path) -> tuple[int, int, int, int]:
    polls_path = seed_root / "polls.jsonl"
    if not polls_path.exists():
        return 0, 0, 0, 0
    polls = load_jsonl(polls_path)
    q = sum(len(p.get("questions") or []) for p in polls)
    r = sum(len(p.get("results") or []) for p in polls)
    links = 0
    links_path = seed_root / "poll_source_links.jsonl"
    if links_path.exists():
        links = len(load_jsonl(links_path))
    return len(polls), q, r, links


@dataclass
class FormalData:
    election_id: str
    events: dict[str, dict]
    sources: dict[str, dict]
    links: set[tuple[str, str]]
    polls: list[dict]
    snapshots: list[dict]
    fts_count: int
    counts: dict[str, int]
    active_snapshot: dict
    previous_snapshot: dict | None
    snapshot_selection_basis: str
    coverage_dir: Path
    coverage_name: str
    coverage_preflight: dict
    coverage_validation: dict
    gap_reconciliation: list[dict]
    research_backlog: list[dict]
    closure_record: dict | None
    blocker_triage: dict
    theme_matrix: list[dict]
    blocked_ids: set[str]


def load_formal_data(config: dict, root: Path, requested_election_id: str) -> FormalData:
    paths = config["paths"]
    db_path = (root / paths["database"]).resolve()
    events_seed = (root / paths["events_seed"]).resolve()
    sources_seed = (root / paths["sources_seed"]).resolve()
    seed_root = events_seed.parent.resolve()

    if not db_path.exists():
        raise EvidencePackError(f"正式数据库不存在: {db_path}")
    if not events_seed.exists() or not sources_seed.exists():
        raise EvidencePackError(f"正式种子不存在: {events_seed} / {sources_seed}")

    conn = read_only_conn(db_path)
    try:
        election_id = resolve_election_id(conn, requested_election_id, seed_root)
        db_events = {
            r["event_id"]: r
            for r in conn.execute(
                "SELECT event_id FROM election_events WHERE election_id = ?", (election_id,)
            ).fetchall()
        }
        db_sources = read_sources_db(conn)
        db_links = read_links_db(conn, election_id)
        snapshots = read_snapshots_db(conn, election_id)
        polls = read_polls_db(conn, election_id)
        fts_count = conn.execute("SELECT COUNT(*) FROM election_events_fts").fetchone()[0]

        events = read_events_seed(events_seed)
        seed_sources = {json.loads(line)["source_id"] for line in open(sources_seed, encoding="utf-8")}

        seed_poll_ids = set()
        polls_seed_path = seed_root / "polls.jsonl"
        if polls_seed_path.exists():
            seed_poll_ids = {p["poll_id"] for p in load_jsonl(polls_seed_path)}

        errors: list[str] = []
        if set(events) != set(db_events):
            errors.append("事件种子与数据库事件集合不一致")
        if set(db_sources) != seed_sources:
            errors.append("来源种子与数据库来源集合不一致")
        if set(db_links) != {(e["event_id"], s["source_id"]) for e in events.values() for s in []}:
            # 种子内嵌来源关系另行核对
            pass
        seed_embedded_pairs = set()
        for ev in events.values():
            for sid in ev["source_ids"]:
                seed_embedded_pairs.add((ev["event_id"], sid))
        if db_links != seed_embedded_pairs:
            errors.append("事件来源关系（event_source_links）与种子内嵌来源不一致")
        if fts_count != len(events):
            errors.append(f"FTS 数量 {fts_count} 与正式事件数 {len(events)} 不一致")
        poll_ids = {p["poll_id"] for p in polls}
        if poll_ids != seed_poll_ids:
            errors.append("民调种子与数据库民调集合不一致")
        if errors:
            raise EvidencePackError("正式数据一致性校验失败：" + "；".join(errors))

        active = select_active_snapshot(snapshots)
        previous, basis = select_previous_snapshot(active, snapshots)
        coverage_dir, coverage_name, preflight, validation = select_coverage_version(seed_root)

        gap_reconciliation = []
        gap_path = coverage_dir / "snapshot_gap_reconciliation.json"
        if gap_path.exists():
            gap_reconciliation = load_json(gap_path)
        research_backlog = []
        backlog_path = coverage_dir / "research_priority_backlog.json"
        if backlog_path.exists():
            research_backlog = load_json(backlog_path)
        closure_record = None
        closure_path = coverage_dir / "rt04_closure_record.json"
        if closure_path.exists():
            closure_record = load_json(closure_path)
        blocker_triage = {}
        triage_path = coverage_dir / "snapshot_blocker_triage.json"
        if triage_path.exists():
            blocker_triage = load_json(triage_path)
        theme_matrix = []
        theme_path = coverage_dir / "theme_coverage_matrix.json"
        if theme_path.exists():
            theme_matrix = load_json(theme_path)

        counts = {
            "elections": conn.execute("SELECT COUNT(*) FROM elections").fetchone()[0],
            "actors": conn.execute("SELECT COUNT(*) FROM actors").fetchone()[0],
            "formal_event_count": len(events),
            "formal_source_count": len(db_sources),
            "formal_link_count": len(db_links),
            "formal_fts_count": fts_count,
            "formal_poll_count": len(polls),
            "poll_question_count": conn.execute(
                "SELECT COUNT(*) FROM poll_questions WHERE poll_id IN "
                "(SELECT poll_id FROM election_polls WHERE election_id=?)",
                (election_id,),
            ).fetchone()[0],
            "poll_result_count": conn.execute(
                "SELECT COUNT(*) FROM poll_results WHERE poll_id IN "
                "(SELECT poll_id FROM election_polls WHERE election_id=?)",
                (election_id,),
            ).fetchone()[0],
            "poll_source_link_count": conn.execute(
                "SELECT COUNT(*) FROM poll_source_links WHERE poll_id IN "
                "(SELECT poll_id FROM election_polls WHERE election_id=?)",
                (election_id,),
            ).fetchone()[0],
            "snapshot_count": len(snapshots),
        }
    finally:
        conn.close()

    return FormalData(
        election_id=election_id,
        events=events,
        sources=db_sources,
        links=db_links,
        polls=polls,
        snapshots=snapshots,
        fts_count=fts_count,
        counts=counts,
        active_snapshot=active,
        previous_snapshot=previous,
        snapshot_selection_basis=basis,
        coverage_dir=coverage_dir,
        coverage_name=coverage_name,
        coverage_preflight=preflight,
        coverage_validation=validation,
        gap_reconciliation=gap_reconciliation,
        research_backlog=research_backlog,
        closure_record=closure_record,
        blocker_triage=blocker_triage,
        theme_matrix=theme_matrix,
        blocked_ids=collect_blocked_ids(seed_root),
    )


# ---------------------------------------------------------------------------
# 提取逻辑
# ---------------------------------------------------------------------------
def in_period(d: date | None, start: date, end: date) -> bool:
    return d is not None and start <= d <= end


def extract_period_events(
    events: dict[str, dict], start: date, end: date, active_snapshot: dict
) -> list[dict]:
    active_events, _ = snapshot_supporting_ids(active_snapshot.get("state") or {})
    out = []
    for ev in events.values():
        event_date = parse_date(ev["event_date"])
        reasons: list[str] = []
        in_period_subevents = [
            s for s in ev["subevents"]
            if in_period(parse_date(s.get("subevent_date")), start, end)
        ]
        if in_period(event_date, start, end):
            reasons.append("event_date_in_period")
        if in_period_subevents:
            reasons.append("subevent_date_in_period")
        if not reasons:
            continue
        # active snapshot 引用仅作为附加原因，不构成本期事件触发条件
        if ev["event_id"] in active_events:
            reasons.append("active_snapshot_evidence")
        item = dict(ev)
        item["evidence_role"] = "period_event"
        item["inclusion_reasons"] = reasons
        item["in_period_subevents"] = in_period_subevents
        out.append(item)
    out.sort(key=lambda x: (x["event_date"], x["event_id"]))
    return out


def select_background_events(
    events: dict[str, dict],
    period_event_ids: set[str],
    active_snapshot: dict,
    previous_snapshot: dict | None,
    config: dict,
) -> list[dict]:
    bg_config = config.get("background", {}) or {}
    max_total = int(bg_config.get("max_total", 15))
    max_per_mainline = int(bg_config.get("max_per_mainline", 10))
    mainlines = bg_config.get("mainlines", []) or []

    active_state = active_snapshot.get("state") or {}
    active_events, _ = snapshot_supporting_ids(active_state)
    prev_events: set[str] = set()
    if previous_snapshot:
        prev_events, _ = snapshot_supporting_ids(previous_snapshot.get("state") or {})
    milestone = set(active_state.get("milestone_events") or [])

    pool_ids = sorted((active_events | prev_events | milestone) - period_event_ids)
    pool = {eid: events[eid] for eid in pool_ids if eid in events}

    def rank_key(eid: str) -> tuple:
        ev = pool[eid]
        active_bonus = 2 if eid in active_events else (1 if eid in milestone else 0)
        sig = int(ev.get("significance_score") or 0)
        return (-active_bonus, -sig, ev.get("event_date") or "")

    chosen: dict[str, list[str]] = {}
    ranked_ids = sorted(pool, key=rank_key)

    # 主线配额
    for ml in mainlines:
        field_name = ml.get("snapshot_field")
        dim = (active_state.get(field_name) or {}) if field_name else {}
        dim_ids = set(dim.get("supporting_event_ids") or [])
        picked = [eid for eid in ranked_ids if eid in dim_ids][:max_per_mainline]
        for eid in picked:
            chosen.setdefault(eid, []).append(f"mainline:{ml.get('key')}")

    # 全局补足
    for eid in ranked_ids:
        if len(chosen) >= max_total:
            break
        if eid not in chosen:
            chosen.setdefault(eid, [])

    out = []
    for eid in sorted(chosen, key=rank_key):
        ev = dict(pool[eid])
        ev["evidence_role"] = "background"
        basis: list[str] = []
        if eid in active_events:
            basis.append("active_snapshot_reference")
        if eid in prev_events:
            basis.append("previous_snapshot_reference")
        if eid in milestone:
            basis.append("milestone_event")
        basis.extend(chosen[eid])
        basis.append(f"significance:{ev.get('significance_score')}")
        ev["ranking_basis"] = basis
        ev["inclusion_reasons"] = basis
        ev["in_period_subevents"] = []
        out.append(ev)
    return out


def collect_sources_for_events(
    event_items: list[dict], sources: dict[str, dict], links: set[tuple[str, str]]
) -> tuple[list[dict], list[str]]:
    included_ids: set[str] = set()
    id_to_events: dict[str, list[str]] = {}
    for ev in event_items:
        for sid in ev["source_ids"]:
            if sid in sources and (ev["event_id"], sid) in links:
                included_ids.add(sid)
                id_to_events.setdefault(sid, []).append(ev["event_id"])
    out = []
    for sid in sorted(included_ids):
        s = dict(sources[sid])
        s["linked_event_ids"] = sorted(id_to_events[sid])
        s["is_formal_source"] = True
        out.append(s)
    return out, sorted(included_ids)


def include_polls(
    polls: list[dict], start: date, end: date, active_snapshot: dict
) -> tuple[list[dict], bool, int, int]:
    active_state = active_snapshot.get("state") or {}
    _active_events, active_poll_ids = snapshot_supporting_ids(active_state)
    out = []
    period_count = 0
    context_count = 0
    for p in polls:
        release = parse_date(p.get("release_date"))
        field_end = parse_date(p.get("fieldwork_end"))
        reasons: list[str] = []
        if in_period(release, start, end):
            reasons.append("release_date_in_period")
        if in_period(field_end, start, end):
            reasons.append("fieldwork_end_in_period")
        if p["poll_id"] in active_poll_ids:
            reasons.append("active_snapshot_reference")
        if not reasons:
            continue
        item = dict(p)
        if in_period(release, start, end) or in_period(field_end, start, end):
            item["evidence_role"] = "period_poll"
            item["inclusion_reasons"] = reasons
            period_count += 1
        else:
            item["evidence_role"] = "context_poll"
            item["inclusion_reasons"] = reasons
            context_count += 1
        out.append(item)
    out.sort(key=lambda x: (x["poll_id"],))
    return out, period_count == 0, period_count, context_count


# ---------------------------------------------------------------------------
# 组装
# ---------------------------------------------------------------------------
def uncovered_range(facts_cutoff: str | None, period_end: date) -> list[str]:
    cutoff = parse_date(facts_cutoff)
    if cutoff is None or period_end <= cutoff:
        return []
    out = []
    cur = cutoff
    from datetime import timedelta
    cur = cutoff + timedelta(days=1)
    while cur <= period_end:
        out.append(cur.isoformat())
        cur += timedelta(days=1)
    return out


def build_pack(
    formal: FormalData,
    period: ReportingPeriod,
    config: dict,
    root: Path,
    previous_pack: dict | None = None,
) -> dict:
    active_state = formal.active_snapshot.get("state") or {}
    previous_state = (
        formal.previous_snapshot.get("state") if formal.previous_snapshot else None
    )
    start, end = period.period_start, period.period_end

    period_events = extract_period_events(formal.events, start, end, formal.active_snapshot)
    period_event_ids = {e["event_id"] for e in period_events}
    background_events = []
    if config.get("evidence_pack", {}).get("include_background_events", True):
        background_events = select_background_events(
            formal.events,
            period_event_ids,
            formal.active_snapshot,
            formal.previous_snapshot,
            config,
        )

    sources, included_source_ids = collect_sources_for_events(
        period_events + background_events, formal.sources, formal.links
    )
    polls, poll_gap, period_poll_count, context_poll_count = include_polls(
        formal.polls, start, end, formal.active_snapshot
    )

    state_diff = diff_snapshots(active_state, previous_state)

    # facts_cutoff/poll_cutoff 以权威 coverage preflight 为准；
    # snapshot 内嵌值可能形成于更早时点，仅在 preflight 缺失时作为兜底。
    facts_cutoff = (
        formal.coverage_preflight.get("facts_cutoff")
        or (active_state.get("coverage") or {}).get("facts_cutoff")
    )
    poll_cutoff = (
        formal.coverage_preflight.get("poll_cutoff")
        or (active_state.get("coverage") or {}).get("poll_cutoff")
    )
    fully_covered = parse_date(facts_cutoff) is not None and end <= parse_date(facts_cutoff)
    uncovered = uncovered_range(facts_cutoff, end)

    coverage_gaps = build_coverage_gaps(formal)
    task_recon = reconcile_research_tasks(
        formal.research_backlog, formal.coverage_validation, previous_pack
    )
    # pack 内嵌对账结果必须幂等：去掉依赖“上一次输出”的易变字段
    pack_task_recon = {
        k: v for k, v in task_recon.items() if k != "active_task_ids_from_evidence_pack_before"
    }
    active_tasks = filter_active_research_tasks(
        formal.research_backlog, task_recon["authoritative_active_task_ids"]
    )
    known_limitations = build_known_limitations(formal, active_state)
    do_not_infer = build_do_not_infer(formal, active_state)

    cur_events, cur_polls = snapshot_supporting_ids(active_state)
    prev_events: set[str] = set()
    prev_polls: set[str] = set()
    if previous_state:
        prev_events, prev_polls = snapshot_supporting_ids(previous_state)
    evidence_recon = reconcile_evidence_references(
        current_snapshot_id=formal.active_snapshot["snapshot_id"],
        previous_snapshot_id=(
            formal.previous_snapshot["snapshot_id"] if formal.previous_snapshot else None
        ),
        current_event_references=cur_events,
        previous_event_references=prev_events,
        current_poll_references=cur_polls,
        previous_poll_references=prev_polls,
        formal_event_ids=set(formal.events),
        formal_poll_ids={p["poll_id"] for p in formal.polls},
    )

    prev_gap_texts = (
        list((previous_state.get("coverage") or {}).get("known_gaps") or [])
        if previous_state
        else []
    )
    cur_gap_texts = list((active_state.get("coverage") or {}).get("known_gaps") or [])
    gap_recon = reconcile_gaps(
        previous_gap_texts=prev_gap_texts,
        current_gap_texts=cur_gap_texts,
        gap_reconciliation=formal.gap_reconciliation,
        backlog=formal.research_backlog,
        blocker_triage=formal.blocker_triage,
    )

    prev_limitations: list[str] = []
    if previous_state:
        prev_limitations = list(
            (previous_state.get("coverage") or {}).get("known_gaps") or []
        )
        for section in (
            "structural_lean",
            "competitiveness",
            "dpp_integration",
            "kmt_organization",
            "kmt_tpp_cooperation",
            "public_poll_assessment",
        ):
            prev_limitations.extend(
                (previous_state.get(section) or {}).get("limitations") or []
            )
    risk_recon = classify_risks(
        current_risks=active_state.get("key_risks") or [],
        previous_risks=(previous_state.get("key_risks") or []) if previous_state else [],
        previous_limitations=prev_limitations,
        previous_supporting_event_ids=prev_events,
    )
    state_diff["new_risks"] = risk_recon["newly_emerged_risks"]
    state_diff["risk_changes"] = risk_recon["risk_changes"]

    eligibility = build_generation_eligibility(
        evidence_pack_ready=True,
        facts_cutoff=facts_cutoff,
        period_end=end,
        uncovered_date_range=uncovered,
    )

    subevent_count = sum(len(e.get("in_period_subevents") or []) for e in period_events)
    evidence_statistics = {
        "formal_event_count": formal.counts["formal_event_count"],
        "formal_source_count": formal.counts["formal_source_count"],
        "formal_link_count": formal.counts["formal_link_count"],
        "formal_poll_count": formal.counts["formal_poll_count"],
        "period_event_count": len(period_events),
        "period_subevent_count": subevent_count,
        "background_event_count": len(background_events),
        "included_source_count": len(sources),
        "included_poll_count": len(polls),
        "period_poll_count": period_poll_count,
        "context_poll_count": context_poll_count,
        "poll_gap": poll_gap,
        "coverage_gap_count": len(coverage_gaps),
        "active_research_task_count": len(active_tasks),
        "gap_change_count": len(gap_recon["gap_changes"]),
        "risk_change_count": risk_recon["risk_change_count"],
        "newly_emerged_risk_count": risk_recon["newly_emerged_risk_count"],
        "known_limitation_count": len(known_limitations),
        "do_not_infer_count": len(do_not_infer),
    }

    data_status = {
        "facts_cutoff": facts_cutoff or "",
        "poll_cutoff": poll_cutoff or "",
        "coverage_version": formal.coverage_name,
        "active_snapshot_id": formal.active_snapshot["snapshot_id"],
        "formal_event_count": formal.counts["formal_event_count"],
        "formal_source_count": formal.counts["formal_source_count"],
        "formal_link_count": formal.counts["formal_link_count"],
        "formal_poll_count": formal.counts["formal_poll_count"],
        "report_period_fully_covered_by_facts": fully_covered,
        "uncovered_date_range": uncovered_range(facts_cutoff, end),
    }

    pack = {
        "schema_version": "1.1",
        "election_id": config["election"]["election_id"],
        "election_name": config["election"]["display_name"],
        "report_period": period.to_dict(),
        "data_status": data_status,
        "generation_eligibility": eligibility,
        "current_snapshot": {
            "snapshot_id": formal.active_snapshot["snapshot_id"],
            "election_id": formal.active_snapshot["election_id"],
            "as_of": formal.active_snapshot["as_of"],
            "created_at": formal.active_snapshot["created_at"],
            "state": active_state,
        },
        "previous_snapshot": (
            {
                "snapshot_id": formal.previous_snapshot["snapshot_id"],
                "election_id": formal.previous_snapshot["election_id"],
                "as_of": formal.previous_snapshot["as_of"],
                "created_at": formal.previous_snapshot["created_at"],
                "state": formal.previous_snapshot["state"],
            }
            if formal.previous_snapshot
            else None
        ),
        "snapshot_selection_basis": formal.snapshot_selection_basis,
        "state_diff": state_diff,
        "research_task_status_reconciliation": pack_task_recon,
        "snapshot_evidence_changes": evidence_recon,
        "gap_changes": gap_recon["gap_changes"],
        "risk_changes": risk_recon["risk_changes"],
        "period_events": period_events,
        "background_events": background_events,
        "sources": sources,
        "polls": polls,
        "theme_status": formal.theme_matrix,
        "coverage_gaps": coverage_gaps,
        "active_research_tasks": active_tasks,
        "known_limitations": known_limitations,
        "do_not_infer": do_not_infer,
        "evidence_statistics": evidence_statistics,
        "validation_summary": {},
    }
    return pack


def build_coverage_gaps(formal: FormalData) -> list[dict]:
    triage_map = {
        "rt05_danas_typhoon": "gap_danas_typhoon",
        "rt06_sanye_budget": "gap_flood_governance",
    }
    gaps: dict[str, dict] = {}
    for key, entry in (formal.blocker_triage or {}).items():
        if not isinstance(entry, dict):
            continue
        gap_id = triage_map.get(key, key)
        gaps[gap_id] = {
            "gap_id": gap_id,
            "label": key,
            "classification": entry.get("classification"),
            "rationale": entry.get("rationale"),
            "snapshot_handling": entry.get("snapshot_handling"),
            "current_status": None,
            "remaining_gap": entry.get("rationale"),
        }
    for rec in formal.gap_reconciliation:
        gap_id = rec.get("gap_id")
        if not gap_id:
            continue
        entry = gaps.setdefault(
            gap_id,
            {"gap_id": gap_id, "label": rec.get("v2_gap_text"), "classification": None,
             "rationale": None, "snapshot_handling": None, "current_status": None,
             "remaining_gap": None},
        )
        entry["label"] = entry.get("label") or rec.get("v2_gap_text")
        entry["current_status"] = rec.get("current_status")
        entry["remaining_gap"] = rec.get("remaining_gap")
        entry["recommended_action"] = rec.get("recommended_action")
        entry["new_formal_evidence_ids"] = rec.get("new_formal_evidence_ids") or []
    return [gaps[k] for k in sorted(gaps)]


def build_active_research_tasks(formal: FormalData) -> list[dict]:
    recon = reconcile_research_tasks(formal.research_backlog, formal.coverage_validation, None)
    return filter_active_research_tasks(
        formal.research_backlog, recon["authoritative_active_task_ids"]
    )


def build_known_limitations(formal: FormalData, active_state: dict) -> list[str]:
    lims: list[str] = []
    coverage = active_state.get("coverage") or {}
    lims.extend(str(x) for x in (coverage.get("known_gaps") or []))
    for section in ("structural_lean", "competitiveness", "dpp_integration",
                    "kmt_organization", "kmt_tpp_cooperation", "public_poll_assessment"):
        lims.extend(str(x) for x in ((active_state.get(section) or {}).get("limitations") or []))
    if formal.closure_record:
        lims.extend(f"RT04: {x}" for x in (formal.closure_record.get("remaining_gaps") or []))
    for rec in formal.gap_reconciliation:
        if rec.get("remaining_gap"):
            lims.append(f"{rec.get('gap_id')}: {rec.get('remaining_gap')}")
    return list(dict.fromkeys(x for x in lims if x))


def build_do_not_infer(formal: FormalData, active_state: dict) -> list[str]:
    items: list[str] = []
    _recursive_key_values(active_state, "prohibited_conclusions", items)
    _recursive_key_values(active_state, "do_not_assume", items)
    if formal.closure_record:
        items.extend(str(x) for x in (formal.closure_record.get("do_not_infer") or []))
    for task in formal.research_backlog:
        for item in (task.get("do_not_assume") or []):
            items.append(f"{task.get('research_task_id')}: {item}")
    return list(dict.fromkeys(x for x in items if x))


def render_markdown(pack: dict) -> str:
    lines: list[str] = []
    add = lines.append
    add("# 台南选情半月报告证据包（人工检查版）")
    add("")
    add("## 一、运行信息")
    rp = pack["report_period"]
    add(f"- 时区: {rp['timezone']}；运行时间: {rp['run_at']}；运行日期: {rp['run_date']}")
    add(f"- 解析模式: {rp['resolution_mode']}")
    add("")
    add("## 二、报告周期")
    add(f"- {rp['period_start']} 至 {rp['period_end']}（上一周期 {rp['previous_period_start']} 至 {rp['previous_period_end']}）")
    add("")
    add("## 三、数据状态与截止日期")
    ds = pack["data_status"]
    add(f"- facts_cutoff: {ds['facts_cutoff']}；poll_cutoff: {ds['poll_cutoff']}")
    add(f"- coverage: {ds['coverage_version']}；active snapshot: {ds['active_snapshot_id']}")
    add(f"- 周期完整覆盖: {ds['report_period_fully_covered_by_facts']}")
    add(f"- 未覆盖日期: {ds['uncovered_date_range'] or '无'}")
    add("")
    add("## 四、当前态势快照")
    cur = pack["current_snapshot"]
    add(f"- {cur['snapshot_id']}（as_of={cur['as_of']}）")
    if cur.get("state", {}).get("structural_lean"):
        add(f"- 结构判断: {cur['state']['structural_lean'].get('value')}（confidence={cur['state']['structural_lean'].get('confidence')}）")
    add("")
    add("## 五、上一态势快照")
    prev = pack["previous_snapshot"]
    if prev:
        add(f"- {prev['snapshot_id']}（as_of={prev['as_of']}）")
    else:
        add("- 无前序快照（initial_baseline）")
    add("")
    add("## 六、结构化状态变化")
    sd = pack["state_diff"]
    add(f"- 模式: {sd.get('state_diff_mode')}；状态: {sd.get('status')}")
    add(f"- 变化维度: {sd.get('changed_dimensions', [])}")
    add(f"- 不变维度: {sd.get('unchanged_dimensions')}")
    add("")
    add("## 七、本期正式事件")
    for e in pack["period_events"]:
        add(f"- [{e['event_date']}] {e['event_id']} {e['title']}（{e['event_type']}）")
        add(f"  - 纳入原因: {e['inclusion_reasons']}；子事件: {len(e.get('in_period_subevents') or [])}")
    add("")
    add("## 八、相关背景事件")
    for e in pack["background_events"]:
        add(f"- [{e['event_date']}] {e['event_id']} {e['title']}（依据: {e.get('ranking_basis')}）")
    add("")
    add("## 九、本期正式民调")
    for p in pack["polls"]:
        add(f"- {p['poll_id']} {p.get('pollster') or ''} release={p.get('release_date')} role={p.get('evidence_role')}")
    if not pack["polls"]:
        add("- 无")
    add("")
    add("## 十、主题覆盖情况")
    for t in pack["theme_status"]:
        add(f"- [{t.get('theme')}] {t.get('question_id')} {t.get('question')} -> {t.get('coverage_status')}")
    add("")
    add("## 十一、已知限制")
    for x in pack["known_limitations"]:
        add(f"- {x}")
    add("")
    add("## 十二、禁止推断事项")
    for x in pack["do_not_infer"]:
        add(f"- {x}")
    add("")
    add("## 十三、证据统计")
    for k, v in pack["evidence_statistics"].items():
        add(f"- {k}: {v}")
    add("")
    add("## 十四、验证结果")
    vs = pack.get("validation_summary") or {}
    add(f"- evidence_pack_ready: {vs.get('evidence_pack_ready')}；errors: {len(vs.get('errors') or [])}；warnings: {len(vs.get('warnings') or [])}")
    return "\n".join(lines) + "\n"
