"""Phase 2.5 read-only inspection: formal state authority map and governance gaps."""

from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "data" / "election_candidates" / "tainan_2026" / "phase25_inspection"
SEED = ROOT / "data" / "election_seed" / "tainan_2026"
DB = ROOT / "data" / "election_context.db"


def read_jsonl(path: Path) -> list[dict]:
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()] if path.exists() else []


def db_conn():
    c = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    c.row_factory = sqlite3.Row
    return c


def normalize(v):
    if isinstance(v, str):
        try:
            return normalize(json.loads(v))
        except Exception:
            return v
    if isinstance(v, dict):
        return {k: normalize(x) for k, x in sorted(v.items())}
    if isinstance(v, list):
        return [normalize(x) for x in v]
    return v


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    conn = db_conn()
    tables = {}
    for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"):
        cols = [dict(zip(("cid", "name", "type", "notnull", "default", "pk"), r)) for r in conn.execute(f"PRAGMA table_info('{name}')")]
        tables[name] = {"columns": cols, "row_count": conn.execute(f"SELECT COUNT(*) FROM '{name}'").fetchone()[0]}
    (OUT / "database_table_inventory.json").write_text(json.dumps(tables, ensure_ascii=False, indent=2), encoding="utf-8")

    field_inventory = {}
    for t, info in tables.items():
        field_inventory[t] = [
            {"field": c["name"], "type": c["type"], "pk": bool(c["pk"]), "notnull": bool(c["notnull"])}
            for c in info["columns"]
        ]
    (OUT / "database_field_inventory.json").write_text(json.dumps(field_inventory, ensure_ascii=False, indent=2), encoding="utf-8")

    authority = {
        "elections": {"authority": "seed_authoritative", "seed_path": "election.json", "table": "elections", "rebuildable": True, "auto_overwrite_ok": True, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "actors": {"authority": "seed_authoritative", "seed_path": "actors.yaml", "table": "actors", "rebuildable": True, "auto_overwrite_ok": True, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "sources": {"authority": "seed_authoritative", "seed_path": "sources.jsonl", "table": "sources", "rebuildable": True, "auto_overwrite_ok": True, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "events": {"authority": "seed_authoritative", "seed_path": "events.jsonl", "table": "election_events", "rebuildable": True, "auto_overwrite_ok": True, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "event_sources": {"authority": "seed_authoritative", "seed_path": "events.jsonl embedded sources[]", "table": "event_sources", "rebuildable": True, "auto_overwrite_ok": True, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "fts": {"authority": "derived_rebuildable", "seed_path": "events.jsonl", "table": "election_events_fts", "rebuildable": True, "auto_overwrite_ok": True, "in_backup": False, "in_publication_diff": False, "in_business_hash": False},
        "polls": {"authority": "seed_authoritative", "seed_path": "polls.jsonl", "table": "election_polls", "rebuildable": True, "auto_overwrite_ok": True, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "poll_questions": {"authority": "seed_authoritative", "seed_path": "poll_questions.jsonl（治理迁移后）", "table": "poll_questions", "rebuildable": False, "auto_overwrite_ok": True, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "poll_results": {"authority": "seed_authoritative", "seed_path": "poll_results.jsonl（治理迁移后）", "table": "poll_results", "rebuildable": False, "auto_overwrite_ok": True, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "poll_sources": {"authority": "seed_authoritative", "seed_path": "poll_sources.jsonl", "table": "sources（民调关联子集）", "rebuildable": True, "auto_overwrite_ok": True, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "poll_source_links": {"authority": "seed_authoritative", "seed_path": "poll_source_links.jsonl", "table": "poll_source_links", "rebuildable": True, "auto_overwrite_ok": True, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "snapshots": {"authority": "seed_authoritative", "seed_path": "initial_snapshot.json + snapshot_history.jsonl", "table": "election_state_snapshots", "rebuildable": True, "auto_overwrite_ok": True, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "analysis_json": {"authority": "seed_authoritative（A类）/ derived（B类）", "seed_path": "events.jsonl analysis_json", "table": "election_events.analysis_json", "rebuildable": True, "auto_overwrite_ok": True, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "coverage_state": {"authority": "derived_rebuildable（coverage builder）", "seed_path": "（coverage builder 输出）", "table": "election_state_snapshots.state_json.coverage", "rebuildable": False, "auto_overwrite_ok": False, "in_backup": True, "in_publication_diff": True, "in_business_hash": True},
        "system_metadata": {"authority": "runtime_state", "seed_path": "无", "table": "sqlite_sequence / FTS 内部表 / WAL", "rebuildable": True, "auto_overwrite_ok": True, "in_backup": False, "in_publication_diff": False, "in_business_hash": False},
    }
    unknown = [k for k, v in authority.items() if v["authority"] == "unknown"]
    (OUT / "authority_map.json").write_text(json.dumps(authority, ensure_ascii=False, indent=2), encoding="utf-8")

    events_seed = read_jsonl(SEED / "events.jsonl")
    events_db = [dict(r) for r in conn.execute("SELECT event_id, analysis_json FROM election_events")]
    db_analysis = {r["event_id"]: normalize(r["analysis_json"]) for r in events_db}
    seed_analysis = {e["event_id"]: normalize(e.get("analysis_json", {})) for e in events_seed}
    analysis_diff = sorted(k for k in db_analysis if db_analysis[k] != seed_analysis.get(k))
    coverage_matrix = {
        "events": {"seed_count": len(events_seed), "db_count": len(events_db), "covered": len(events_seed) == len(events_db)},
        "polls": {"seed_count": len(read_jsonl(SEED / "polls.jsonl")), "db_count": conn.execute("SELECT COUNT(*) FROM election_polls").fetchone()[0]},
        "poll_questions": {"seed_count": 0, "db_count": conn.execute("SELECT COUNT(*) FROM poll_questions").fetchone()[0]},
        "poll_results": {"seed_count": 0, "db_count": conn.execute("SELECT COUNT(*) FROM poll_results").fetchone()[0]},
        "snapshots": {"seed_count": 1 + len(read_jsonl(SEED / "snapshot_history.jsonl")), "db_count": conn.execute("SELECT COUNT(*) FROM election_state_snapshots").fetchone()[0]},
        "analysis_json_gap_events": analysis_diff,
    }
    (OUT / "seed_coverage_matrix.json").write_text(json.dumps(coverage_matrix, ensure_ascii=False, indent=2), encoding="utf-8")

    unmanaged = [
        {"entity": "poll_questions", "status": "legacy_unmanaged", "gap": "39 条 question 仅存在于 DB，未进入 seed", "blocking": True},
        {"entity": "poll_results", "status": "legacy_unmanaged", "gap": "116 条 result 仅存在于 DB，未进入 seed", "blocking": True},
        {"entity": "events.analysis_json", "status": "legacy_unmanaged", "gap": f"{len(analysis_diff)} 条 event 的 analysis_json 增强未进入 seed", "blocking": True},
        {"entity": "coverage", "status": "derived_rebuildable_未实现", "gap": "coverage builder 未实现，当前 coverage 仅存于 snapshot state_json", "blocking": False},
    ]
    (OUT / "unmanaged_state_report.json").write_text(json.dumps(unmanaged, ensure_ascii=False, indent=2), encoding="utf-8")

    analysis_governance = {
        "classification": {
            "A_formal_fact_semantics": ["verified_facts", "observed_facts", "attributed_statements", "allegations", "mentions", "subevents"],
            "B_deterministically_derived": ["supporting_fact_ids", "inference_id", "analytical_significance"],
            "C_snapshot_assessment": ["media_interpretations", "prohibited_conclusions"],
            "D_legacy_historical_enrichment": ["enrich_rt02_*", "enrich_rt03_*", "enrich_rt04_*", "enrich_012"],
            "E_temporary_cache": [],
        },
        "migration_plan": {
            "A": "合并进 events.jsonl（保持现有字段不变，仅追加 DB 已有增强字段）",
            "B": "随 A 一并种子化（视为已确认事实语义），并在 bootstrap_v2 原样写入",
            "C": "保留于事件 analysis_json（它们本身是已存在的正式事实/研判标注），同时快照业务判断保留在 snapshot seed",
            "D": "列入 legacy_state_inventory，随迁移写入 seed 但标记 migration_status=merged_preserved",
            "E": "无",
        },
        "blocking": True,
    }
    (OUT / "analysis_json_governance.json").write_text(json.dumps(analysis_governance, ensure_ascii=False, indent=2), encoding="utf-8")

    lines = [
        "# Phase 2.5 输入核查",
        "",
        f"- 业务表数量：{sum(1 for t in tables if not t.startswith('election_events_fts') and t not in ('sqlite_sequence',))}",
        f"- unknown authority 数量：{len(unknown)}",
        f"- poll_questions 仅 DB：{coverage_matrix['poll_questions']['db_count']}",
        f"- poll_results 仅 DB：{coverage_matrix['poll_results']['db_count']}",
        f"- analysis_json 增强事件：{len(analysis_diff)}",
        f"- 治理阻断项：{sum(1 for u in unmanaged if u['blocking'])}",
        "",
        "结论：polls/sources/links/snapshots 主记录已种子化；poll_questions/results 与 events.analysis_json 增强需治理迁移；",
        "迁移仅把 DB 已有业务事实写入权威 seed，不修改任何事实语义。",
    ]
    (OUT / "phase25_inspection_summary.md").write_text("\n".join(lines), encoding="utf-8")
    conn.close()
    print("\n".join(lines))


if __name__ == "__main__":
    main()
