import sqlite3
from datetime import datetime

from app.database import Database


URL = "https://example.com/shared-story"


def _create_legacy_topics_db(path):
    conn = sqlite3.connect(path)
    conn.execute(
        """
        CREATE TABLE news_topics (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT NOT NULL UNIQUE,
            topic TEXT NOT NULL DEFAULT 'election_2026_local',
            scope TEXT,
            region TEXT,
            regions_json TEXT,
            event_type TEXT,
            confidence INTEGER,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.execute(
        """
        INSERT INTO news_topics
            (url, topic, scope, region, regions_json, event_type, confidence, created_at)
        VALUES (?, 'election_2026_local', 'local', '台南市', '["台南市"]',
                'nomination', 92, ?)
        """,
        (URL, datetime(2026, 9, 4, 8, 0).isoformat()),
    )
    conn.commit()
    conn.close()


def test_fresh_database_allows_election_and_military_for_same_url(tmp_path):
    db = Database(tmp_path / "news.db")
    db.connect()
    db.create_tables()
    db.save_election_topic(
        URL,
        scope="local",
        region="台南市",
        regions=["台南市"],
        event_type="nomination",
        confidence=92,
    )
    db.save_military_topic(URL, source_type="commercial_military")

    rows = db.conn.execute(
        "SELECT topic, source_type FROM news_topics WHERE url = ? ORDER BY topic",
        (URL,),
    ).fetchall()
    assert rows == [
        ("election_2026_local", None),
        ("military", "commercial_military"),
    ]
    assert db.get_election_topic(URL)["region"] == "台南市"
    assert db.get_topic_urls([URL], "military") == {URL}
    db.close()


def test_repeated_military_topic_write_is_idempotent(tmp_path):
    db = Database(tmp_path / "news.db")
    db.connect()
    db.create_tables()
    db.save_military_topic(URL, source_type="commercial_military")
    db.save_military_topic(URL, source_type="official_military")

    rows = db.conn.execute(
        "SELECT source_type FROM news_topics WHERE url = ? AND topic = 'military'",
        (URL,),
    ).fetchall()
    assert rows == [("official_military",)]
    db.close()


def test_legacy_url_unique_schema_migrates_without_losing_election_data(tmp_path):
    path = tmp_path / "legacy.db"
    _create_legacy_topics_db(path)

    db = Database(path)
    db.connect()
    db.save_military_topic(URL, source_type="official_military")

    rows = db.conn.execute(
        "SELECT id, topic, region FROM news_topics WHERE url = ? ORDER BY topic",
        (URL,),
    ).fetchall()
    assert rows == [(1, "election_2026_local", "台南市"), (2, "military", None)]
    assert db.get_election_topic(URL)["confidence"] == 92
    db.close()


def test_multi_topic_migration_is_idempotent_across_reconnects(tmp_path):
    path = tmp_path / "legacy.db"
    _create_legacy_topics_db(path)

    for _ in range(3):
        db = Database(path)
        db.connect()
        db.save_military_topic(URL, source_type="commercial_military")
        db.close()

    conn = sqlite3.connect(path)
    rows = conn.execute(
        "SELECT topic, COUNT(*) FROM news_topics GROUP BY topic ORDER BY topic"
    ).fetchall()
    unique_indexes = []
    for index in conn.execute("PRAGMA index_list(news_topics)").fetchall():
        if index[2]:
            columns = tuple(
                row[2]
                for row in conn.execute(f"PRAGMA index_info('{index[1]}')").fetchall()
            )
            unique_indexes.append(columns)
    conn.close()

    assert rows == [("election_2026_local", 1), ("military", 1)]
    assert ("url", "topic") in unique_indexes
    assert ("url",) not in unique_indexes
