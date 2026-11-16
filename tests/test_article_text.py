"""Internal full-text store: schema, capture, opt-in fetch, retention."""

from datetime import datetime, timedelta, timezone

import pytest

from app import article_text, summarizer
from app.database import SCHEMA_VERSION, Database
from app.models import Article

NOW = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)
LONG = "國防部今日公布共軍動態，" * 10  # > MIN_TEXT_CHARS


def make_article(url, access_level=None, summary=None):
    return Article(
        source_id="test",
        source_name="中央社",
        category="politics",
        title="測試新聞",
        url=url,
        published_at=datetime(2026, 11, 14, 7, 0),
        fetched_at=datetime(2026, 11, 14, 7, 30),
        position=1,
        summary=summary,
        access_level=access_level,
    )


@pytest.fixture
def db(tmp_path):
    database = Database(tmp_path / "news.db")
    database.connect()
    yield database
    database.close()


def test_schema_adds_article_texts_without_touching_articles(db):
    assert db.schema_version() == SCHEMA_VERSION == 8
    cols = {r[1] for r in db.conn.execute("PRAGMA table_info(article_texts)")}
    assert cols == {"url", "text", "extractor", "char_count", "content_sha256", "fetched_at"}


def test_upgrade_from_v7_database_keeps_rows(tmp_path):
    path = tmp_path / "old.db"
    first = Database(path)
    first.connect()
    first.save_articles([make_article("https://example.com/a")])
    first.conn.execute("DROP TABLE article_texts")
    first.conn.execute("UPDATE schema_meta SET value='7' WHERE key='schema_version'")
    first.conn.commit()
    first.close()
    again = Database(path)
    again.connect()
    assert again.count_articles() == 1
    assert again.count_article_texts() == 0
    assert again.schema_version() == 8
    again.close()


def test_save_is_idempotent_and_updates_on_change(db):
    url = "https://example.com/a"
    assert db.save_article_texts({url: LONG}, extractor="trafilatura", fetched_at=NOW) == 1
    assert db.save_article_texts({url: LONG}, extractor="trafilatura", fetched_at=NOW) == 0
    assert db.save_article_texts({url: LONG + "更新"}, extractor="trafilatura", fetched_at=NOW) == 1
    row = db.get_article_text(url)
    assert row["text"].endswith("更新") and row["char_count"] == len(LONG) + 2


def test_save_texts_skips_short_fragments_and_respects_switch(db, monkeypatch):
    assert article_text.save_texts(db, {"https://x/1": "太短", "https://x/2": LONG}, now=NOW) == 1
    assert db.get_urls_with_text(["https://x/1", "https://x/2"]) == {"https://x/2"}
    monkeypatch.setenv("ARTICLE_TEXT_STORE", "0")
    assert article_text.save_texts(db, {"https://x/3": LONG}, now=NOW) == 0


def test_purge_removes_only_expired_text(db):
    db.save_article_texts({"https://x/old": LONG}, extractor="t", fetched_at=NOW - timedelta(days=731))
    db.save_article_texts({"https://x/new": LONG}, extractor="t", fetched_at=NOW - timedelta(days=700))
    assert article_text.purge_expired(db, now=NOW) == 1
    assert db.get_urls_with_text(["https://x/old", "https://x/new"]) == {"https://x/new"}


def test_retention_env_override(monkeypatch):
    assert article_text.retention_days() == 730
    monkeypatch.setenv("ARTICLE_TEXT_RETENTION_DAYS", "90")
    assert article_text.retention_days() == 90


def test_fetch_missing_is_off_by_default_outside_scope(db):
    def boom(_):
        raise AssertionError("must not fetch")

    assert article_text.fetch_missing(db, [make_article("https://x/1")], fetcher=boom) == 0


def test_inscope_military_text_is_fetched_by_default_with_cap(db, monkeypatch):
    monkeypatch.delenv("ARTICLE_TEXT_INSCOPE_FETCH_MAX")  # conftest sets 0 for other tests
    asked = []

    def fake_fetch(articles):
        asked.extend(a.url for a in articles)
        return {a.url: LONG for a in articles}

    military = [make_article(f"https://m/{i}") for i in range(25)]
    for a in military:
        a.category = "military"
    others = [make_article("https://x/1")]
    assert article_text.fetch_missing(db, others + military, fetcher=fake_fetch, now=NOW) == 20
    assert asked == [f"https://m/{i}" for i in range(20)]
    monkeypatch.setenv("ARTICLE_TEXT_INSCOPE_FETCH_MAX", "0")
    asked.clear()
    assert article_text.fetch_missing(db, military, fetcher=fake_fetch, now=NOW) == 0


def test_fetch_missing_caps_skips_stored_and_international(db, monkeypatch):
    monkeypatch.setenv("ARTICLE_TEXT_FETCH_MAX", "2")
    monkeypatch.setenv("ARTICLE_TEXT_INSCOPE_FETCH_MAX", "0")
    db.save_article_texts({"https://x/have": LONG}, extractor="t", fetched_at=NOW)
    asked = []

    def fake_fetch(articles):
        asked.extend(a.url for a in articles)
        return {a.url: LONG for a in articles}

    articles = [
        make_article("https://x/have"),
        make_article("https://www.reuters.com/w", access_level="metadata_only"),
        make_article("https://x/1"),
        make_article("https://x/2"),
        make_article("https://x/3"),
    ]
    assert article_text.fetch_missing(db, articles, fetcher=fake_fetch, now=NOW) == 2
    assert asked == ["https://x/1", "https://x/2"]


def test_fetch_missing_never_raises(db, monkeypatch):
    monkeypatch.setenv("ARTICLE_TEXT_FETCH_MAX", "5")

    def broken(_):
        raise RuntimeError("network down")

    assert article_text.fetch_missing(db, [make_article("https://x/1")], fetcher=broken) == 0


def test_hybrid_summary_run_stores_main_text_but_not_meta_fallback(db, monkeypatch):
    monkeypatch.setenv("SUMMARIZER_MODE", "hybrid")
    monkeypatch.setattr(summarizer, "deepseek_available", lambda: True)
    pages = {
        "https://x/main": (LONG, True, True),
        "https://x/meta": ("只有描述" * 30, True, False),
    }
    monkeypatch.setattr(summarizer, "_fetch_article_content", lambda url, client: pages[url])
    monkeypatch.setattr(
        summarizer, "summarize_with_deepseek",
        lambda articles, **kw: {a.url: "摘要。" for a in articles},
    )
    articles = [make_article("https://x/main"), make_article("https://x/meta")]
    db.save_articles(articles)
    summarizer.enrich_articles_with_summaries(articles, db)
    assert db.get_urls_with_text(["https://x/main", "https://x/meta"]) == {"https://x/main"}
    assert db.get_article_text("https://x/main")["extractor"] == "trafilatura"
