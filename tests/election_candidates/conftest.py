from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from app.election_candidates.config import load_config
from app.election_candidates.candidate_models import MatchInfo, NormalizedArticle


FIXTURE = Path(__file__).resolve().parent.parent / "fixtures" / "election_candidates"


def load_golden_cases() -> list[dict]:
    return json.loads((FIXTURE / "golden_candidate_cases.json").read_text(encoding="utf-8"))


def article_from_fixture(item: dict) -> NormalizedArticle:
    match_raw = item.get("match") or {}
    match = MatchInfo(
        city=match_raw.get("city", "tainan"),
        relevance=match_raw.get("relevance", "low"),
        matched_people=list(match_raw.get("matched_people", [])),
        matched_parties=list(match_raw.get("matched_parties", [])),
        matched_issues=list(match_raw.get("matched_issues", [])),
        matched_terms=list(match_raw.get("matched_terms", [])),
        matched_basis=list(match_raw.get("matched_basis", [])),
        match_rule_id=match_raw.get("match_rule_id", "fixture"),
        region_match=bool(match_raw.get("region_match", False)),
        election_context_match=bool(match_raw.get("election_context_match", False)),
        match_score=float(match_raw.get("match_score", 0.0)),
    )
    return NormalizedArticle(
        news_article_id=str(item["id"]),
        raw_title=item.get("title", ""),
        normalized_title=item.get("title", ""),
        raw_url=item.get("url", ""),
        normalized_url=item.get("url", ""),
        source_name=item.get("source_name", ""),
        normalized_source_name=item.get("source_name", ""),
        normalized_domain="",
        category=item.get("category", "politics"),
        summary=item.get("summary", ""),
        published_at=item.get("published_at", ""),
        collected_at=item.get("published_at", ""),
        match=match,
    )


def make_config(tmp_path: Path, test_mode: bool = True):
    config = load_config(Path("config/election_candidate_pipeline.yaml"))
    paths = {
        "news_db": str(tmp_path / "news.db"),
        "match_db": str(tmp_path / "election_watch.db"),
        "formal_db": str(tmp_path / "election_context.db"),
        "candidate_db": str(tmp_path / "candidate_test.db"),
        "output_root": str(tmp_path / "out"),
        "frozen_release_zip": str(tmp_path / "release.zip"),
    }
    config.raw["paths"].update(paths)
    # keep pipeline run logs inside the isolated tmp tree; the production
    # log path must never be appended by tests
    config.raw["deployment"]["log_path"] = str(tmp_path / "logs" / "candidate_pipeline.jsonl")
    config.raw["test_mode"] = test_mode
    return config


def create_news_db(path: Path, rows: list[dict]):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE articles (id INTEGER PRIMARY KEY, source_id TEXT, source_name TEXT, "
        "category TEXT, title TEXT, url TEXT, published_at TEXT, fetched_at TEXT, "
        "position INTEGER, summary TEXT, summary_source TEXT, summary_attempted_at TEXT)"
    )
    for i, row in enumerate(rows, 1):
        conn.execute(
            "INSERT INTO articles (id, source_id, source_name, category, title, url, "
            "published_at, fetched_at, position, summary) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (
                int(row["id"]),
                row.get("source_id", "test"),
                row.get("source_name", ""),
                row.get("category", "politics"),
                row.get("title", ""),
                row.get("url", ""),
                row.get("published_at", ""),
                row.get("published_at", ""),
                i,
                row.get("summary", ""),
            ),
        )
    conn.commit()
    conn.close()


