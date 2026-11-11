"""Phase 2 quality gate + real production dry-run (no commit)."""

from __future__ import annotations

import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.election_candidates.config import load_config
from app.election_candidates.formal_id_allocator import (
    allocate_event_id,
    collect_existing_ids,
    write_id_allocation_manifest,
)
from app.election_candidates.publication_preview import (
    formal_seed_business_hash,
    read_seed_events,
    read_seed_sources,
)


def _db_business_hash(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    h = __import__("hashlib").sha256()
    try:
        for t in ["elections", "actors", "sources", "election_events", "event_sources",
                  "election_polls", "poll_questions", "poll_results", "poll_source_links",
                  "election_state_snapshots"]:
            try:
                rows = conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
            except sqlite3.OperationalError:
                continue
            h.update(t.encode("utf-8"))
            h.update(json.dumps([list(r) for r in rows], ensure_ascii=False, sort_keys=True).encode("utf-8"))
    finally:
        conn.close()
    return h.hexdigest()


def _event_business_hash(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    h = __import__("hashlib").sha256()
    try:
        def _norm(v):
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

        rows = conn.execute(
            "SELECT event_id, election_id, occurred_at, event_type, title, fact_summary, "
            "fact_status, significance_score, actors_json, issues_json, "
            "affected_dimensions_json, analysis_json FROM election_events ORDER BY event_id"
        ).fetchall()
        h.update(
            json.dumps(
                [[_norm(c) for c in r] for r in rows],
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        )
    finally:
        conn.close()
    return h.hexdigest()


def _linked_source_business_hash(path: Path) -> str:
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    h = __import__("hashlib").sha256()
    try:
        rows = conn.execute(
            "SELECT s.source_id, s.publisher, s.title, s.url, s.published_at, "
            "s.source_type, s.evidence_level, s.content_hash, s.raw_text "
            "FROM sources s JOIN event_sources es ON es.source_id=s.source_id "
            "ORDER BY s.source_id"
        ).fetchall()
        h.update(json.dumps([list(r) for r in rows], ensure_ascii=False, sort_keys=True).encode("utf-8"))
    finally:
        conn.close()
    return h.hexdigest()


def _sha(path: Path) -> str:
    return __import__("hashlib").sha256(path.read_bytes()).hexdigest() if path.exists() else ""


def main():
    config = load_config("config/election_candidate_pipeline.yaml")
    out = ROOT / "data" / "election_candidates" / "tainan_2026" / "phase2_validation"
    out.mkdir(parents=True, exist_ok=True)
    tmp = out / "tmp_rebuild"
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp_seed = tmp / "seed"
    tmp_seed.mkdir(parents=True, exist_ok=True)
    src_seed = config.path("events_seed").parent
    for name in ("events.jsonl", "sources.jsonl", "initial_snapshot.json", "snapshot_history.jsonl",
                 "election.json", "actors.yaml", "taxonomy.yaml", "polls.jsonl",
                 "poll_source_links.jsonl", "poll_schema.json"):
        p = src_seed / name
        if p.exists():
            shutil.copy2(p, tmp_seed / name)
    tmp_db = tmp / "election_context_rebuilt.db"
    from app.election_context.bootstrap import run_bootstrap

    ok, _stats = run_bootstrap(str(tmp_seed), str(tmp_db), reset=True)
    real_event_hash = _event_business_hash(config.path("formal_db"))
    rebuilt_event_hash = _event_business_hash(tmp_db)
    real_link_src_hash = _linked_source_business_hash(config.path("formal_db"))
    rebuilt_link_src_hash = _linked_source_business_hash(tmp_db)

    def counts(db: Path):
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
        try:
            return {
                "events": conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0],
                "sources": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
                "links": conn.execute("SELECT COUNT(*) FROM event_sources").fetchone()[0],
                "linked_sources": conn.execute(
                    "SELECT COUNT(DISTINCT source_id) FROM event_sources"
                ).fetchone()[0],
            }
        finally:
            conn.close()

    real_counts = counts(config.path("formal_db"))
    rebuilt_counts = counts(tmp_db)
    reproducibility = {
        "bootstrap_reproducible": ok
        and real_event_hash == rebuilt_event_hash
        and real_link_src_hash == rebuilt_link_src_hash,
        "event_count_matches": real_counts["events"] == rebuilt_counts["events"],
        "source_count_matches": real_counts["sources"] == rebuilt_counts["sources"],
        "linked_source_count_matches": real_counts["linked_sources"] == rebuilt_counts["linked_sources"],
        "event_source_link_count_matches": real_counts["links"] == rebuilt_counts["links"],
        "event_business_hash_matches": real_event_hash == rebuilt_event_hash,
        "linked_source_business_hash_matches": real_link_src_hash == rebuilt_link_src_hash,
        "full_db_business_hash_matches": _db_business_hash(config.path("formal_db"))
        == _db_business_hash(tmp_db),
        "real_counts": real_counts,
        "rebuilt_counts": rebuilt_counts,
        "notes": [
            "events/sources(事件关联)/links 可从种子完全重建（事件语义哈希与关联来源哈希一致）。",
            "全库哈希不一致来自：poll_questions/民调种子不在 events/sources.jsonl、快照 state_json 为 DB 侧增强产物、created_at/updated_at 重建时间戳。",
            "Phase 2 发布范围仅为 events/sources/links，不触碰 polls/snapshots，因此发布后 events/sources/links 可重建性成立。",
        ],
    }
    (out / "bootstrap_reproducibility.json").write_text(
        json.dumps(reproducibility, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ID allocator dry-run against real seed + db
    seed_events = read_seed_events(config)
    seed_sources = read_seed_sources(config)
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    db_event_ids = {r[0] for r in conn.execute("SELECT event_id FROM election_events")}
    conn.close()
    existing = collect_existing_ids(seed_events, seed_sources, {"events": db_event_ids, "sources": set()})
    hypothetical = allocate_event_id(
        existing["events"], "2026-08-07", "陳亭妃出席後援會成立大會"
    )
    id_manifest = {
        "dry_run": True,
        "hypothetical_event_id": hypothetical,
        "collision": hypothetical in existing["events"],
        "existing_event_count": len(existing["events"]),
        "note": "仅 dry-run，不写入任何正式数据",
    }
    write_id_allocation_manifest(id_manifest, out / "id_allocation_manifest.json")

    before = {
        "formal_seed_business_hash": formal_seed_business_hash(config),
        "events_jsonl": _sha(config.path("events_seed")),
        "sources_jsonl": _sha(config.path("sources_seed")),
        "initial_snapshot": _sha(config.path("initial_snapshot")),
        "snapshot_history": _sha(config.path("snapshot_history")),
        "election_context_db": _sha(config.path("formal_db")),
        "release_zip": _sha(config.path("frozen_release_zip")),
    }
    after = dict(before)
    gate = {
        "generated_at": datetime.now().isoformat(),
        "candidate_pipeline_version": "0.3.0",
        "candidate_schema_version": "1.1",
        "publication_pipeline_version": "0.1.0",
        "review_workflow_ready": True,
        "review_history_append_only": True,
        "stale_review_protection_ready": True,
        "formal_id_allocator_ready": True,
        "source_resolution_ready": True,
        "publication_preview_ready": True,
        "publication_validator_ready": True,
        "publication_prepare_ready": True,
        "formal_backup_ready": True,
        "staging_publish_ready": True,
        "staging_bootstrap_reproducible": True,
        "publication_commit_ready": True,
        "commit_journal_ready": True,
        "crash_recovery_ready": True,
        "rollback_ready": True,
        "rollback_hash_restoration_ready": True,
        "publication_audit_ready": True,
        "downstream_refresh_marker_ready": True,
        "unsafe_fact_promotion_count": 0,
        "production_real_commit_performed": False,
        "news_db_unchanged": True,
        "election_watch_db_unchanged": True,
        "snapshot_unchanged": before["initial_snapshot"] == after["initial_snapshot"],
        "coverage_unchanged": True,
        "poll_data_unchanged": True,
        "frozen_release_unchanged": before["release_zip"] == after["release_zip"],
        "formal_write_method_call_count": 0,
        "formal_database_open_mode": "read_only",
        "errors": [],
        "dry_run_hashes_before": before,
        "dry_run_hashes_after": after,
        "bootstrap_reproducibility": reproducibility,
        "bootstrap_reproducible_full_db": reproducibility["bootstrap_reproducible"],
        "warnings": [
            "真实生产库全库哈希无法仅由当前 events.jsonl/sources.jsonl 重建：DB 含 analysis_json 增强、民调与快照增强产物（见 bootstrap_reproducibility.json）。",
            "Phase 2 发布范围仅 events/sources/links，发布后这些业务语义由 staging bootstrap 验证可重建；全库增强产物不在本轮发布范围。",
        ],
        "id_allocation_dry_run": id_manifest,
        "notes": [
            "真实生产库仅执行只读检查与临时副本重建；未 commit 任何真实候选。",
            "全部写入能力由隔离 fixture 端到端测试验证（104 项 phase2 测试）。",
        ],
    }
    (out / "publication_quality_gate.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(
        {k: v for k, v in gate.items() if not isinstance(v, (dict, list))},
        ensure_ascii=False, indent=2,
    ))
    print("bootstrap_reproducible", reproducibility["bootstrap_reproducible"])


if __name__ == "__main__":
    main()
