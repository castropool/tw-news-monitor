"""数据库兼容性测试（需求36）：旧 news.db → 升级 → 读写/Word 正常；幂等迁移。"""

import os
import sqlite3
import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from app.database import Database
from app.models import Article

NOW = datetime(2026, 9, 4, 9, 0)


def _old_schema_db(path):
    """构造旧版（Phase I 前）11 列 news.db。"""
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE articles (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            source_id TEXT NOT NULL,
            source_name TEXT NOT NULL,
            category TEXT NOT NULL,
            title TEXT NOT NULL,
            url TEXT NOT NULL UNIQUE,
            published_at TEXT,
            fetched_at TEXT NOT NULL,
            position INTEGER NOT NULL,
            summary TEXT,
            summary_source TEXT,
            summary_attempted_at TEXT
        )
        """
    )
    conn.execute(
        "INSERT INTO articles (source_id, source_name, category, title, url, "
        "published_at, fetched_at, position, summary, summary_source) VALUES "
        "(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("old_src", "旧媒体", "politics", "旧历史新闻标题",
         "https://old.example.com/1", "2026-08-01T10:00:00",
         "2026-08-01T10:05:00", 1, "旧摘要", "rss"),
    )
    conn.execute(
        "INSERT INTO articles (source_id, source_name, category, title, url, "
        "published_at, fetched_at, position) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        ("old_src2", "旧媒体2", "economy", "旧经济新闻",
         "https://old.example.com/2", "2026-08-02T10:00:00",
         "2026-08-02T10:05:00", 1),
    )
    conn.commit()
    conn.close()


class TestOldDbUpgrade:
    def test_upgrade_preserves_and_creates_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "news.db"
            _old_schema_db(db_path)
            db = Database(db_path)
            try:
                db.connect()  # 触发迁移：补 articles 新列 + 建 news_topics
                db.create_tables()
                # 历史新闻保留且可读
                assert db.get_total_count() == 2
                assert db.article_exists("https://old.example.com/1")
                arts = db.get_articles_since(datetime(2000, 1, 1))
                assert len(arts) == 2
                by_url = {a.url: a for a in arts}
                assert by_url["https://old.example.com/1"].title == "旧历史新闻标题"
                assert by_url["https://old.example.com/2"].category == "economy"
                # news_topics 表存在且可写
                db.save_election_topic(
                    "https://old.example.com/1", scope="local", region="台南市",
                    regions=["台南市"], event_type="nomination", confidence=90,
                )
                row = db.get_election_topic("https://old.example.com/1")
                assert row is not None
                assert row["scope"] == "local"
                assert row["region"] == "台南市"
            finally:
                db.close()

    def test_migration_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "news.db"
            _old_schema_db(db_path)
            # 反复 connect/create_tables（模拟重复运行迁移）不得报错
            for _ in range(3):
                db = Database(db_path)
                db.connect()
                db.create_tables()
                db.close()
            db = Database(db_path)
            try:
                db.connect()
                assert db.get_total_count() == 2
                db.save_election_topic(
                    "https://old.example.com/1", scope="local", region="台南市",
                    regions=["台南市"], event_type="nomination", confidence=70,
                )
                db.save_election_topic(
                    "https://old.example.com/2", scope="national", region=None,
                    regions=[], event_type="polling", confidence=70,
                )
                # INSERT OR REPLACE 幂等：重复写同一 url 不报错、不新增行
                db.save_election_topic(
                    "https://old.example.com/2", scope="national", region=None,
                    regions=[], event_type="polling", confidence=80,
                )
                rows = db.get_election_topics_by_urls(
                    ["https://old.example.com/1", "https://old.example.com/2"]
                )
                assert len(rows) == 2
                assert rows["https://old.example.com/2"]["confidence"] == 80
            finally:
                db.close()

    def test_old_article_columns_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = Path(tmp) / "news.db"
            _old_schema_db(db_path)
            db = Database(db_path)
            db.connect()
            db.create_tables()
            conn = sqlite3.connect(str(db_path))
            cols = {
                r[1] for r in conn.execute("PRAGMA table_info(articles)").fetchall()
            }
            conn.close()
            # 升级后新列存在且历史行为 NULL
            for col in ("summary_attempted_at", "section", "language", "access_level"):
                assert col in cols
            # news_topics 列齐全
            conn = sqlite3.connect(str(db_path))
            topic_cols = {
                r[1]
                for r in conn.execute("PRAGMA table_info(news_topics)").fetchall()
            }
            conn.close()
            for col in (
                "url", "topic", "scope", "region", "regions_json",
                "event_type", "confidence", "created_at",
            ):
                assert col in topic_cols
            db.close()


class TestFreshDb:
    def test_fresh_db_has_topics_table(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "fresh.db")
            db.connect()
            db.create_tables()
            db.save_election_topic(
                "https://fresh.com/1", scope="multi_region",
                region="新竹縣", regions=["新竹縣", "新竹市"],
                event_type="party_coordination", confidence=85,
            )
            row = db.get_election_topic("https://fresh.com/1")
            assert row["scope"] == "multi_region"
            assert row["regions"] == ["新竹縣", "新竹市"]
            assert row["topic"] == "election_2026_local"
            db.close()

    def test_get_missing_returns_none(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "fresh.db")
            db.connect()
            db.create_tables()
            assert db.get_election_topic("https://nope.com/x") is None
            assert db.get_election_topics_by_urls(["https://nope.com/x"]) == {}
            db.close()

    def test_save_articles_roundtrip_with_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = Database(Path(tmp) / "fresh.db")
            db.connect()
            db.create_tables()
            art = Article(
                source_id="s", source_name="媒体", category="politics",
                title="陈亭妃宣布投入台南市长选举", url="https://x.com/1",
                published_at=NOW, fetched_at=NOW, position=1,
            )
            inserted = db.save_articles([art])
            assert len(inserted) == 1
            db.save_election_topic(
                art.url, scope="local", region="台南市",
                regions=["台南市"], event_type="nomination", confidence=95,
            )
            # 文章与专题均存在
            assert db.article_exists(art.url)
            assert db.get_election_topic(art.url)["region"] == "台南市"
            db.close()