def create_match_db(path: Path, rows: list[dict]):
    conn = sqlite3.connect(path)
    conn.execute(
        "CREATE TABLE article_matches (article_url TEXT PRIMARY KEY, city TEXT NOT NULL, "
        "relevance TEXT NOT NULL, matched_people TEXT, matched_parties TEXT, "
        "matched_issues TEXT, matched_basis TEXT, processed_at TEXT NOT NULL, "
        "in_fact_base INTEGER DEFAULT 0)"
    )
    for row in rows:
        conn.execute(
            "INSERT OR REPLACE INTO article_matches VALUES (?,?,?,?,?,?,?,?,?)",
            (
                row["article_url"],
                row.get("city", "tainan"),
                row.get("relevance", "medium"),
                ",".join(row.get("matched_people", [])),
                ",".join(row.get("matched_parties", [])),
                ",".join(row.get("matched_issues", [])),
                ",".join(row.get("matched_basis", [])),
                row.get("processed_at", "2026-07-01T00:00:00"),
                0,
            ),
        )
    conn.commit()
    conn.close()


def create_formal_db(path: Path):
    conn = sqlite3.connect(path)
    conn.executescript(
        """
        CREATE TABLE elections (
            election_id TEXT PRIMARY KEY, election_name TEXT, election_date TEXT,
            region TEXT, election_type TEXT, status TEXT
        );
        CREATE TABLE election_events (
            event_id TEXT PRIMARY KEY, election_id TEXT, occurred_at TEXT, event_type TEXT,
            title TEXT, fact_summary TEXT, fact_status TEXT, significance_score INTEGER,
            actors_json TEXT, issues_json TEXT, affected_dimensions_json TEXT,
            analysis_json TEXT, created_at TEXT, updated_at TEXT
        );
        CREATE TABLE sources (
            source_id TEXT PRIMARY KEY, publisher TEXT, title TEXT, url TEXT,
            published_at TEXT, fetched_at TEXT, source_type TEXT, evidence_level TEXT,
            content_hash TEXT, raw_text TEXT, updated_at TEXT
        );
        CREATE TABLE event_sources (
            event_id TEXT NOT NULL, source_id TEXT NOT NULL, is_primary INTEGER DEFAULT 0,
            PRIMARY KEY (event_id, source_id)
        );
        CREATE TABLE election_polls (poll_id TEXT PRIMARY KEY, election_id TEXT);
        CREATE TABLE election_state_snapshots (
            snapshot_id TEXT PRIMARY KEY, election_id TEXT, as_of TEXT, state_json TEXT,
            supporting_event_ids_json TEXT, created_at TEXT, snapshot_status TEXT,
            superseded_by TEXT, superseded_at TEXT
        );
        """
    )
    conn.execute(
        "INSERT INTO elections VALUES ('TW-2026-TNN-MAYOR','2026年台南市市长选举',"
        "'2026-11-26','台南市','mayor','active')"
    )
    conn.execute(
        "INSERT INTO election_events VALUES "
        "('evt_tnn_nom_20260121','TW-2026-TNN-MAYOR','2026-01-21T10:00:00+08:00',"
        "'party_nomination','民进党正式提名陈亭妃','民进党中执会正式提名陈亭妃参选台南市长',"
        "'verified',90,'[\"陳亭妃\"]','[\"提名\"]','[]','{}','2026-08-01T00:00:00','2026-08-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO election_events VALUES "
        "('evt_tnn_rally_20260725','TW-2026-TNN-MAYOR','2026-07-25T21:00:00+08:00',"
        "'campaign_launch','蓝白凯道集会','蓝白阵营号召民众上凯道捍卫食安',"
        "'verified',80,'[]','[\"選舉\"]','[]','{}','2026-08-01T00:00:00','2026-08-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO sources VALUES "
        "('src_cna','中央社','民进党正式提名陈亭妃','https://www.cna.com.tw/news/aipl/202601210001.aspx',"
        "'2026-01-21T10:00:00+08:00',NULL,'news','high','','','2026-08-01T00:00:00')"
    )
    conn.execute(
        "INSERT INTO event_sources VALUES ('evt_tnn_nom_20260121','src_cna',1)"
    )
    conn.commit()
    conn.close()


@pytest.fixture
def golden_cases():
    return load_golden_cases()
