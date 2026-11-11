import tempfile
from datetime import datetime
from pathlib import Path

import pytest

from app.database import Database
from app.models import Article


@pytest.fixture
def db():
    """Create a temporary database for testing."""
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp.close()
    db_path = tmp.name
    db = Database(db_path)
    db.connect()
    db.create_tables()
    yield db
    db.close()
    Path(db_path).unlink(missing_ok=True)


def make_article(url: str = "https://example.com/news/1", **kwargs) -> Article:
    now = datetime.now()
    return Article(
        source_id=kwargs.get("source_id", "test"),
        source_name=kwargs.get("source_name", "測試媒體"),
        category=kwargs.get("category", "politics"),
        title=kwargs.get("title", "測試新聞"),
        url=url,
        published_at=kwargs.get("published_at", now),
        fetched_at=kwargs.get("fetched_at", now),
        position=kwargs.get("position", 1),
    )


class TestDatabase:
    def test_create_tables(self, db):
        """1. Table creation succeeds."""
        # Tables should already exist from fixture
        rows = db.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='articles'"
        ).fetchall()
        assert len(rows) == 1

    def test_save_article(self, db):
        """2. Save article succeeds."""
        article = make_article()
        db.save_article(article)
        assert db.count_articles() == 1

    def test_duplicate_url_not_saved(self, db):
        """3. Duplicate URL is not saved again."""
        a1 = make_article(url="https://example.com/news/1")
        db.save_article(a1)
        assert db.count_articles() == 1

        a2 = make_article(url="https://example.com/news/1", title="重複標題")
        db.save_article(a2)
        assert db.count_articles() == 1

    def test_empty_query_returns_empty_list(self, db):
        """4. Empty query returns empty list."""
        articles = db.get_articles_since(datetime(2025, 1, 1))
        assert articles == []
        assert isinstance(articles, list)

    def test_db_file_deletable_after_close(self):
        """5. DB file can be deleted after close."""
        with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
            db_path = tmp.name
        db = Database(db_path)
        db.connect()
        db.create_tables()
        article = make_article()
        db.save_article(article)
        db.close()

        # Should be deletable after close
        Path(db_path).unlink()
        assert not Path(db_path).exists()

    def test_save_articles_bulk(self, db):
        """Save multiple articles and count."""
        articles = [
            make_article(url=f"https://example.com/news/{i}", position=i)
            for i in range(5)
        ]
        db.save_articles(articles)
        assert db.count_articles() == 5

    def test_article_exists(self, db):
        """Check article existence by URL."""
        url = "https://example.com/exists"
        assert not db.article_exists(url)
        db.save_article(make_article(url=url))
        assert db.article_exists(url)

    def test_get_articles_since(self, db):
        """Filter articles by fetched_at."""
        early = datetime(2025, 6, 1)
        late = datetime(2025, 7, 1)

        a1 = make_article(url="https://example.com/early", fetched_at=early)
        a2 = make_article(url="https://example.com/late", fetched_at=late)

        db.save_articles([a1, a2])

        # Should get only late
        result = db.get_articles_since(datetime(2025, 6, 15))
        assert len(result) == 1
        assert result[0].url == "https://example.com/late"

    def test_count_by_category(self, db):
        """Count articles grouped by category."""
        a1 = make_article(url="https://example.com/pol", category="politics")
        a2 = make_article(url="https://example.com/eco", category="economy")
        a3 = make_article(url="https://example.com/eco2", category="economy")
        db.save_articles([a1, a2, a3])

        counts = db.count_by_category()
        assert counts.get("politics") == 1
        assert counts.get("economy") == 2

    def test_empty_count_returns_empty_dict(self, db):
        """count_by_category on empty DB returns empty dict."""
        counts = db.count_by_category()
        assert counts == {}
        assert isinstance(counts, dict)

    def test_empty_count_by_source(self, db):
        """count_by_source on empty DB returns empty dict."""
        counts = db.count_by_source()
        assert counts == {}
        assert isinstance(counts, dict)

    def test_article_with_null_published_at(self, db):
        """Article with None published_at saves and retrieves OK."""
        now = datetime.now()
        article = Article(
            source_id="test",
            source_name="測試媒體",
            category="politics",
            title="無時間新聞",
            url="https://example.com/notime",
            published_at=None,
            fetched_at=now,
            position=1,
        )
        db.save_article(article)

        result = db.get_articles_since(datetime(2025, 1, 1))
        assert len(result) == 1
        assert result[0].published_at is None
        assert result[0].title == "無時間新聞"


