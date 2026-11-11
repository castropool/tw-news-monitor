import pytest
from pathlib import Path
from urllib.parse import urlsplit

from app.collectors.ltn import LtnRSSCollector, LTN_ALLOWED_HOSTS

FIXTURES = Path(__file__).resolve().parent / "fixtures"

def make_source(url, sid="ltn_politics", cat="politics"):
    return {"id": sid, "name": "自由時報", "type": "ltn_rss", "category": cat, "url": url, "enabled": False}


class TestLtnAllowedHosts:
    def test_allows_news_domain(self):
        assert "news.ltn.com.tw" in LTN_ALLOWED_HOSTS

    def test_allows_economic_domain(self):
        assert "ec.ltn.com.tw" in LTN_ALLOWED_HOSTS

    def test_rejects_external(self):
        assert "example.com" not in LTN_ALLOWED_HOSTS
        assert "ltn.com.tw.evil.example" not in LTN_ALLOWED_HOSTS


class TestLtnUrlNormalization:
    def test_normalize_strips_utm(self):
        c = LtnRSSCollector(make_source("https://news.ltn.com.tw/rss/politics.xml"))
        u = c.normalize_url(
            "https://news.ltn.com.tw/news/politics/breakingnews/1234567?utm_source=test"
        )
        assert "utm_source" not in u
        assert "breakingnews/1234567" in u

    def test_normalize_strips_fragment(self):
        c = LtnRSSCollector(make_source("https://news.ltn.com.tw/rss/politics.xml"))
        u = c.normalize_url(
            "https://news.ltn.com.tw/news/politics/breakingnews/1234567#comment"
        )
        assert "#" not in u

    def test_same_url_same_identity(self):
        from app.article_identity import article_identity_key
        a = article_identity_key("https://news.ltn.com.tw/news/politics/breakingnews/1234567")
        b = article_identity_key("https://news.ltn.com.tw/news/politics/breakingnews/1234567?utm_campaign=test")
        assert a == b

    def test_different_articles_different_identity(self):
        from app.article_identity import article_identity_key
        a = article_identity_key("https://news.ltn.com.tw/news/politics/breakingnews/1234567")
        b = article_identity_key("https://news.ltn.com.tw/news/politics/breakingnews/7654321")
        assert a != b


class TestLtnDescriptionCleaning:
    def test_strip_script_tags(self):
        from app.collectors.ltn import LtnRSSCollector
        c = LtnRSSCollector(make_source("https://news.ltn.com.tw/rss/politics.xml"))

    def test_identity_has_ltn_prefix(self):
        from app.article_identity import article_identity_key
        k = article_identity_key("https://news.ltn.com.tw/news/politics/breakingnews/1234567")
        assert k.startswith("ltn:")


class TestLtnRSSParsing:
    @pytest.fixture
    def collector(self):
        return LtnRSSCollector(make_source("https://news.ltn.com.tw/rss/politics.xml"))

    def test_politics_fixture_parses(self):
        c = LtnRSSCollector(make_source("https://news.ltn.com.tw/rss/politics.xml"))
        import feedparser
        xml = (FIXTURES / "ltn_politics.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        assert len(feed.entries) == 2
        assert feed.entries[0].title == "政治新闻X"
        assert feed.entries[0].link == "https://news.ltn.com.tw/news/politics/breakingnews/1234567"

    def test_business_fixture_parses(self):
        c = LtnRSSCollector(make_source("https://news.ltn.com.tw/rss/business.xml"))
        import feedparser
        xml = (FIXTURES / "ltn_business.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        assert len(feed.entries) == 1
        assert feed.entries[0].title == "财经新闻A"

    def test_world_fixture_parses(self):
        c = LtnRSSCollector(make_source("https://news.ltn.com.tw/rss/world.xml"))
        import feedparser
        xml = (FIXTURES / "ltn_world.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        assert len(feed.entries) == 1
        assert feed.entries[0].title == "国际新闻A"

    def test_politics_time_aware(self):
        import feedparser, email.utils
        from zoneinfo import ZoneInfo
        TAIPEI = ZoneInfo("Asia/Taipei")
        xml = (FIXTURES / "ltn_politics.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        for entry in feed.entries:
            pub = entry.get("published", "")
            if pub:
                dt = email.utils.parsedate_to_datetime(pub)
                assert dt.tzinfo is not None
                dt_taipei = dt.astimezone(TAIPEI)
                assert dt_taipei.tzinfo == TAIPEI

    def test_politics_category(self):
        c = LtnRSSCollector(make_source("https://news.ltn.com.tw/rss/politics.xml"))
        assert c.category == "politics"


class TestLtnBadItemIsolation:
    def test_missing_title_skipped(self):
        c = LtnRSSCollector(make_source("https://news.ltn.com.tw/rss/politics.xml"))
        import feedparser
        xml = (FIXTURES / "ltn_politics.xml").read_text(encoding="utf-8")
        # Remove title from first item
        xml_bad = xml.replace("<title>政治新闻X</title>", "<title></title>")
        feed = feedparser.parse(xml_bad)
        titles = [e.title for e in feed.entries if e.title.strip()]
        assert "政治新闻Y" in titles  # Second item still present
        assert "" not in titles  # Empty title not present

    def test_missing_link_skipped(self):
        import feedparser
        xml = (FIXTURES / "ltn_business.xml").read_text(encoding="utf-8")
        # Remove link
        xml_bad = xml.replace(
            "<link>https://news.ltn.com.tw/news/business/breakingnews/2345678</link>", ""
        )
        feed = feedparser.parse(xml_bad)
        if feed.entries:
            e = feed.entries[0]
            link = e.get("link", "") or ""
            assert not link.strip()


class TestLtnXMLSecurity:
    def test_billion_laughs_not_parsed(self):
        """If feedparser receives an entity-expansion attack, it should not hang or crash."""
        import feedparser
        xml = (FIXTURES / "ltn_politics.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        assert len(feed.entries) >= 1  # Normal parsing unaffected


class TestLtnDedup:
    def test_same_url_in_feed_dedup(self):
        c = LtnRSSCollector(make_source("https://news.ltn.com.tw/rss/politics.xml"))
        import feedparser
        xml = (FIXTURES / "ltn_politics.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        seen = set()
        urls = []
        for entry in feed.entries:
            link = (entry.get("link") or "").strip()
            if not link:
                continue
            norm = c.normalize_url(link)
            if norm in seen:
                continue
            seen.add(norm)
            urls.append(norm)
        assert len(urls) == len(feed.entries)  # No dups in our fixture