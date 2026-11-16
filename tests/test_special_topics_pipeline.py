"""End-to-end: poll/betting sources through collection, storage, digest, Word."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import patch

from docx import Document

import app.main as main_mod
from app.database import Database
from app.digest import build_digest
from app.election2026.config import load_election_config
from app.models import Article
from app.time_utils import TAIPEI
from app.word_digest import build_word_digest

NOW = datetime(2026, 11, 13, 9, 0, tzinfo=TAIPEI)


def _a(title, category, url, source_name="自由時報", source_id=None):
    return Article(
        source_id=source_id or f"{category}_src", source_name=source_name,
        category=category, title=title, url=url, published_at=NOW,
        fetched_at=NOW, position=1,
    )


POLL_ITEMS = [
    _a("台灣民意基金會民調：賴清德聲望42.3% 不滿意46.1%", "poll", "https://n.test/poll/1"),
    _a("2026新北市長選舉民調 侯友宜支持度51.2%", "poll", "https://n.test/poll/2", source_name="Reuters"),
    _a("年終獎金調查出爐", "poll", "https://n.test/poll/3"),
]
BETTING_ITEMS = [
    _a("九合一選舉地下賭盤開盤 警方查獲組頭", "betting", "https://n.test/bet/1"),
    _a("颱風假明天放不放", "betting", "https://n.test/bet/2"),
]


def _fake_collector(items):
    class _Collector:
        def __init__(self, source):
            self.source = source

        def collect(self):
            return [item for item in items if item.category == self.source["category"]]

        def close(self):
            pass

    return _Collector


def _collect(tmp_path):
    db = Database(tmp_path / "news.db")
    db.connect()
    db.create_tables()
    sources = [
        {"id": "poll_src", "name": "民调", "type": "google_news_rss", "category": "poll", "url": "https://x"},
        {"id": "betting_src", "name": "赌盘", "type": "google_news_rss", "category": "betting", "url": "https://y"},
    ]
    with patch.object(main_mod, "COLLECTOR_MAP", {"google_news_rss": _fake_collector(POLL_ITEMS + BETTING_ITEMS)}):
        result = main_mod.collect_all(sources, db, {"enabled": False})
    return db, result


def test_collection_stores_all_and_gates_delivery(tmp_path):
    db, result = _collect(tmp_path)
    try:
        assert len(result.inserted_articles) == 5
        excluded = {a.url for a in result.filtered_from_delivery}
        assert excluded == {"https://n.test/poll/3", "https://n.test/bet/2"}
        rows = {
            r[0]: (r[1], r[2])
            for r in db.conn.execute("SELECT url, delivery_eligible, filter_reason FROM articles")
        }
        assert rows["https://n.test/poll/3"] == (0, "topic_relevance")
        assert rows["https://n.test/poll/1"] == (1, None)
        eligible = db.get_articles_since(datetime(2000, 1, 1), eligible_only=True)
        assert {a.url for a in eligible} == {
            "https://n.test/poll/1", "https://n.test/poll/2", "https://n.test/bet/1",
        }
        topics = {
            (r[0], r[1]): (r[2], r[3], json.loads(r[4]))
            for r in db.conn.execute(
                "SELECT url, topic, source_type, scope, metadata_json FROM news_topics "
                "WHERE topic IN ('poll','betting')"
            )
        }
        pollster, _, meta = topics[("https://n.test/poll/1", "poll")]
        assert pollster == "台灣民意基金會"
        assert meta["figures"][0]["value"] == 42.3
        assert topics[("https://n.test/bet/1", "betting")][1] == "election"
        # off-topic items are annotated too (relevant=False) for auditability
        assert topics[("https://n.test/poll/3", "poll")][2]["relevant"] is False
    finally:
        db.close()


def test_rerun_is_deduplicated(tmp_path):
    db, _ = _collect(tmp_path)
    db.close()
    db2, again = _collect(tmp_path)
    try:
        assert again.inserted_articles == []
        assert db2.conn.execute("SELECT COUNT(*) FROM articles").fetchone()[0] == 5
    finally:
        db2.close()


def test_text_digest_sections_and_annotations():
    deliverable = [POLL_ITEMS[0], POLL_ITEMS[1], BETTING_ITEMS[0]]
    text = build_digest(
        deliverable, NOW,
        international_config={"enabled": True, "tier1_international_media": ["Reuters"]},
        include_international_media=False,
    )
    assert "民调｜新增2条" in text  # the Reuters-published poll is not dropped
    assert "地下赌盘｜新增1条" in text
    assert "民调机构：台灣民意基金會｜关键数据：賴清德聲望42.3%、不滿意46.1%" in text
    assert "类型：选举赌盘、查缉执法｜涉及选举：是" in text


def test_word_sections_not_rerouted_to_election_or_international(tmp_path):
    deliverable = [POLL_ITEMS[0], POLL_ITEMS[1], BETTING_ITEMS[0]]
    election_config = load_election_config()
    path = build_word_digest(
        deliverable, tmp_path, generated_at=NOW,
        international_config={"enabled": True, "tier1_international_media": ["Reuters"]},
        election_config=election_config,
    )
    paragraphs = [p.text for p in Document(str(path)).paragraphs]
    headings = [t for t in paragraphs if "、" in t and t.split("、", 1)[0] in "一二三四五六七八九十"]
    assert any(h.endswith("民调动态") for h in headings)
    assert any(h.endswith("地下赌盘动态") for h in headings)
    assert not any("九合一" in h for h in headings)
    assert not any(h.endswith("国际媒体") for h in headings)
    assert "民调机构：台灣民意基金會｜关键数据：賴清德聲望42.3%、不滿意46.1%" in paragraphs
    assert "类型：选举赌盘、查缉执法｜涉及选举：是" in paragraphs
    assert any("侯友宜支持度51.2%" in t for t in paragraphs)


def test_sources_config_has_poll_and_betting_sources():
    sources = main_mod.load_sources(main_mod.Path(__file__).resolve().parents[1] / "config" / "sources.yaml")
    main_mod.validate_sources_config(sources, main_mod.COLLECTOR_MAP)
    poll = [s for s in sources if s.get("category") == "poll" and s.get("enabled", True)]
    betting = [s for s in sources if s.get("category") == "betting" and s.get("enabled", True)]
    assert len(poll) >= 6 and len(betting) >= 3
    for source in poll + betting:
        assert source["type"] == "google_news_rss"
        assert source["url"].startswith("https://news.google.com/rss/search?q=")
        assert "hl=zh-TW" in source["url"] and "ceid=TW:zh-Hant" in source["url"]
