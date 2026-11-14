"""Chinese full-text search: tokenization, Simplified/Traditional folding, sync."""

from datetime import date, datetime, timezone

import pytest

from app import search
from app.database import Database
from app.models import Article

NOW = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)


def art(url, title, summary=None, category="politics", day=14):
    return Article(
        source_id="t", source_name="中央社", category=category, title=title, url=url,
        published_at=datetime(2026, 11, day, 7, 0), fetched_at=datetime(2026, 11, day, 7, 30),
        position=1, summary=summary,
    )


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "news.db")
    d.connect()
    d.save_articles([
        art("https://x/1", "國防部：共機25架次逾越海峽中線", "國防部今日偵獲共機與共艦。", "military"),
        art("https://x/2", "賴清德出席國慶活動", "總統談兩岸關係。", day=10),
        art("https://x/3", "F-16V戰機完成升級", None, "military"),
        art("https://x/4", "部會首長異動", "國防預算審查延後。", day=12),
    ])
    d.save_article_texts(
        {"https://x/2": "賴清德表示，國防自主是重中之重，將持續推動潛艦國造。" * 3},
        extractor="trafilatura", fetched_at=NOW,
    )
    yield d
    d.close()


def urls(hits):
    return [h.url for h in hits]


def test_index_text_splits_han_characters_and_keeps_latin_words():
    assert search.index_text("F-16V戰機") == "f 16v 戰 機"


def test_match_is_phrase_per_term_and_and_between_terms():
    assert search.build_match("国防部  共机") == '"國 防 部" AND "共 機"'
    assert search.build_match(" ， ") is None


def test_simplified_and_traditional_queries_match_the_same(db):
    assert urls(search.search(db.conn, "国防部")) == urls(search.search(db.conn, "國防部"))
    assert "https://x/1" in urls(search.search(db.conn, "国防部"))


def test_characters_must_be_adjacent(db):
    # x/4 holds 部 and 防 but never as the adjacent pair 防部.
    assert urls(search.search(db.conn, "防部")) == ["https://x/1"]


def test_full_text_is_searchable_and_title_hits_rank_first(db):
    hits = search.search(db.conn, "國防")
    assert set(urls(hits)) == {"https://x/1", "https://x/2", "https://x/4"}
    assert hits[0].url == "https://x/1"  # title match outranks body/summary
    assert urls(search.search(db.conn, "潛艦國造")) == ["https://x/2"]
    assert search.search(db.conn, "潛艦國造")[0].has_text is True


def test_filters_by_date_and_category(db):
    assert urls(search.search(db.conn, "國防", category="military")) == ["https://x/1"]
    assert set(urls(search.search(db.conn, "國防", since=date(2026, 11, 11)))) == {"https://x/1", "https://x/4"}
    assert urls(search.search(db.conn, "國防", until=date(2026, 11, 10))) == ["https://x/2"]


def test_latin_terms_and_snippet_display(db):
    hit = search.search(db.conn, "f-16v")[0]
    assert hit.url == "https://x/3"
    assert hit.snippet == "【F-16V】戰機完成升級"
    body_hit = search.search(db.conn, "潜艇国造".replace("艇", "艦"))[0]
    assert "【潛艦國造】" in body_hit.snippet and body_hit.snippet.endswith("…")


def test_sync_is_incremental_and_tracks_changes(db):
    assert search.sync_index(db.conn) == 4
    assert search.sync_index(db.conn) == 0
    db.update_article_summaries({"https://x/3": "空軍新增反艦飛彈掛載。"}, source="llm", attempted_at=NOW)
    assert search.sync_index(db.conn) == 1
    assert urls(search.search(db.conn, "反艦飛彈")) == ["https://x/3"]
    db.purge_article_texts(datetime(2030, 1, 1, tzinfo=timezone.utc))
    assert search.sync_index(db.conn) == 1
    assert search.search(db.conn, "潛艦國造") == []
    db.save_articles([art("https://x/5", "海巡署驅離中國海警船")])
    assert urls(search.search(db.conn, "海警")) == ["https://x/5"]


def test_rebuild_reindexes_everything(db):
    search.sync_index(db.conn)
    assert search.sync_index(db.conn, rebuild=True) == 4


def test_connect_does_not_create_search_tables(tmp_path):
    d = Database(tmp_path / "n.db")
    d.connect()
    names = {r[0] for r in d.conn.execute("SELECT name FROM sqlite_master")}
    assert "article_search" not in names
    d.close()


def test_cli_prints_hits(db, tmp_path, capsys):
    path = db._db_path
    db.close()
    assert search.main(["國防部", "--db", str(path)]) == 0
    out = capsys.readouterr().out
    assert "https://x/1" in out and "共機25架次" in out
    assert search.main(["沒有這個詞", "--db", str(path)]) == 0
    assert "No matches." in capsys.readouterr().out
    db.connect()
