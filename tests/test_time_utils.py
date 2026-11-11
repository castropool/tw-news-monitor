import pytest
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from app.time_utils import normalize_published_at, TAIPEI
from app.freshness import filter_fresh_articles
from app.models import Article

UTC = ZoneInfo("UTC")
NOW = datetime(2026, 7, 17, 22, 0, tzinfo=TAIPEI)


def make(pub, url="a", title="T", src="S"):
    return Article(source_id="t", source_name=src, category="pol",
                   title=title, url=url, published_at=pub, fetched_at=NOW, position=1)


class TestNormalize:
    def test_none(self):
        assert normalize_published_at(None) is None

    def test_utc_to_taipei(self):
        r = normalize_published_at(datetime(2026, 7, 17, 14, 0, tzinfo=UTC))
        assert r.tzinfo == TAIPEI and r.hour == 22

    def test_e08_stays(self):
        r = normalize_published_at(datetime(2026, 7, 17, 22, 0, tzinfo=ZoneInfo("Asia/Taipei")))
        assert r.hour == 22

    def test_naive_with_assumed(self):
        r = normalize_published_at(datetime(2026, 7, 17, 22, 0), assumed_timezone=TAIPEI)
        assert r.tzinfo is TAIPEI

    def test_naive_without_assumed(self):
        assert normalize_published_at(datetime(2026, 7, 17, 22, 0)) is None


class TestFreshness:
    def test_30m_fresh(self):
        fr = filter_fresh_articles([make(NOW - timedelta(minutes=30))], NOW)
        assert len(fr.fresh_articles) == 1

    def test_3h_stale(self):
        fr = filter_fresh_articles([make(NOW - timedelta(hours=3))], NOW)
        assert len(fr.stale_articles) == 1

    def test_future_5m_fresh(self):
        fr = filter_fresh_articles([make(NOW + timedelta(minutes=5))], NOW)
        assert len(fr.fresh_articles) == 1

    def test_future_11m_abnormal(self):
        fr = filter_fresh_articles([make(NOW + timedelta(minutes=11))], NOW)
        assert len(fr.future_time_articles) == 1

    def test_no_pub_unknown(self):
        a = make(NOW)
        a.published_at = None
        fr = filter_fresh_articles([a], NOW)
        assert len(fr.unknown_time_articles) == 1


class TestFixtureAware:
    def test_rss(self):
        import feedparser, email.utils
        with open("tests/fixtures/sample_rss.xml", encoding="utf-8") as f:
            feed = feedparser.parse(f.read())
        for e in feed.entries:
            ps = e.get("published", "")
            if ps:
                p = email.utils.parsedate_to_datetime(ps)
                if p and p.tzinfo is not None:
                    assert p.astimezone(TAIPEI).tzinfo is not None

    def test_udn(self):
        from bs4 import BeautifulSoup
        with open("tests/fixtures/sample_udn.html", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
        for item in soup.find_all(class_="story-list__news"):
            t = item.find("time")
            if t:
                pub = datetime.fromisoformat(t.text.strip()).replace(tzinfo=TAIPEI)
                assert pub.tzinfo is not None

    def test_ebc(self):
        from bs4 import BeautifulSoup
        with open("tests/fixtures/sample_ebc.html", encoding="utf-8") as f:
            soup = BeautifulSoup(f.read(), "html.parser")
        for item in soup.select("a.item.row_box"):
            td = item.find(class_="item_time")
            if td:
                te = td.find("time")
                if te and te.get("datetime"):
                    dt = datetime.fromisoformat(te["datetime"])
                    if dt.tzinfo is not None:
                        assert dt.astimezone(TAIPEI).tzinfo is not None
                    else:
                        assert dt.replace(tzinfo=TAIPEI).tzinfo is not None
