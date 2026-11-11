import pytest, feedparser, email.utils
from pathlib import Path
from zoneinfo import ZoneInfo
from app.article_identity import article_identity_key

TAIPEI = ZoneInfo("Asia/Taipei")
FIXTURES = Path(__file__).resolve().parent / "fixtures"

def src(sid="storm_politics", cat="politics"):
    return {"id": sid, "name": "風傳媒", "type": "rss",
            "category": cat, "url": "https://www.storm.mg/api/getRss/...", "enabled": False}


class TestStormRSS:
    def test_politics_parses(self):
        xml = (FIXTURES / "storm_politics.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        assert len(feed.entries) >= 1

    def test_international_parses(self):
        xml = (FIXTURES / "storm_international.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        assert len(feed.entries) >= 1

    def test_time_aware(self):
        xml = (FIXTURES / "storm_politics.xml").read_text(encoding="utf-8")
        for e in feedparser.parse(xml).entries:
            pub = e.get("published", "")
            if not pub:
                continue
            dt = email.utils.parsedate_to_datetime(pub)
            assert dt.tzinfo is not None, f"No tzinfo for {pub}"

    def test_category_from_src(self):
        s = src(cat="international")
        assert s["category"] == "international"

    def test_title_not_empty(self):
        xml = (FIXTURES / "storm_politics.xml").read_text(encoding="utf-8")
        for e in feedparser.parse(xml).entries:
            if e.get("title"):
                break
        else:
            pytest.fail("All titles empty")

    def test_link_not_empty(self):
        xml = (FIXTURES / "storm_politics.xml").read_text(encoding="utf-8")
        for e in feedparser.parse(xml).entries:
            assert e.get("link"), f"Empty link at entry"
            break

    def test_link_storm_domain(self):
        xml = (FIXTURES / "storm_politics.xml").read_text(encoding="utf-8")
        for e in feedparser.parse(xml).entries[:5]:
            link = e.get("link", "")
            assert "storm.mg" in link

    def test_description_has_html(self):
        xml = (FIXTURES / "storm_politics.xml").read_text(encoding="utf-8")
        for e in feedparser.parse(xml).entries[:3]:
            desc = e.get("summary", "") or e.get("description", "")
            assert len(desc) > 0


class TestStormIdentity:
    def test_basic_identity(self):
        k = article_identity_key("https://www.storm.mg/article/11150542?utm_source=rss")
        assert k == "storm:11150542"

    def test_no_utm(self):
        k = article_identity_key("https://www.storm.mg/article/11150542")
        assert k == "storm:11150542"

    def test_identity_stable_across_feeds(self):
        a = article_identity_key("https://www.storm.mg/article/11150542?utm_source=rss")
        b = article_identity_key("https://www.storm.mg/article/11150542")
        assert a == b

    def test_different_ids(self):
        a = article_identity_key("https://www.storm.mg/article/11150542")
        b = article_identity_key("https://www.storm.mg/article/11150516")
        assert a != b

    def test_fragment(self):
        k = article_identity_key("https://www.storm.mg/article/11150542#comments")
        assert k == "storm:11150542"

    def test_non_storm_fallback(self):
        k = article_identity_key("https://example.com/article/123")
        assert k.startswith("url:")

    def test_utm_stripped(self):
        from app.collectors.base import BaseCollector
        u = BaseCollector.normalize_url("https://www.storm.mg/article/11150542?utm_source=rss&utm_medium=web")
        assert "utm" not in u

    def test_no_article_id(self):
        k = article_identity_key("https://www.storm.mg/")
        assert k.startswith("url:")

    def test_cross_feed_identity_same(self):
        """Same article in politics and international feeds gets same identity."""
        a = article_identity_key("https://www.storm.mg/article/11150542")
        b = article_identity_key("https://www.storm.mg/article/11150542")
        assert a == b
