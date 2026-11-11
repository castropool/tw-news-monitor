"""WSJRSSCollector tests — local fixtures only, no network access.

The source is disabled in config (enabled: false); the collector is fully
implemented and PAID-marked entries must parse normally without failure.
"""

import httpx

from app.collectors.wsj import WSJRSSCollector
from app.main import COLLECTOR_MAP
from app.time_utils import TAIPEI

FEED_URL = "https://feeds.a.dj.com/rss/RSSWorldNews.xml"


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
    """items: dicts with keys title/link/description/pub_date/paid (all optional)."""
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
        if it.get("paid"):
            parts.append(f"<AccessClassName>{it['paid']}</AccessClassName>")
        parts.append("</item>")
        entry_parts.append("".join(parts))
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<rss version="2.0"><channel>'
        "<title>WSJ World News</title>"
        "<link>https://feeds.a.dj.com/rss/RSSWorldNews.xml</link>"
        "<description>test feed</description>"
        + "".join(entry_parts)
        + "</channel></rss>"
    )
    return body.encode("utf-8")


def _collector(body: bytes, **source_extra) -> WSJRSSCollector:
    source = {
        "id": "wsj_international",
        "name": "Wall Street Journal",
        "type": "wsj_rss",
        "category": "international",
        "language": "en",
        "access_level": "metadata_only",
        "section": "world",
        "enabled": False,
        "url": FEED_URL,
    }
    source.update(source_extra)
    collector = WSJRSSCollector(source)
    collector._client = StaticClient(_response(body))
    return collector


# ── registration ────────────────────────────────────────────────────

def test_wsj_registered_in_collector_map():
    assert "wsj_rss" in COLLECTOR_MAP
    assert COLLECTOR_MAP["wsj_rss"] is WSJRSSCollector


# ── parsing ─────────────────────────────────────────────────────────

def test_wsj_parses_entries_with_metadata_only_fields():
    body = rss_xml(
        {
            "title": "World markets roundup",
            "link": "https://www.wsj.com/articles/world-markets-1234?mod=rss_WorldNews",
            "description": "Brief on global markets.",
            "pub_date": "Mon, 12 Jan 2026 12:00:00 -0500",
        },
    )
    articles = _collector(body).collect()

    assert len(articles) == 1
    a = articles[0]
    assert a.source_id == "wsj_international"
    assert a.source_name == "Wall Street Journal"
    assert a.category == "international"
    assert a.title == "World markets roundup"
    # ?mod= is not in TRACKING_PARAMS -> kept as-is (query values keep case);
    # scheme/host/path lowercased, trailing slash removed.
    assert a.url == "https://www.wsj.com/articles/world-markets-1234?mod=rss_WorldNews"
    assert a.summary == "Brief on global markets."
    assert a.summary_source == "rss"
    assert a.section == "world"  # from source config
    assert a.language == "en"
    assert a.access_level == "metadata_only"
    assert a.position == 1
    # 12:00 -0500 -> 17:00Z -> 01:00 Taipei next day (Jan 13)
    t = a.published_at.astimezone(TAIPEI)
    assert (t.month, t.day, t.hour) == (1, 13, 1)
    assert a.published_at.tzinfo is not None


def test_wsj_paid_marker_does_not_block():
    body = rss_xml(
        {
            "title": "PAID exclusive story",
            "link": "https://www.wsj.com/articles/paid-1",
            "description": "Paywalled teaser.",
            "pub_date": "Mon, 12 Jan 2026 12:00:00 -0500",
            "paid": "PAID",
        },
        {
            "title": "Another paid story",
            "link": "https://www.wsj.com/articles/paid-2",
            "description": "Also paywalled.",
            "pub_date": "Mon, 12 Jan 2026 13:00:00 -0500",
            "paid": "PAID",
        },
    )
    articles = _collector(body).collect()
    # PAID entries are collected normally — no failure, no workaround.
    assert len(articles) == 2
    assert [a.title for a in articles] == ["PAID exclusive story", "Another paid story"]


def test_wsj_missing_pubdate_yields_none():
    body = rss_xml(
        {
            "title": "No date",
            "link": "https://www.wsj.com/articles/no-date-1",
            "description": "Teaser without date.",
        },
    )
    articles = _collector(body).collect()
    assert len(articles) == 1
    assert articles[0].published_at is None


def test_wsj_missing_description_does_not_fail():
    body = rss_xml(
        {
            "title": "No teaser",
            "link": "https://www.wsj.com/articles/no-teaser-1",
            "pub_date": "Mon, 12 Jan 2026 12:00:00 -0500",
        },
    )
    articles = _collector(body).collect()
    assert len(articles) == 1
    assert articles[0].summary is None


def test_wsj_caps_at_max_items():
    items = [
        {
            "title": f"Story {i}",
            "link": f"https://www.wsj.com/articles/story-{i}",
            "description": f"Teaser {i}.",
            "pub_date": f"Mon, 12 Jan 2026 12:0{i % 10}:00 -0500",
        }
        for i in range(25)
    ]
    articles = _collector(rss_xml(*items)).collect()
    assert len(articles) == WSJRSSCollector.MAX_ITEMS