# ── 国际媒体监测层 Phase I：section / language / access_level ──────


class TestDatabasePhaseIColumns:
    """Phase I 三列：可空、迁移、save/read 往返保真。"""

    def test_create_tables_includes_new_columns(self, db):
        """create_tables 建出的表含 section/language/access_level 列。"""
        cols = {row[1] for row in db.conn.execute("PRAGMA table_info(articles)")}
        for col in ("section", "language", "access_level"):
            assert col in cols

    def test_new_columns_default_to_null(self, db):
        """不传新字段保存 → DB 中为 NULL，读回为 None。"""
        db.save_article(make_article())
        row = db.conn.execute(
            "SELECT section, language, access_level FROM articles"
        ).fetchone()
        assert row == (None, None, None)
        got = db.get_articles_since(datetime(2025, 1, 1))[0]
        assert got.section is None
        assert got.language is None
        assert got.access_level is None

    def test_save_read_roundtrip_new_fields(self, db):
        """save_article 往返保真：新字段值完整保存并读回。"""
        a = make_article(url="https://example.com/intl/1")
        a.section = "world"
        a.language = "en"
        a.access_level = "public"
        db.save_article(a)

        got = db.get_articles_since(datetime(2025, 1, 1))[0]
        assert got.section == "world"
        assert got.language == "en"
        assert got.access_level == "public"

    def test_save_articles_bulk_roundtrip_new_fields(self, db):
        """save_articles 批量往返保真，NULL 与取值混存。"""
        arts = []
        for i in range(3):
            a = make_article(url=f"https://example.com/intl/{i}", position=i)
            a.section = f"sec-{i}"
            a.language = "en" if i % 2 == 0 else None
            a.access_level = "public" if i % 2 == 0 else "metadata_only"
            arts.append(a)
        db.save_articles(arts)

        by_url = {a.url: a for a in db.get_articles_since(datetime(2025, 1, 1))}
        assert len(by_url) == 3
        for i in range(3):
            url = f"https://example.com/intl/{i}"
            assert by_url[url].section == f"sec-{i}"
            assert by_url[url].language == ("en" if i % 2 == 0 else None)
            assert by_url[url].access_level == (
                "public" if i % 2 == 0 else "metadata_only"
            )

    def test_old_schema_migration_preserves_old_rows(self, tmp_path):
        """旧库（Phase I 之前 schema + 已有数据）迁移后：
        新列被追加、旧行新列为 NULL、旧数据仍可读写、新字段可继续写入。"""
        import sqlite3

        db_path = tmp_path / "old_schema.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("""
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
        """)
        conn.execute(
            "INSERT INTO articles (source_id, source_name, category, title, url, "
            "published_at, fetched_at, position, summary, summary_source, "
            "summary_attempted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            ("s1", "媒體", "politics", "舊聞", "https://example.com/old",
             "2025-01-01T00:00:00", "2025-01-01T00:00:00", 1, None, None, None),
        )
        conn.commit()
        conn.close()

        db = Database(db_path)
        db.connect()
        try:
            # 迁移后新列存在
            cols = {row[1] for row in db.conn.execute("PRAGMA table_info(articles)")}
            for col in ("section", "language", "access_level"):
                assert col in cols
            # 旧行数据完好，新列为 NULL
            row = db.conn.execute(
                "SELECT title, section, language, access_level FROM articles"
            ).fetchone()
            assert row[0] == "舊聞"
            assert row[1:] == (None, None, None)
            # 旧数据可读（旧行为不变）
            got = db.get_articles_since(datetime(2000, 1, 1))
            assert len(got) == 1
            assert got[0].url == "https://example.com/old"
            assert got[0].section is None
            assert got[0].language is None
            assert got[0].access_level is None
            # 迁移后仍可写入新字段
            a = make_article(url="https://example.com/new")
            a.section = "world"
            a.language = "en"
            a.access_level = "public"
            db.save_article(a)
            got = db.get_articles_since(datetime(2000, 1, 1))
            assert len(got) == 2
            new_row = next(x for x in got if x.url == "https://example.com/new")
            assert new_row.section == "world"
            assert new_row.language == "en"
            assert new_row.access_level == "public"
        finally:
            db.close()
