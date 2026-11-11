"""FTAlphavilleCollector tests — local fixtures only, no network access."""

import httpx
from pathlib import Path

from app.collectors.ft_alphaville import FTAlphavilleCollector
from app.main import COLLECTOR_MAP
from app.time_utils import TAIPEI

FEED_URL = "https://www.ft.com/alphaville?format=rss"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "international"


class StaticClient:
    def __init__(self, response: httpx.Response):
        self.response = response

    def get(self, _url):
        return self.response

    def close(self):
        return None


def _response(body: bytes) -> httpx.Response:
    return httpx.Response(
        200,
        content=body,
        headers={"content-type": "application/rss+xml; charset=utf-8"},
        request=httpx.Request("GET", FEED_URL),
    )


def rss_xml(*items) -> bytes:
    """items: dicts with keys title/link/description/pub_date (all optional)."""
    entry_parts = []
    for it in items:
        parts = ["<item>"]
        if it.get("title"):
            parts.append(f"<title>{it['title']}</title>")
        if it.get("link"):
            parts.append(f"<link>{it['link']}</link>")
        if it.get("description"):
            parts.append(f"<description>{it['description']}</description>")
        if it.get("pub_date"):
            parts.append(f"<pubDate>{it['pub_date']}</pubDate>")
        parts.append("</item>")
        entry_parts.append("".join(parts))
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        "<title>FT Alphaville</title>"
        "<link>https://ftalphaville.ft.com/</link>"
        "<description>test feed</description>"
        + "".join(entry_parts)
        + "</channel></rss>"
    )
    return body.encode("utf-8")


def _collector(body: bytes, **source_extra) -> FTAlphavilleCollector:
    source = {
        "id": "ft_alphaville",
        "name": "Financial Times",
        "type": "ft_alphaville",
        "category": "international",
        "language": "en",
        "access_level": "public",
        "section": "alphaville",
        "url": FEED_URL,
    }
    source.update(source_extra)
    collector = FTAlphavilleCollector(source)
    collector._client = StaticClient(_response(body))
    return collector


# ── registration ────────────────────────────────────────────────────

def test_ft_alphaville_registered_in_collector_map():
    assert "ft_alphaville" in COLLECTOR_MAP
    assert COLLECTOR_MAP["ft_alphaville"] is FTAlphavilleCollector


def test_ft_official_structure_fixture():
    body = (FIXTURE_DIR / "ft_alphaville_feed.xml").read_bytes()

    articles = _collector(body).collect()

    assert len(articles) == 2
    assert articles[0].title == "TSMC & the next semiconductor cycle"
    assert "Taiwan's largest chipmaker" in articles[0].summary
    assert articles[0].url == "https://www.ft.com/content/11111111-1111-1111-1111-111111111111"
    assert articles[0].section == "Alphaville"
    assert articles[1].summary is None
    assert articles[1].published_at is not None  # old items remain available to freshness


# ── parsing ─────────────────────────────────────────────────────────

def test_ft_parses_entries_with_summary_and_taipei_time():
    body = rss_xml(
        {
            "title": "Alpha trading note",
            "link": "https://ftalphaville.ft.com/2026/08/13/alpha/",
            "description": "Short teaser about markets.",
            "pub_date": "Mon, 13 Aug 2026 08:00:00 -0500",
        },
        {
            "title": "Beta rates commentary",
            "link": "https://ftalphaville.ft.com/2026/08/13/beta/",
            "description": "Another teaser.",
            "pub_date": "Sun, 14 Jun 2026 12:00:00 -0400",  # US Eastern DST-era offset
        },
    )
    articles = _collector(body).collect()

    assert len(articles) == 2
    a0, a1 = articles
    assert a0.source_id == "ft_alphaville"
    assert a0.source_name == "Financial Times"
    assert a0.category == "international"
    assert a0.title == "Alpha trading note"
    assert a0.url == "https://ftalphaville.ft.com/2026/08/13/alpha"
    assert a0.summary == "Short teaser about markets."
    assert a0.summary_source == "rss"
    assert a0.section == "Alphaville"
    assert a0.language == "en"
    assert a0.access_level == "public"
    assert a0.position == 1

    # 08:00 -0500 -> 13:00Z -> 21:00 Taipei (same day)
    t0 = a0.published_at.astimezone(TAIPEI)
    assert (t0.month, t0.day, t0.hour) == (8, 13, 21)
    # 12:00 -0400 -> 16:00Z -> 00:00 Taipei next day (Jun 15)
    t1 = a1.published_at.astimezone(TAIPEI)
    assert (t1.month, t1.day, t1.hour) == (6, 15, 0)
    assert all(a.published_at.tzinfo is not None for a in articles)


def test_ft_missing_description_does_not_fail():
    body = rss_xml(
        {
            "title": "No teaser article",
            "link": "https://ftalphaville.ft.com/2026/08/13/no-teaser/",
            "pub_date": "Mon, 13 Aug 2026 08:00:00 -0500",
        },
    )
    articles = _collector(body).collect()
    assert len(articles) == 1
    assert articles[0].summary is None
    assert articles[0].summary_source is None


def test_ft_html_entities_are_decoded():
    body = rss_xml(
        {
            "title": "FT &amp; Co: &lt;test&gt; note",
            "link": "https://ftalphaville.ft.com/2026/08/13/entities/",
            "description": "Rates &amp; yields move together.",
            "pub_date": "Mon, 13 Aug 2026 08:00:00 -0500",
        },
    )
    articles = _collector(body).collect()
    assert len(articles) == 1
    assert articles[0].title == "FT & Co: <test> note"
    assert articles[0].summary == "Rates & yields move together."


def test_ft_old_article_is_kept():
    body = rss_xml(
        {
            "title": "Old article",
            "link": "https://ftalphaville.ft.com/2025/01/01/old/",
            "description": "Old teaser.",
            "pub_date": "Wed, 01 Jan 2025 12:00:00 -0500",
        },
    )
    articles = _collector(body).collect()
    assert len(articles) == 1
    assert articles[0].published_at.year == 2025
    assert articles[0].published_at.astimezone(TAIPEI).hour == 1  # 17:00Z -> 01:00 next day


def test_ft_missing_pubdate_yields_none():
    body = rss_xml(
        {
            "title": "No date",
            "link": "https://ftalphaville.ft.com/2026/08/13/no-date/",
            "description": "Teaser without date.",
        },
    )
    articles = _collector(body).collect()
    assert len(articles) == 1
    assert articles[0].published_at is None


def test_ft_caps_at_max_items():
    items = [
        {
            "title": f"Story {i}",
            "link": f"https://ftalphaville.ft.com/2026/08/13/story-{i}/",
            "description": f"Teaser {i}.",
            "pub_date": f"Mon, 13 Aug 2026 08:0{i % 10}:00 -0500",
        }
        for i in range(25)
    ]
    articles = _collector(rss_xml(*items)).collect()
    assert len(articles) == FTAlphavilleCollector.MAX_ITEMS


def test_ft_skips_empty_title_or_link():
    body = rss_xml(
        {"title": "", "link": "https://ftalphaville.ft.com/x/",
         "pub_date": "Mon, 13 Aug 2026 08:00:00 -0500"},
        {"title": "No link", "link": "",
         "pub_date": "Mon, 13 Aug 2026 08:00:00 -0500"},
        {"title": "Kept", "link": "https://ftalphaville.ft.com/kept/",
         "description": "kept", "pub_date": "Mon, 13 Aug 2026 08:00:00 -0500"},
    )
    articles = _collector(body).collect()
    assert [a.title for a in articles] == ["Kept"]
