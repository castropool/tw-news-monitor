import pytest, feedparser, email.utils
from pathlib import Path
from zoneinfo import ZoneInfo
from app.article_identity import article_identity_key

TAIPEI = ZoneInfo("Asia/Taipei")
FIXTURES = Path(__file__).resolve().parent / "fixtures"

def src(sid="newtalk_politics", cat="politics"):
    return {"id": sid, "name": "Newtalk\u65b0\u805e", "type": "newtalk_rss",
            "category": cat, "url": "https://newtalk.tw/rss/category/2", "enabled": False}

class TestNewtalkRSS:
    def test_politics_parses(self):
        xml = (FIXTURES / "newtalk_politics.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        assert len(feed.entries) >= 1

    def test_economy_parses(self):
        xml = (FIXTURES / "newtalk_economy.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        assert len(feed.entries) >= 1

    def test_international_parses(self):
        xml = (FIXTURES / "newtalk_international.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        assert len(feed.entries) >= 1

    def test_time_aware(self):
        xml = (FIXTURES / "newtalk_politics.xml").read_text(encoding="utf-8")
        for e in feedparser.parse(xml).entries:
            pub = e.get("published", "")
            if pub:
                dt = email.utils.parsedate_to_datetime(pub)
                assert dt.tzinfo is not None

    def test_category_from_src(self):
        s = src(cat="economy")
        assert s["category"] == "economy"

class TestNewtalkIdentity:
    def test_basic_identity(self):
        k = article_identity_key("https://newtalk.tw/news/view/2026-07-20/1000001")
        assert k == "newtalk:1000001"

    def test_www_unified(self):
        a = article_identity_key("https://newtalk.tw/news/view/2026-07-20/1")
        b = article_identity_key("https://www.newtalk.tw/news/view/2026-07-20/1")
        assert a == b

    def test_utm(self):
        k = article_identity_key("https://newtalk.tw/news/view/2026-07-20/1?utm_source=test")
        assert k == "newtalk:1"

    def test_fragment(self):
        k = article_identity_key("https://newtalk.tw/news/view/2026-07-20/1#c")
        assert k == "newtalk:1"

    def test_different_ids(self):
        a = article_identity_key("https://newtalk.tw/news/view/2026-07-20/1")
        b = article_identity_key("https://newtalk.tw/news/view/2026-07-20/2")
        assert a != b

    def test_non_newtalk_fallback(self):
        k = article_identity_key("https://example.com/x")
        assert k.startswith("url:")

class TestNewtalkNormalize:
    def test_utm_stripped(self):
        from app.collectors.base import BaseCollector
        u = BaseCollector.normalize_url("https://newtalk.tw/news/view/1?utm_source=x")
        assert "utm" not in u
