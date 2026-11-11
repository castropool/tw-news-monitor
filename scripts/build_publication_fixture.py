"""Build the isolated Phase 2 publication fixture (seed + db)."""

from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

FIX = ROOT / "tests" / "fixtures" / "election_publication"
SEED = FIX / "seed"


def write(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def main():
    if SEED.exists():
        shutil.rmtree(SEED)
    SEED.mkdir(parents=True, exist_ok=True)
    write(
        SEED / "election.json",
        json.dumps(
            {
                "election_id": "TW-2026-TNN-MAYOR",
                "election_name": "2026年台南市市长选举",
                "election_date": "2026-11-26",
                "region": "台南市",
                "election_type": "mayor",
                "status": "active",
            },
            ensure_ascii=False,
        ),
    )
    write(SEED / "actors.yaml", "actors: []\n")
    sources = [
        {
            "source_id": "src_fix_cna",
            "publisher": "中央社",
            "title": "中央社新闻",
            "url": "https://www.cna.com.tw/news/aipl/fix.aspx",
            "published_at": "2026-01-01T00:00:00+08:00",
            "source_type": "news",
            "evidence_level": "high",
            "content_hash": "",
            "raw_text": "",
        },
        {
            "source_id": "src_fix_ltn",
            "publisher": "自由時報",
            "title": "自由时报新闻",
            "url": "https://news.ltn.com.tw/news/politics/fix",
            "published_at": "2026-01-01T00:00:00+08:00",
            "source_type": "news",
            "evidence_level": "high",
            "content_hash": "",
            "raw_text": "",
        },
    ]
    sources_by_id = {s["source_id"]: s for s in sources}
    events = [
        {
            "event_id": "evt_fix_nom_20260121",
            "election_id": "TW-2026-TNN-MAYOR",
            "occurred_at": "2026-01-21T10:00:00+08:00",
            "event_type": "party_nomination",
            "title": "民进党正式提名陈亭妃",
            "fact_summary": "民进党中执会正式提名陈亭妃参选台南市长",
            "fact_status": "verified",
            "significance_score": 90,
            "actors_json": '["陳亭妃"]',
            "issues_json": '["提名"]',
            "affected_dimensions_json": "[]",
            "analysis_json": '{"verified_facts":["正式提名"]}',
            "created_at": "2026-01-21T12:00:00+08:00",
            "updated_at": "2026-01-21T12:00:00+08:00",
            "sources": [{**sources_by_id["src_fix_cna"], "is_primary": True}],
        },
        {
            "event_id": "evt_fix_rally_20260725",
            "election_id": "TW-2026-TNN-MAYOR",
            "occurred_at": "2026-07-25T18:00:00+08:00",
            "event_type": "campaign_event",
            "title": "谢龙介台南市长选举造势晚会",
            "fact_summary": "谢龙介举办造势晚会",
            "fact_status": "verified",
            "significance_score": 80,
            "actors_json": '["謝龍介"]',
            "issues_json": '["造勢"]',
            "affected_dimensions_json": "[]",
            "analysis_json": '{"verified_facts":["造势晚会"]}',
            "created_at": "2026-07-25T20:00:00+08:00",
            "updated_at": "2026-07-25T20:00:00+08:00",
            "sources": [{**sources_by_id["src_fix_cna"], "is_primary": True}],
        },
    ]
    write(
        SEED / "sources.jsonl",
        "".join(json.dumps(s, ensure_ascii=False) + "\n" for s in sources),
    )
    write(
        SEED / "events.jsonl",
        "".join(json.dumps(e, ensure_ascii=False) + "\n" for e in events),
    )
    write(
        SEED / "initial_snapshot.json",
        json.dumps(
            {
                "snapshot_id": "tn_state_fix_v1",
                "election_id": "TW-2026-TNN-MAYOR",
                "as_of": "2026-08-01T00:00:00+08:00",
                "state_json": {
                    "coverage": [],
                    "candidate_status": {},
                    "structural_lean": "unknown",
                    "competitiveness": "unknown",
                    "dpp_integration": "unknown",
                    "kmt_organization": "unknown",
                    "kmt_tpp_cooperation": "unknown",
                    "core_issues": [],
                    "key_risks": [],
                    "milestone_events": [],
                    "unresolved_questions": [],
                    "generated_at": "2026-08-01T00:00:00+08:00",
                },
                "supporting_event_ids": ["evt_fix_nom_20260121"],
                "created_at": "2026-08-01T00:00:00+08:00",
                "snapshot_status": "active",
            },
            ensure_ascii=False,
        ),
    )
    write(SEED / "snapshot_history.jsonl", "")
    write(SEED / "polls.jsonl", "")
    write(SEED / "poll_sources.jsonl", "")
    write(SEED / "poll_source_links.jsonl", "")
    write(SEED / "poll_questions.jsonl", "")
    write(SEED / "poll_results.jsonl", "")
    print(f"fixture seed ready: {SEED}")


if __name__ == "__main__":
    main()
