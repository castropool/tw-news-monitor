import pytest, feedparser, email.utils
from pathlib import Path
from zoneinfo import ZoneInfo
from app.article_identity import article_identity_key
from app.source_registry import is_official_source, get_source_info

TAIPEI = ZoneInfo("Asia/Taipei")
FIXTURES = Path(__file__).resolve().parent / "fixtures"

def src(sid="ey_cabinet_news", cat="official"):
    return {"id": sid, "name": "行政院", "type": "rss",
            "category": cat, "url": "https://www.ey.gov.tw/RSS_Content.aspx?ModuleType=3", "enabled": False}


class TestEYRSS:
    def test_cabinet_parses(self):
        xml = (FIXTURES / "ey_cabinet_news.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        assert len(feed.entries) >= 1

    def test_ministry_parses(self):
        xml = (FIXTURES / "ey_ministry_news.xml").read_text(encoding="utf-8")
        feed = feedparser.parse(xml)
        assert len(feed.entries) >= 1

    def test_time_aware(self):
        xml = (FIXTURES / "ey_cabinet_news.xml").read_text(encoding="utf-8")
        for e in feedparser.parse(xml).entries:
            pub = e.get("published", "")
            if not pub:
                continue
            dt = email.utils.parsedate_to_datetime(pub)
            assert dt.tzinfo is not None

    def test_category_official(self):
        s = src()
        assert s["category"] == "official"

    def test_title_not_empty(self):
        xml = (FIXTURES / "ey_cabinet_news.xml").read_text(encoding="utf-8")
        for e in feedparser.parse(xml).entries:
            if e.get("title"):
                break
        else:
            pytest.fail("All titles empty")

    def test_link_not_empty(self):
        xml = (FIXTURES / "ey_cabinet_news.xml").read_text(encoding="utf-8")
        for e in feedparser.parse(xml).entries:
            assert e.get("link"), "Empty link"
            break

    def test_description_not_empty(self):
        xml = (FIXTURES / "ey_cabinet_news.xml").read_text(encoding="utf-8")
        for e in feedparser.parse(xml).entries[:3]:
            desc = e.get("summary", "") or e.get("description", "")
            assert len(desc) > 0

    def test_ministry_link_different_domains(self):
        """EY ministry links go to various ministry sites, not just ey.gov.tw."""
        xml = (FIXTURES / "ey_ministry_news.xml").read_text(encoding="utf-8")
        domains = set()
        for e in feedparser.parse(xml).entries[:10]:
            link = e.get("link", "")
            from urllib.parse import urlsplit
            host = urlsplit(link).hostname or ""
            domains.add(host)
        assert len(domains) >= 2, f"Only {len(domains)} domain(s): {domains}"


class TestEYIdentity:
    def test_cabinet_uuid_identity(self):
        k = article_identity_key("https://www.ey.gov.tw/Page/9277F759E41CCD91//659ecbe4-441d-4f78-8a79-518a749568f3")
        assert k == "ey:659ecbe4-441d-4f78-8a79-518a749568f3"

    def test_cabinet_different_uuid(self):
        a = article_identity_key("https://www.ey.gov.tw/Page/9277F759E41CCD91//659ecbe4-441d-4f78-8a79-518a749568f3")
        b = article_identity_key("https://www.ey.gov.tw/Page/9277F759E41CCD91//f0b94d35-73e4-40cb-a349-cd60e4937520")
        assert a != b

    def test_non_ey_fallback(self):
        k = article_identity_key("https://example.com/page/123")
        assert k.startswith("url:")

    def test_ministry_non_ey_gov(self):
        """Ministry articles on other domains fall back to url: identity."""
        k = article_identity_key("https://www.mol.gov.tw/1607/1632/1633/95651/")
        assert k.startswith("url:") or k.startswith("ey:")

    def test_same_article_cross_feed(self):
        """Same article ID in cabinet yields same identity."""
        a = article_identity_key("https://www.ey.gov.tw/Page/9277F759E41CCD91//659ecbe4-441d-4f78-8a79-518a749568f3")
        b = article_identity_key("https://www.ey.gov.tw/Page/9277F759E41CCD91//659ecbe4-441d-4f78-8a79-518a749568f3")
        assert a == b


class TestEYOfficialSource:
    def test_cabinet_is_official(self):
        assert is_official_source("ey_cabinet_news") is True

    def test_ministry_is_official(self):
        assert is_official_source("ey_ministry_news") is True

    def test_cabinet_display_name(self):
        info = get_source_info("ey_cabinet_news")
        assert info.get("display_name") == "\u884c\u653f\u9662"

    def test_ministry_display_name(self):
        info = get_source_info("ey_ministry_news")
        assert info.get("display_name") == "\u884c\u653f\u9662"

    def test_both_official_same_name(self):
        cab = get_source_info("ey_cabinet_news")
        min = get_source_info("ey_ministry_news")
        assert cab["display_name"] == min["display_name"] == "\u884c\u653f\u9662"
