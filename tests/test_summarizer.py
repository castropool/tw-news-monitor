"""Tests for the summary enrichment feature (RSS teasers + DeepSeek batch)."""

import json
import sqlite3
from datetime import datetime, timezone

import pytest
from docx import Document

from app import summarizer
from app.collectors.rss import RSSCollector
from app.database import Database
from app.models import Article
from app.word_digest import build_word_digest


def make_article(
    url="https://example.com/news/1",
    title="測試新聞",
    summary=None,
    summary_source=None,
    summary_attempted_at=None,
    access_level=None,
):
    now = datetime(2026, 7, 14, 20, 30)
    return Article(
        source_id="test",
        source_name="中央社",
        category="politics",
        title=title,
        url=url,
        published_at=now,
        fetched_at=now,
        position=1,
        summary=summary,
        summary_source=summary_source,
        summary_attempted_at=summary_attempted_at,
        access_level=access_level,
    )


class FakeDB:
    def __init__(self):
        self.calls = []

    def update_article_summaries(self, summaries, source="llm", attempted_at=None):
        self.calls.append(("update", source, dict(summaries)))

    def mark_summary_attempted(self, urls, attempted_at=None):
        self.calls.append(("mark", sorted(urls)))


class TestRssTeaser:
    def test_clean_rss_summary_strips_html(self):
        text = "<p>立法院今天審查預算案，<b>朝野</b>進行攻防。</p>"
        assert summarizer.clean_rss_summary(text) == "立法院今天審查預算案，朝野進行攻防。"

    def test_clean_rss_summary_truncates(self):
        text = "字" * 300
        result = summarizer.clean_rss_summary(text, max_length=20)
        assert result == text

    def test_clean_rss_summary_keeps_last_complete_sentence(self):
        text = "第一句完整。第二句很长" + "字" * 200
        assert summarizer.clean_rss_summary(text, max_length=20) == "第一句完整。"

    def test_clean_rss_summary_removes_feed_cutoff_after_complete_sentence(self):
        text = "第一句完整。第二句被來源截斷...…"
        assert summarizer.clean_rss_summary(text) == "第一句完整。"

    def test_clean_rss_summary_discards_cutoff_when_no_complete_sentence_exists(self):
        text = "只有被來源截斷的半句話..."
        assert summarizer.clean_rss_summary(text) is None

    def test_complete_overlength_summary_does_not_need_rewrite(self):
        article = make_article(summary="完整句子。" + "字" * 200)
        assert summarizer.summary_needs_rewrite(article) is False

    def test_summary_needs_rewrite_detects_ascii_cutoff_marker(self):
        article = make_article(summary="完整句子。後半段被截斷...")
        assert summarizer.summary_needs_rewrite(article) is True

    def test_clean_rss_summary_empty(self):
        assert summarizer.clean_rss_summary("") is None
        assert summarizer.clean_rss_summary("<p></p>") is None

    def test_clean_rss_summary_strips_leading_image_captions(self):
        text = (
            "圖為解放軍2025年93閱兵的殲-20/A/S編隊。（中央社檔案照片） "
            "圖：中央社提供 Newtalk新聞 "
            "中國人民解放軍今天迎來建軍99週年。"
        )
        result = summarizer.clean_rss_summary(text)
        assert result == "中國人民解放軍今天迎來建軍99週年。"

    def test_rss_summary_from_entry_prefers_summary(self):
        entry = {"summary": "摘要A", "description": "摘要B"}
        assert summarizer.rss_summary_from_entry(entry) == "摘要A"

    def test_rss_summary_from_entry_falls_back_to_description(self):
        entry = {"summary": "", "description": "<p>導語B</p>"}
        assert summarizer.rss_summary_from_entry(entry) == "導語B"

    def test_rss_summary_from_entry_none(self):
        assert summarizer.rss_summary_from_entry({}) is None

    def test_rss_collector_captures_feed_summary(self):
        xml = """<?xml version="1.0"?>
        <rss version="2.0"><channel><title>T</title><item>
        <title>新聞標題</title>
        <link>https://www.cna.com.tw/news/aipl/202607140001.aspx</link>
        <description><![CDATA[<p>導語內容</p>]]></description>
        <pubDate>Mon, 14 Jul 2026 10:00:00 +0800</pubDate>
        </item></channel></rss>"""

        class FakeResp:
            text = xml

            def raise_for_status(self):
                return None

        class FakeClient:
            def get(self, url):
                return FakeResp()

        source = {
            "id": "cna_politics",
            "name": "中央社",
            "category": "politics",
            "url": "https://example.com/feed",
        }
        collector = RSSCollector(source)
        collector._client = FakeClient()
        articles = collector.collect()
        assert articles[0].summary == "導語內容"
        assert articles[0].summary_source == "rss"


