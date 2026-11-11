import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.election_report import build_fact_base
from app.election_classifier import ElectionClassifier
from app.election_fact_store import ElectionFactStore
import sqlite3
import tempfile
import json
import os

CONFIG_PATH = Path(__file__).resolve().parent.parent / 'config' / 'election_watch.yaml'

class TestFactBase:
    def test_build_facts_for_tainan(self):
        classifier = ElectionClassifier(CONFIG_PATH)
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('''
            CREATE TABLE articles (
                source_id TEXT, source_name TEXT, category TEXT,
                title TEXT, url TEXT PRIMARY KEY,
                published_at TEXT, fetched_at TEXT
            )
        ''')
        conn.execute(
            "INSERT INTO articles VALUES ('cna_politics','中央社','politics',"
            "'林俊憲宣布參選台南市長 民進黨初選啟動','https://cna.tw/1','2026-07-26','2026-07-26')"
        )
        conn.execute(
            "INSERT INTO articles VALUES ('udn_politics','聯合報','politics',"
            "'台南美食節開跑 在地小吃人潮湧現','https://udn.tw/1','2026-07-26','2026-07-26')"
        )
        facts = build_fact_base(None, conn, classifier, 'tainan', days=365)
        assert len(facts) >= 1
        assert any('林俊憲' in f['actor'] for f in facts)
        conn.close()

    def test_build_facts_for_new_taipei(self):
        classifier = ElectionClassifier(CONFIG_PATH)
        conn = sqlite3.connect(':memory:')
        conn.row_factory = sqlite3.Row
        conn.execute('''
            CREATE TABLE articles (
                source_id TEXT, source_name TEXT, category TEXT,
                title TEXT, url TEXT PRIMARY KEY,
                published_at TEXT, fetched_at TEXT
            )
        ''')
        conn.execute(
            "INSERT INTO articles VALUES ('cna_politics','中央社','politics',"
            "'蘇巧慧表態參選新北市長','https://cna.tw/2','2026-07-26','2026-07-26')"
        )
        facts = build_fact_base(None, conn, classifier, 'new_taipei', days=365)
        assert len(facts) >= 1
        assert any('蘇巧慧' in f['actor'] for f in facts)
        conn.close()
