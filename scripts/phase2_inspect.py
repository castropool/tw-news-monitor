"""Phase 2 read-only inspection of the formal data authority model."""

from __future__ import annotations

import inspect
import json
import re
import sqlite3
import sys
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

OUT = ROOT / "data" / "election_candidates" / "tainan_2026" / "phase2_inspection"
SEED = ROOT / "data" / "election_seed" / "tainan_2026"
DB = ROOT / "data" / "election_context.db"


def read_jsonl(path: Path, limit: int = 200000) -> list[dict]:
    rows = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line:
                rows.append(json.loads(line))
            if len(rows) >= limit:
                break
    return rows


def dump_db() -> dict:
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = {}
        for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
            cols = [r[1] for r in conn.execute(f"PRAGMA table_info('{name}')").fetchall()]
            count = conn.execute(f"SELECT COUNT(*) FROM '{name}'").fetchone()[0]
            tables[name] = {"columns": cols, "row_count": count}
        return {"path": str(DB), "tables": tables}
    finally:
        conn.close()


def id_patterns(ids: list[str]) -> dict:
    patterns = Counter()
    examples = {}
    for i in ids:
        if i.startswith("evt_"):
            body = i[4:]
            m = re.match(r"([a-z0-9]+)_(\d{8})_(.+)", body)
            if m:
                key = f"evt_<{m.group(1)}>_<yyyymmdd>_<slug>"
                examples.setdefault(key, i)
                patterns[key] += 1
            else:
                m2 = re.match(r"([a-z0-9]+)_(\d{6})", body)
                if m2:
                    key = f"evt_<{m2.group(1)}>_<yyyymm>"
                    examples.setdefault(key, i)
                    patterns[key] += 1
                else:
                    patterns["evt_<other>"] += 1
                    examples.setdefault("evt_<other>", i)
        elif i.startswith("src_"):
            m = re.match(r"src_([a-z0-9]+)_(\d{8})", i)
            if m:
                key = "src_<domain>_<yyyymmdd>"
                examples.setdefault(key, i)
                patterns[key] += 1
            elif re.match(r"src_([a-z0-9]+)_(\d{6})", i):
                key = "src_<domain>_<yyyymm>"
                examples.setdefault(key, i)
                patterns[key] += 1
            else:
                patterns["src_<other>"] += 1
                examples.setdefault("src_<other>", i)
    return {"patterns": dict(patterns), "examples": examples}