class TestParseResponse:
    def test_plain_dict(self):
        requested = {"https://example.com/1", "https://example.com/2"}
        data = {
            "https://example.com/1": "測試摘要一號",
            "https://example.com/2": "測試摘要二號",
            "https://evil.example/3": "不應出現的外站內容",
        }
        result = summarizer.parse_summaries_response(data, requested)
        assert result == {
            "https://example.com/1": "測試摘要一號",
            "https://example.com/2": "測試摘要二號",
        }

    def test_wrapped_dict_and_list(self):
        requested = {"https://example.com/1"}
        wrapped = {"summaries": {"https://example.com/1": "測試摘要一號"}}
        assert summarizer.parse_summaries_response(wrapped, requested)[
            "https://example.com/1"
        ] == "測試摘要一號"
        listed = [{"url": "https://example.com/1", "summary": "測試摘要一號"}]
        assert summarizer.parse_summaries_response(listed, requested)[
            "https://example.com/1"
        ] == "測試摘要一號"

    def test_filters_blank_short_and_unknown(self):
        requested = {"https://example.com/1"}
        data = {"https://example.com/1": "   ", "https://example.com/2": "合法摘要內容"}
        assert summarizer.parse_summaries_response(data, requested) == {}

    def test_does_not_cut_model_summary_mid_sentence(self):
        requested = {"https://example.com/1"}
        text = "完整第一句。" + "字" * 200
        result = summarizer.parse_summaries_response(
            {"https://example.com/1": text}, requested, max_length=20
        )
        assert result["https://example.com/1"] == "完整第一句。"

    def test_parse_summaries_response_removes_model_cutoff_marker(self):
        requested = {"https://example.com/1"}
        result = summarizer.parse_summaries_response(
            {"https://example.com/1": "完整第一句。後半段被模型截斷..."}, requested
        )
        assert result == {"https://example.com/1": "完整第一句。"}

    def test_parse_summaries_response_rejects_only_cutoff_fragment(self):
        requested = {"https://example.com/1"}
        result = summarizer.parse_summaries_response(
            {"https://example.com/1": "只有半句被模型截斷..."}, requested
        )
        assert result == {}


class TestInternationalMetadataTranslation:
    def test_translate_metadata_uses_only_supplied_metadata(self, monkeypatch):
        class FakeClient:
            def analyze(self, system, user):
                assert "https://" not in user
                assert "English title" in user
                assert "English teaser" in user
                return {
                    "status": "success",
                    "title": "中文標題",
                    "summary": "中文摘要。",
                }

        monkeypatch.setenv("INTERNATIONAL_TRANSLATION_ENABLED", "true")
        monkeypatch.setattr(summarizer, "_load_deepseek_client", lambda: FakeClient())
        assert summarizer.translate_metadata(
            "English title", "English teaser", source_name="Reuters"
        ) == ("中文標題", "中文摘要。")


