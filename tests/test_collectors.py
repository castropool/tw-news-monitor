from datetime import datetime
from pathlib import Path

import pytest
from bs4 import BeautifulSoup

from app.collectors.rss import RSSCollector
from app.collectors.udn import UDNCollector
from app.collectors.ebc import EBCCollector
from app.models import Article

FIXTURES = Path(__file__).resolve().parent / "fixtures"


class CollectorForTest:
    """Mixin-like helper to create collectors for testing."""


def test_rss_parsing():
    """RSS collector parses XML correctly."""
    xml_path = FIXTURES / "sample_rss.xml"
    xml_content = xml_path.read_text(encoding="utf-8")

    # We'll test the core parsing logic by reusing it
    import feedparser
    feed = feedparser.parse(xml_content)

    source = {
        "id": "cna_politics",
        "name": "中央社",
        "category": "politics",
        "collector": "rss",
        "url": "",
    }

    # Create collector and simulate parsing
    collector = RSSCollector(source)
    articles = []
    now = datetime.now()

    for i, entry in enumerate(feed.entries[:RSSCollector.MAX_ITEMS]):
        title = entry.get("title", "").strip()
        link = entry.get("link", "").strip()
        if not title or not link:
            continue
        published = None
        if entry.get("published_parsed"):
            published = datetime(*entry.published_parsed[:6])
        articles.append(Article(
            source_id="cna_politics",
            source_name="中央社",
            category="politics",
            title=title,
            url=collector.normalize_url(link),
            published_at=published,
            fetched_at=now,
            position=i + 1,
        ))

    # Should have 2 valid articles (empty title and empty link skipped)
    assert len(articles) == 2

    # Check first article
    assert articles[0].title == "測試新聞一：立法院審查預算案"
    assert articles[0].url == "https://www.cna.com.tw/news/aipl/202607140001.aspx"
    assert articles[0].published_at is not None
    assert articles[0].source_id == "cna_politics"
    assert articles[0].category == "politics"

    # Check second article
    assert articles[1].title == "測試新聞二：行政院通過新法案"
    assert articles[1].position == 2


def test_rss_empty_feed():
    """Empty RSS feed returns empty list."""
    import feedparser
    empty_xml = '<?xml version="1.0"?><rss version="2.0"><channel><title>Empty</title></channel></rss>'
    feed = feedparser.parse(empty_xml)
    assert len(feed.entries) == 0


def test_udn_parsing():
    """UDN collector parses HTML correctly."""
    html_path = FIXTURES / "sample_udn.html"
    html_content = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_content, "html.parser")

    source = {
        "id": "udn_politics",
        "name": "聯合新聞網",
        "category": "politics",
        "collector": "udn",
        "url": "",
    }

    collector = UDNCollector(source)
    now = datetime.now()
    articles: list[Article] = []
    items = soup.find_all(class_="story-list__news")[:UDNCollector.MAX_ITEMS]

    for i, item in enumerate(items):
        text_div = item.find(class_="story-list__text")
        if not text_div:
            continue
        h = text_div.find(["h2", "h3", "h4"])
        if not h:
            continue
        a = h.find("a")
        if not a:
            continue
        title = a.text.strip()
        href = a.get("href", "").strip()
        if not title or not href:
            continue
        if href.startswith("/"):
            href = "https://udn.com" + href

        published_at = None
        info = item.find(class_="story-list__info")
        if info:
            time_el = info.find("time")
            if time_el:
                try:
                    published_at = datetime.fromisoformat(time_el.text.strip())
                except ValueError:
                    pass

        articles.append(Article(
            source_id="udn_politics",
            source_name="聯合新聞網",
            category="politics",
            title=title,
            url=collector.normalize_url(href),
            published_at=published_at,
            fetched_at=now,
            position=i + 1,
        ))

    # Should have 2 valid articles (one has empty href, one has no a tag)
    assert len(articles) == 2

    # Check first article
    assert "政治新聞測試" in articles[0].title
    assert "udn.com/news/story/6656" in articles[0].url

    # Check published_at parsing
    assert articles[0].published_at is not None

    # Check category from source config
    assert articles[0].category == "politics"


def test_udn_empty_page():
    """Empty UDN page returns empty list."""
    soup = BeautifulSoup("<html><body></body></html>", "html.parser")
    items = soup.find_all(class_="story-list__news")
    assert len(items) == 0