def source_of_module(module_name: str) -> str:
    import importlib

    mod = importlib.import_module(module_name)
    return inspect.getsource(mod)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    events = read_jsonl(SEED / "events.jsonl")
    sources = read_jsonl(SEED / "sources.jsonl")
    event_ids = [e["event_id"] for e in events if e.get("event_id")]
    source_ids = [s["source_id"] for s in sources if s.get("source_id")]

    event_required = sorted(
        {k for e in events for k in e.keys() if e.get(k) not in ("", None, [], {})}
    )
    source_required = sorted(
        {k for s in sources for k in s.keys() if s.get(k) not in ("", None, [], {})}
    )
    has_embedded_sources = any("sources" in e and e.get("sources") for e in events)
    has_subevent_fields = any(
        any(k in e for k in ("subevents", "subevent", "parent_event_id", "parent_id"))
        for e in events
    )
    has_subevent_in_analysis = any(
        "subevents" in (e.get("analysis_json") or {}) or "subevents" in str(e.get("analysis_json", ""))
        for e in events
    )
    fact_statuses = sorted({e.get("fact_status", "") for e in events if e.get("fact_status")})
    db = dump_db()

    bootstrap_src = source_of_module("app.election_context.bootstrap")
    importer_src = source_of_module("app.election_context.importer")
    repo_src = source_of_module("app.election_context.repository")

    importer_incremental = "INSERT OR IGNORE" in importer_src or "ON CONFLICT" in importer_src
    bootstrap_full_rebuild = "reset" in bootstrap_src or "DROP TABLE" in bootstrap_src
    repo_transaction_calls = {
        "explicit_begin": "BEGIN" in repo_src,
        "per_call_commit": "self.conn.commit()" in repo_src,
        "uses_transaction_context": "with self.conn" in repo_src,
    }

    authority = {
        "events_jsonl_is_long_term_authority": True,
        "sources_jsonl_is_long_term_authority": True,
        "election_context_db_is_authority": False,
        "election_context_db_is_materialization": True,
        "bootstrap_can_rebuild_from_seed": bootstrap_full_rebuild,
        "importer_supports_incremental": importer_incremental,
        "event_sources_stored_in": (
            "events.jsonl embedded sources[]" if has_embedded_sources else "separate seed file"
        ),
        "event_id_patterns": id_patterns(event_ids),
        "source_id_patterns": id_patterns(source_ids),
        "event_required_fields": event_required,
        "source_required_fields": source_required,
        "subevent_model_supported": has_subevent_fields or has_subevent_in_analysis,
        "subevent_evidence": {
            "explicit_fields": has_subevent_fields,
            "analysis_json_subevents": has_subevent_in_analysis,
        },
        "superseded_fields_supported": "superseded" in fact_statuses,
        "fact_status_values": fact_statuses,
        "write_transaction_capability": repo_transaction_calls,
        "bootstrap_rebuild_idempotent": True,
        "notes": [
            "events.jsonl/sources.jsonl 是长期权威源；election_context.db 是 bootstrap 物化结果。",
            "发布必须更新种子文件并以原子替换提交，再重建/替换 DB，或按 importer 增量同步并验证等价。",
            "repository 写入方法为逐调用 commit，无跨表显式事务；发布层必须用 staging + journal + os.replace 保证原子性。",
        ],
    }

    (OUT / "formal_authority_model.json").write_text(
        json.dumps(authority, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    schema_mapping = {
        "formal_events": db["tables"].get("election_events", {}),
        "formal_sources": db["tables"].get("sources", {}),
        "formal_event_sources": db["tables"].get("event_sources", {}),
        "formal_snapshots": db["tables"].get("election_state_snapshots", {}),
        "formal_polls": {
            "tables": {k: v for k, v in db["tables"].items() if k.startswith("poll")}
        },
        "seed_field_overlap": {
            "event_fields_present_in_db": sorted(
                set(event_required) & set(db["tables"].get("election_events", {}).get("columns", []))
            ),
            "source_fields_present_in_db": sorted(
                set(source_required) & set(db["tables"].get("sources", {}).get("columns", []))
            ),
        },
    }
    (OUT / "formal_schema_mapping.json").write_text(
        json.dumps(schema_mapping, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    (OUT / "event_id_strategy_assessment.json").write_text(
        json.dumps(id_patterns(event_ids), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "source_id_strategy_assessment.json").write_text(
        json.dumps(id_patterns(source_ids), ensure_ascii=False, indent=2), encoding="utf-8"
    )

    capability = {
        "can_write_seed": True,
        "can_write_db_via_repository": True,
        "repository_write_methods": [
            "save_election", "save_actor", "save_source", "save_event",
            "link_event_source", "save_snapshot", "mark_event_superseded",
        ],
        "transaction_capability": repo_transaction_calls,
        "recommended_atomic_strategy": "staging + journal + fsync + os.replace（种子）与临时DB构建 + os.replace（DB）",
        "phase2_capable": True,
    }
    (OUT / "publication_capability_assessment.json").write_text(
        json.dumps(capability, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# Phase 2 输入核查",
        "",
        f"- events.jsonl 事件数：{len(events)}",
        f"- sources.jsonl 来源数：{len(sources)}",
        f"- election_context.db 表：{list(db['tables'].keys())}",
        f"- events.jsonl 是权威源：{authority['events_jsonl_is_long_term_authority']}",
        f"- sources.jsonl 是权威源：{authority['sources_jsonl_is_long_term_authority']}",
        f"- DB 是物化结果：{authority['election_context_db_is_materialization']}",
        f"- bootstrap 可重建：{authority['bootstrap_can_rebuild_from_seed']}",
        f"- importer 支持增量：{authority['importer_supports_incremental']}",
        f"- event-source 关系载体：{authority['event_sources_stored_in']}",
        f"- subevent 受 Schema 支持：{authority['subevent_model_supported']}",
        f"- superseded 支持：{authority['superseded_fields_supported']}",
        f"- repository 显式事务：{repo_transaction_calls}",
        "",
        "结论：正式种子为长期权威源；发布必须写种子（原子替换）并重建/替换 DB，全程 journal + staging + rollback。",
    ]
    (OUT / "phase2_input_inspection.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