class TestPrompt:
    def test_build_batch_prompt_requires_five_elements(self):
        system, user = summarizer.build_batch_prompt([make_article()])
        assert "五要素" in system
        for keyword in ("时间", "人物", "事件", "地点", "原因"):
            assert keyword in system
        assert "50-120" in system
        assert "避免只复述标题" in system
        assert "has_content" in system
        assert "繁体中文" in system

    def test_build_batch_prompt_includes_content(self):
        system, user = summarizer.build_batch_prompt(
            [make_article()], contents={"https://example.com/news/1": "正文內容"}
        )
        payload = json.loads(user)
        assert payload["articles"][0]["content"] == "正文內容"
        assert payload["articles"][0]["has_content"] is True

    def test_build_batch_prompt_marks_missing_content(self):
        system, user = summarizer.build_batch_prompt([make_article()])
        payload = json.loads(user)
        assert payload["articles"][0]["has_content"] is False
        assert "content" not in payload["articles"][0]

    def test_extract_article_text_with_trafilatura(self):
        pytest.importorskip("trafilatura")
        html = (
            "<html><head><title>新聞</title></head><body>"
            "<nav><a href='/'>首頁</a><a href='/a'>政治</a>"
            "<a href='/b'>財經</a><a href='/c'>國際</a>"
            "<a href='/d'>娛樂</a></nav>"
            "<article><h1>標題</h1><p>第一段內容。</p><p>第二段內容。</p></article>"
            "<footer>版權所有</footer></body></html>"
        )
        text = summarizer._extract_article_text(html)
        assert text
        assert "第一段內容" in text
        assert "第二段內容" in text
        assert len(text) < len(html) // 2

    def test_fetch_article_contents_tracks_empty_not_errors(self, monkeypatch):
        fake_results = iter(
            [("正文一", True), (None, True), (None, False)]
        )
        monkeypatch.setattr(
            summarizer,
            "_fetch_article_content",
            lambda url, client: next(fake_results),
        )
        articles = [
            make_article(url=f"https://example.com/{i}") for i in range(3)
        ]
        contents, empty = summarizer.fetch_article_contents(articles, max_workers=1)
        assert contents == {"https://example.com/0": "正文一"}
        assert empty == ["https://example.com/1"]