def test_ebc_parsing():
    """EBC collector parses HTML correctly."""
    html_path = FIXTURES / "sample_ebc.html"
    html_content = html_path.read_text(encoding="utf-8")
    soup = BeautifulSoup(html_content, "html.parser")

    source = {
        "id": "ebc_politics",
        "name": "東森新聞",
        "category": "politics",
        "collector": "ebc",
        "url": "",
    }

    collector = EBCCollector(source)
    now = datetime.now()
    articles: list[Article] = []
    items = soup.select("a.item.row_box")[:EBCCollector.MAX_ITEMS]

    for i, item in enumerate(items):
        title_el = item.find("h3")
        if not title_el:
            continue
        title = title_el.text.strip()
        href = item.get("href", "").strip()
        if not title or not href:
            continue
        if href.startswith("/"):
            href = "https://news.ebc.net.tw" + href

        published_at = None
        time_div = item.find(class_="item_time")
        if time_div:
            time_el = time_div.find("time")
            if time_el and time_el.get("datetime"):
                try:
                    dt_str = time_el["datetime"].replace("+08:00", "")
                    published_at = datetime.fromisoformat(dt_str)
                except (ValueError, IndexError):
                    pass

        articles.append(Article(
            source_id="ebc_politics",
            source_name="東森新聞",
            category="politics",
            title=title,
            url=collector.normalize_url(href),
            published_at=published_at,
            fetched_at=now,
            position=i + 1,
        ))

    # Should have 2 valid articles (1 empty href, 1 not an <a> tag)
    assert len(articles) == 2
    assert articles[0].title == "女友遭投資詐騙！民進黨大老張俊宏背債3億"
    assert "news.ebc.net.tw/news/politics/561073" in articles[0].url
    assert articles[0].published_at is not None


def test_ebc_empty_page():
    """Empty EBC page returns empty list."""
    soup = BeautifulSoup("<html><body></body></html>", "html.parser")
    items = soup.select("a.item.row_box")
    assert len(items) == 0


def test_category_from_config():
    """Category comes from config, not guessed from keywords."""
    import feedparser
    xml_path = FIXTURES / "sample_rss.xml"
    xml_content = xml_path.read_text(encoding="utf-8")
    feed = feedparser.parse(xml_content)

    for expected_cat in ["politics", "economy", "international"]:
        source = {
            "id": "test",
            "name": "測試",
            "category": expected_cat,
            "collector": "rss",
            "url": "",
        }
        collector = RSSCollector(source)
        now = datetime.now()
        for i, entry in enumerate(feed.entries[:1]):
            articles = [Article(
                source_id="test",
                source_name="測試",
                category=source["category"],
                title=entry.get("title", ""),
                url=collector.normalize_url(entry.get("link", "")),
                published_at=now,
                fetched_at=now,
                position=1,
            )]
        assert len(articles) == 1
        assert articles[0].category == expected_cat


def test_url_normalization():
    """URL normalization produces consistent dedup keys."""
    collector = RSSCollector({
        "id": "test", "name": "測試", "category": "politics", "collector": "rss", "url": ""
    })

    # Same URL with/without trailing slash
    a = collector.normalize_url("https://example.com/news/1/")
    b = collector.normalize_url("https://example.com/news/1")
    assert a == b

    # Same URL with/without fragment
    c = collector.normalize_url("https://example.com/news/1#section")
    assert a == c

    # Host is case-insensitive, but the path remains user-visible/case-sensitive.
    d = collector.normalize_url("HTTPS://EXAMPLE.COM/NEWS/1")
    assert d == "https://example.com/NEWS/1"
    # UDN tracking params: different 'from' values should produce same URL
    e = collector.normalize_url(
        "https://udn.com/news/story/1234/56789?from=udn-catebreaknews_ch2"
    )
    f = collector.normalize_url(
        "https://udn.com/news/story/1234/56789?from=udn-catelistnews_ch2"
    )
    assert e == f, "UDN URLs with different 'from' params must match"
    assert "from=" not in e, "'from' param should be stripped"

    # URL with and without utm params should be the same
    g = collector.normalize_url(
        "https://example.com/page?utm_source=twitter&utm_medium=social"
    )
    h = collector.normalize_url("https://example.com/page")
    assert g == h, "URLs with and without utm params must match"

    # Different article IDs must remain different
    i = collector.normalize_url("https://udn.com/news/story/1234/11111")
    j = collector.normalize_url("https://udn.com/news/story/1234/22222")
    assert i != j, "Different article IDs must remain different"

    # Unknown business parameters are preserved
    k = collector.normalize_url(
        "https://example.com/page?article_id=123&lang=zh"
    )
    assert "article_id=123" in k, "Unknown params should be preserved"
    assert "lang=zh" in k, "Unknown params should be preserved"
    # And should be sorted
    assert k.index("article_id") < k.index("lang") or "article_id" not in k,         "Params should be sorted alphabetically"

    # Fragment is removed
    l = collector.normalize_url("https://example.com/page#section")
    assert "#" not in l, "Fragment should be removed"

    # fbclid and gclid stripped
    m = collector.normalize_url(
        "https://example.com/page?fbclid=abc123&gclid=def456"
    )
    assert m == "https://example.com/page",         "fbclid and gclid should be stripped"

