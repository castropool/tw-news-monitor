from __future__ import annotations

import sqlite3

from app.election_candidates.news_reader import (
    business_signature,
    open_news_connection,
    read_articles,
    table_columns,
)

from .conftest import create_news_db, make_config


def _rows():
    return [
        {"id": 1, "title": "a", "url": "https://a.com/1", "source_name": "A",
         "category": "politics", "published_at": "2026-07-01T08:00:00", "summary": ""},
        {"id": 2, "title": "b", "url": "https://b.com/1", "source_name": "B",
         "category": "politics", "published_at": "2026-07-15T08:00:00", "summary": ""},
        {"id": 3, "title": "c", "url": "https://c.com/1", "source_name": "C",
         "category": "politics", "published_at": "2026-07-31T08:00:00", "summary": ""},
    ]


def test_news_db_opened_read_only(tmp_path):
    create_news_db(tmp_path / "news.db", _rows())
    conn = open_news_connection(tmp_path / "news.db")
    try:
        conn.execute("DROP TABLE articles")
        raised = False
    except sqlite3.OperationalError:
        raised = True
    finally:
        conn.close()
    assert raised is True


def test_table_columns_detected(tmp_path):
    create_news_db(tmp_path / "news.db", _rows())
    conn = open_news_connection(tmp_path / "news.db")
    cols = table_columns(conn, "articles")
    assert "id" in cols and "title" in cols and "url" in cols
    conn.close()


def test_read_articles_date_and_id_filters(tmp_path):
    create_news_db(tmp_path / "news.db", _rows())
    conn = open_news_connection(tmp_path / "news.db")
    rows = read_articles(
        conn, date_from="2026-07-02", date_to="2026-07-30", id_after=0
    )
    assert [r["id"] for r in rows] == [2]
    conn.close()


def test_business_signature_stable_and_sensitive(tmp_path):
    create_news_db(tmp_path / "news.db", _rows())
    conn = open_news_connection(tmp_path / "news.db")
    sig1 = business_signature(conn)
    sig2 = business_signature(conn)
    assert sig1 == sig2
    conn.close()
    w = sqlite3.connect(tmp_path / "news.db")
    w.execute("UPDATE articles SET title='changed' WHERE id=1")
    w.commit()
    w.close()
    conn2 = open_news_connection(tmp_path / "news.db")
    assert business_signature(conn2) != sig1
    conn2.close()