class TestEnrich:
    def test_international_access_level_never_fetches_article_page(self, monkeypatch):
        monkeypatch.setenv("SUMMARIZER_MODE", "hybrid")
        monkeypatch.setattr(summarizer, "deepseek_available", lambda: False)
        fetched = {"content": [], "meta": []}

        def fake_contents(articles, **kwargs):
            fetched["content"] = [a.url for a in articles]
            return {}, []

        def fake_meta(articles, **kwargs):
            fetched["meta"] = [a.url for a in articles]
            return {}

        monkeypatch.setattr(summarizer, "fetch_article_contents", fake_contents)
        monkeypatch.setattr(summarizer, "fetch_meta_descriptions", fake_meta)
        monkeypatch.setattr(
            summarizer, "summarize_with_deepseek", lambda articles, **kwargs: {}
        )
        local = make_article(url="https://example.com/local")
        metadata = make_article(
            url="https://www.reuters.com/world/taiwan-a1",
            access_level="metadata_only",
        )
        newsletter = make_article(
            url="https://www.wsj.com/world/taiwan-a2",
            access_level="newsletter",
        )

        summarizer.enrich_articles_with_summaries([local, metadata, newsletter])

        assert fetched["content"] == [local.url]
        assert fetched["meta"] == [local.url]
    def test_enrich_uses_deepseek(self, monkeypatch):
        monkeypatch.setenv("SUMMARIZER_MODE", "llm")
        monkeypatch.setattr(
            summarizer,
            "summarize_with_deepseek",
            lambda articles, **kw: {a.url: "AI摘要" for a in articles},
        )
        fake_db = FakeDB()
        articles = [
            make_article(url="https://example.com/1"),
            make_article(url="https://example.com/2", summary="已有"),
        ]
        summarizer.enrich_articles_with_summaries(articles, fake_db)
        assert articles[0].summary == "AI摘要"
        assert articles[0].summary_source == "llm"
        assert articles[1].summary == "已有"
        assert fake_db.calls == [
            ("update", "llm", {"https://example.com/1": "AI摘要"})
        ]

    def test_enrich_meta_fallback_without_deepseek_key(self, monkeypatch):
        monkeypatch.setenv("SUMMARIZER_MODE", "hybrid")
        monkeypatch.setattr(summarizer, "deepseek_available", lambda: False)
        monkeypatch.setattr(summarizer, "summarize_with_deepseek", lambda articles, **kw: {})
        monkeypatch.setattr(
            summarizer, "fetch_article_contents", lambda articles, **kw: ({}, [])
        )
        monkeypatch.setattr(
            summarizer,
            "fetch_meta_descriptions",
            lambda articles, **kw: {a.url: "META摘要" for a in articles},
        )
        fake_db = FakeDB()
        articles = [make_article(url="https://example.com/1")]
        summarizer.enrich_articles_with_summaries(articles, fake_db)
        assert articles[0].summary == "META摘要"
        assert articles[0].summary_source == "meta"
        assert ("update", "meta", {"https://example.com/1": "META摘要"}) in fake_db.calls

    def test_enrich_hybrid_fetches_content_and_uses_it(self, monkeypatch):
        monkeypatch.setenv("SUMMARIZER_MODE", "hybrid")
        monkeypatch.setattr(summarizer, "deepseek_available", lambda: True)
        monkeypatch.setattr(
            summarizer,
            "fetch_article_contents",
            lambda articles, **kw: ({a.url: "正文內容" for a in articles}, []),
        )
        captured = {}

        def fake_summarize(articles, contents=None, **kw):
            captured["contents"] = contents
            return {a.url: "五要素摘要" for a in articles}

        monkeypatch.setattr(summarizer, "summarize_with_deepseek", fake_summarize)
        fake_db = FakeDB()
        articles = [make_article(url="https://example.com/1")]
        summarizer.enrich_articles_with_summaries(articles, fake_db)
        assert captured["contents"] == {"https://example.com/1": "正文內容"}
        assert articles[0].summary == "五要素摘要"
        assert articles[0].summary_source == "llm"

    def test_enrich_skips_recent_attempted(self, monkeypatch):
        monkeypatch.setenv("SUMMARIZER_MODE", "hybrid")

        def boom(*args, **kwargs):
            raise AssertionError("must not fetch")

        monkeypatch.setattr(summarizer, "fetch_article_contents", boom)
        articles = [
            make_article(
                url="https://example.com/1",
                summary_attempted_at=datetime.now(timezone.utc),
            )
        ]
        summarizer.enrich_articles_with_summaries(articles, None)
        assert articles[0].summary is None

    def test_enrich_rss_mode_does_nothing(self, monkeypatch):
        monkeypatch.setenv("SUMMARIZER_MODE", "rss")

        def boom(*args, **kwargs):
            raise AssertionError("must not fetch")

        monkeypatch.setattr(summarizer, "summarize_with_deepseek", boom)
        monkeypatch.setattr(summarizer, "fetch_meta_descriptions", boom)
        fake_db = FakeDB()
        articles = [make_article()]
        summarizer.enrich_articles_with_summaries(articles, fake_db)
        assert articles[0].summary is None
        assert fake_db.calls == []

    def test_enrich_llm_failure_never_raises(self, monkeypatch):
        monkeypatch.setenv("SUMMARIZER_MODE", "llm")

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(summarizer, "summarize_with_deepseek", boom)
        articles = [make_article()]
        result = summarizer.enrich_articles_with_summaries(articles, None)
        assert result is articles
        assert articles[0].summary is None

    def test_enrich_repairs_truncated_existing_summary_when_llm_fails(self, monkeypatch):
        monkeypatch.setenv("SUMMARIZER_MODE", "llm")
        monkeypatch.setattr(summarizer, "summarize_with_deepseek", lambda *args, **kw: {})
        fake_db = FakeDB()
        articles = [
            make_article(
                summary="第一句完整。第二句被遠端摘要截斷...",
                summary_source="rss",
            )
        ]

        summarizer.enrich_articles_with_summaries(articles, fake_db)

        assert articles[0].summary == "第一句完整。"
        assert articles[0].summary_source == "rss"
        assert ("update", "rss", {articles[0].url: "第一句完整。"}) in fake_db.calls

    def test_enrich_uses_title_fallback_for_only_cutoff_fragment(self, monkeypatch):
        monkeypatch.setenv("SUMMARIZER_MODE", "llm")
        monkeypatch.setattr(summarizer, "summarize_with_deepseek", lambda *args, **kw: {})
        fake_db = FakeDB()
        articles = [
            make_article(
                title="測試新聞標題",
                summary="只有半句被遠端摘要截斷...",
                summary_source="rss",
            )
        ]

        summarizer.enrich_articles_with_summaries(articles, fake_db)

        assert articles[0].summary == "标题指出：測試新聞標題。"
        assert articles[0].summary_source == "fallback"

    def test_enrich_meta_failure_never_raises(self, monkeypatch):
        monkeypatch.setenv("SUMMARIZER_MODE", "meta")

        def boom(*args, **kwargs):
            raise RuntimeError("boom")

        monkeypatch.setattr(summarizer, "fetch_meta_descriptions", boom)
        articles = [make_article()]
        summarizer.enrich_articles_with_summaries(articles, None)
        assert articles[0].summary is None


