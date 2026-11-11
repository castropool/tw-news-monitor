import sqlite3
from pathlib import Path
from datetime import datetime
from typing import Any

CREATE_MATCHES = '''
CREATE TABLE IF NOT EXISTS article_matches (
    article_url TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    relevance TEXT NOT NULL,
    matched_people TEXT,
    matched_parties TEXT,
    matched_issues TEXT,
    matched_basis TEXT,
    processed_at TEXT NOT NULL,
    in_fact_base INTEGER DEFAULT 0
)
'''

CREATE_EVENTS = '''
CREATE TABLE IF NOT EXISTS election_events (
    event_id TEXT PRIMARY KEY,
    city TEXT NOT NULL,
    event_date TEXT,
    event_title TEXT NOT NULL,
    actors TEXT,
    parties TEXT,
    action TEXT,
    issue TEXT,
    event_type TEXT,
    election_significance TEXT,
    source_count INTEGER DEFAULT 1,
    confidence TEXT DEFAULT 'medium',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
)
'''

CREATE_EVENT_SOURCES = '''
CREATE TABLE IF NOT EXISTS event_sources (
    event_id TEXT NOT NULL,
    article_url TEXT NOT NULL,
    source_name TEXT,
    title TEXT,
    published_at TEXT,
    url TEXT NOT NULL,
    fact_summary TEXT,
    PRIMARY KEY (event_id, article_url)
)
'''

CREATE_REPORT_RUNS = '''
CREATE TABLE IF NOT EXISTS report_runs (
    report_period TEXT PRIMARY KEY,
    cutoff_time TEXT NOT NULL,
    fact_count INTEGER DEFAULT 0,
    event_count INTEGER DEFAULT 0,
    deepseek_model TEXT,
    api_status TEXT,
    input_tokens INTEGER DEFAULT 0,
    output_tokens INTEGER DEFAULT 0,
    word_path TEXT,
    word_sha256 TEXT,
    feishu_status TEXT DEFAULT 'not_sent',
    generated_at TEXT NOT NULL
)
'''

CREATE_SCAN_STATE = '''
CREATE TABLE IF NOT EXISTS scan_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
)
'''

class ElectionFactStore:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = None

    def connect(self):
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")

    def create_tables(self):
        for ddl in [CREATE_MATCHES, CREATE_EVENTS, CREATE_EVENT_SOURCES, CREATE_REPORT_RUNS, CREATE_SCAN_STATE]:
            self.conn.execute(ddl)
        self.conn.commit()

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def save_match(self, article_url: str, city: str, relevance: str,
                   matched_people: list, matched_parties: list,
                   matched_issues: list, matched_basis: list):
        self.conn.execute('''
            INSERT OR REPLACE INTO article_matches
            (article_url, city, relevance, matched_people, matched_parties,
             matched_issues, matched_basis, processed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', (
            article_url, city, relevance,
            ','.join(matched_people),
            ','.join(matched_parties),
            ','.join(matched_issues),
            ','.join(matched_basis),
            datetime.now().isoformat(),
        ))
        self.conn.commit()

    def get_scan_state(self) -> dict:
        rows = self.conn.execute(
            "SELECT key, value FROM scan_state"
        ).fetchall()
        return dict(rows)

    def set_scan_state(self, key: str, value: str):
        self.conn.execute(
            "INSERT OR REPLACE INTO scan_state (key, value) VALUES (?, ?)",
            (key, value)
        )
        self.conn.commit()

    def get_match_count(self, city: str | None = None) -> int:
        if city:
            return self.conn.execute(
                "SELECT COUNT(*) FROM article_matches WHERE city=?", (city,)
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM article_matches"
        ).fetchone()[0]

    def get_event_count(self, city: str | None = None) -> int:
        if city:
            return self.conn.execute(
                "SELECT COUNT(*) FROM election_events WHERE city=?", (city,)
            ).fetchone()[0]
        return self.conn.execute(
            "SELECT COUNT(*) FROM election_events"
        ).fetchone()[0]

    def get_last_report_period(self) -> str | None:
        row = self.conn.execute(
            "SELECT report_period FROM report_runs ORDER BY generated_at DESC LIMIT 1"
        ).fetchone()
        return row[0] if row else None

    def is_report_generated(self, period: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM report_runs WHERE report_period=?", (period,)
        ).fetchone()
        return row is not None