class TestDatabase:
    def test_database_migrates_old_schema(self, tmp_path):
        db_path = tmp_path / "old.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            "CREATE TABLE articles ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT,"
            "source_id TEXT NOT NULL, source_name TEXT NOT NULL,"
            "category TEXT NOT NULL, title TEXT NOT NULL,"
            "url TEXT NOT NULL UNIQUE, published_at TEXT,"
            "fetched_at TEXT NOT NULL, position INTEGER NOT NULL)"
        )
        conn.commit()
        conn.close()

        db = Database(db_path)
        db.connect()
        cols = [r[1] for r in db.conn.execute("PRAGMA table_info(articles)").fetchall()]
        assert "summary" in cols
        assert "summary_source" in cols
        assert "summary_attempted_at" in cols
        db.close()

    def test_database_summary_roundtrip(self, tmp_path):
        db = Database(tmp_path / "news.db")
        db.connect()
        db.create_tables()
        db.save_article(make_article(summary="測試梗概", summary_source="rss"))

        got = db.get_articles_since(datetime(2000, 1, 1))
        assert got[0].summary == "測試梗概"
        assert got[0].summary_source == "rss"

        db.update_article_summaries(
            {"https://example.com/news/1": "AI更新"},
            source="llm",
            attempted_at=datetime(2026, 7, 14, 20, 30, tzinfo=timezone.utc),
        )
        got = db.get_articles_since(datetime(2000, 1, 1))
        assert got[0].summary == "AI更新"
        assert got[0].summary_source == "llm"
        assert got[0].summary_attempted_at == datetime(
            2026, 7, 14, 20, 30, tzinfo=timezone.utc
        )

        db.save_article(make_article(url="https://example.com/news/2"))
        db.mark_summary_attempted(
            ["https://example.com/news/2"],
            attempted_at=datetime(2026, 7, 14, 21, 0, tzinfo=timezone.utc),
        )
        by_url = {a.url: a for a in db.get_articles_since(datetime(2000, 1, 1))}
        assert by_url["https://example.com/news/2"].summary is None
        assert by_url["https://example.com/news/2"].summary_attempted_at == datetime(
            2026, 7, 14, 21, 0, tzinfo=timezone.utc
        )
        db.close()


class TestWordDigest:
    def test_word_digest_contains_summary(self, tmp_path):
        articles = [make_article(summary="這是測試梗概")]
        output = build_word_digest(articles, tmp_path)
        doc = Document(str(output))
        texts = [p.text for p in doc.paragraphs]
        assert any("梗概：這是測試梗概" in t for t in texts)
