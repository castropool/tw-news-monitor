"""ReutersCollector tests — local fixtures only, no network access.

Fixtures are inline XML bytes served through a routing static client,
mirroring the StaticClient pattern of test_zaobao.py.
"""

import httpx
from pathlib import Path

from app.collectors.reuters import ReutersCollector
from app.main import COLLECTOR_MAP
from app.time_utils import TAIPEI

INDEX_URL = "https://www.reuters.com/arc/outboundfeeds/news-sitemap-index/?outputType=xml"
PAGE_URL = "https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml"
PAGE_URL_2 = "https://www.reuters.com/arc/outboundfeeds/news-sitemap/?outputType=xml&from=100"
OTHER_SITEMAP_URL = "https://www.reuters.com/sitemap.xml"
FIXTURE_DIR = Path(__file__).parent / "fixtures" / "international"

_NS = (
    'xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
    'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9"'
)


class RoutingClient:
    """Returns a per-URL response; records every requested URL."""

    def __init__(self, responses: dict[str, httpx.Response]):
        self.responses = responses
        self.requested: list[str] = []

    def get(self, url: str):
        self.requested.append(url)
        return self.responses[url]

    def close(self):
        return None


def _resp(body: bytes, url: str) -> httpx.Response:
    return httpx.Response(
        200,
        content=body,
        headers={"content-type": "application/xml; charset=utf-8"},
        request=httpx.Request("GET", url),
    )


def _xml_escape(value: str) -> str:
    """Escape XML special chars (sitemap locs may contain & in query strings)."""
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def index_xml(*locs: str) -> bytes:
    entries = "".join(
        f"<sitemap><loc>{_xml_escape(loc)}</loc></sitemap>" for loc in locs
    )
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<sitemapindex {_NS}>{entries}</sitemapindex>'
    )
    return body.encode("utf-8")


def page_xml(*entries) -> bytes:
    """entries: dicts with keys loc/title/date/lastmod (date wins over lastmod)."""
    parts = []
    for e in entries:
        inner = f"<loc>{_xml_escape(e['loc'])}</loc>"
        if e.get("date"):
            inner += (
                "<news:news>"
                f"<news:publication_date>{e['date']}</news:publication_date>"
                f"<news:title>{e['title']}</news:title>"
                "</news:news>"
            )
        else:
            inner += f"<news:news><news:title>{e['title']}</news:title></news:news>"
        if e.get("lastmod"):
            inner += f"<lastmod>{e['lastmod']}</lastmod>"
        parts.append(f"<url>{inner}</url>")
    body = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        f'<urlset {_NS}>{"".join(parts)}</urlset>'
    )
    return body.encode("utf-8")


def _collector(client: RoutingClient, **source_extra) -> ReutersCollector:
    source = {
        "id": "reuters_international",
        "name": "Reuters",
        "type": "reuters",
        "category": "international",
        "language": "en",
        "access_level": "metadata_only",
        "url": INDEX_URL,
    }
    source.update(source_extra)
    collector = ReutersCollector(source)
    collector._client = client
    return collector


def _standard_client(entries=None) -> RoutingClient:
    """Index with a single news-sitemap page (no duplicate, English only)."""
    if entries is None:
        entries = [
            {
                "loc": "https://www.reuters.com/world/china/china-1/",
                "title": "China markets story",
                "date": "2026-08-13T01:00:00Z",
            },
            {
                "loc": "https://www.reuters.com/business/biz-1/",
                "title": "Business story",
                "date": "2026-08-13T02:00:00Z",
            },
        ]
    return RoutingClient({
        INDEX_URL: _resp(index_xml(PAGE_URL), INDEX_URL),
        PAGE_URL: _resp(page_xml(*entries), PAGE_URL),
    })


# ── registration ────────────────────────────────────────────────────

def test_reuters_registered_in_collector_map():
    assert "reuters" in COLLECTOR_MAP
    assert COLLECTOR_MAP["reuters"] is ReutersCollector


def test_reuters_official_structure_fixture():
    client = RoutingClient({
        INDEX_URL: _resp(
            (FIXTURE_DIR / "reuters_news_sitemap_index.xml").read_bytes(),
            INDEX_URL,
        ),
        PAGE_URL: _resp(
            (FIXTURE_DIR / "reuters_news_sitemap.xml").read_bytes(),
            PAGE_URL,
        ),
    })

    articles = _collector(client).collect()

    assert len(articles) == 3  # tracking-parameter duplicate removed
    assert articles[0].title == "China launches drills near Taiwan"
    assert articles[0].section == "China"
    assert articles[0].summary is None
    assert articles[0].access_level == "metadata_only"
    assert articles[1].published_at is not None  # lastmod fallback
    assert articles[2].title == "European football result"  # old items are kept


# ── happy path ──────────────────────────────────────────────────────

def test_reuters_parses_articles_with_sections_and_fields():
    entries = [
        {"loc": "https://www.reuters.com/world/china/abc-1/", "title": "China story",
         "date": "2026-08-13T01:00:00Z"},
        {"loc": "https://www.reuters.com/business/finance-1/", "title": "Business story",
         "date": "2026-08-13T02:00:00Z"},
        {"loc": "https://www.reuters.com/world/europe/eu-1/", "title": "World story",
         "date": "2026-08-13T03:00:00Z"},
        {"loc": "https://www.reuters.com/markets/market-1/", "title": "Markets story",
         "date": "2026-08-13T04:00:00Z"},
        {"loc": "https://www.reuters.com/technology/tech-1/", "title": "Tech story",
         "date": "2026-08-13T05:00:00Z"},
        {"loc": "https://www.reuters.com/sports/tennis-1/", "title": "Sports story",
         "date": "2026-08-13T06:00:00Z"},
        {"loc": "https://www.reuters.com/legal/law-1/", "title": "Legal story",
         "date": "2026-08-13T07:00:00Z"},
        {"loc": "https://www.reuters.com/commentary/opinion-1/", "title": "Commentary story",
         "date": "2026-08-13T08:00:00Z"},
    ]
    articles = _collector(_standard_client(entries)).collect()

    assert len(articles) == 8
    first = articles[0]
    assert first.source_id == "reuters_international"
    assert first.source_name == "Reuters"
    assert first.category == "international"
    assert first.title == "China story"
    # trailing slash stripped + host lowercased by normalize_url
    assert first.url == "https://www.reuters.com/world/china/abc-1"
    assert first.language == "en"
    assert first.access_level == "metadata_only"
    assert first.summary is None
    assert first.summary_source is None
    assert first.position == 1
    assert first.published_at is not None
    assert first.published_at.tzinfo is not None
    assert first.published_at.astimezone(TAIPEI).hour == 9  # 01:00Z -> 09:00 Taipei

    expected_sections = ["China", "Business", "world", "Markets",
                         "Technology", "Sports", "Legal", "Commentary"]
    assert [a.section for a in articles] == expected_sections
    assert [a.position for a in articles] == list(range(1, 9))
    assert [a.language for a in articles] == ["en"] * 8
    assert [a.access_level for a in articles] == ["metadata_only"] * 8


def test_reuters_falls_back_to_lastmod():
    entries = [
        {"loc": "https://www.reuters.com/world/fallback-1/", "title": "No pub date",
         "lastmod": "2026-08-12T20:30:00Z"},
    ]
    articles = _collector(_standard_client(entries)).collect()
    assert len(articles) == 1
    dt = articles[0].published_at
    assert dt.astimezone(TAIPEI).year == 2026
    assert dt.astimezone(TAIPEI).month == 8
    assert dt.astimezone(TAIPEI).day == 13
    assert dt.astimezone(TAIPEI).hour == 4
    assert dt.astimezone(TAIPEI).minute == 30


def test_reuters_time_format_variations_all_aware_taipei():
    entries = [
        {"loc": "https://www.reuters.com/world/z-1/", "title": "UTC Z",
         "date": "2026-08-13T01:02:03Z"},
        {"loc": "https://www.reuters.com/world/offset-1/", "title": "Offset +08",
         "date": "2026-08-13T09:02:03+08:00"},
        {"loc": "https://www.reuters.com/world/naive-1/", "title": "Naive ISO",
         "date": "2026-08-13T01:02:03"},
    ]
    articles = _collector(_standard_client(entries)).collect()
    assert len(articles) == 3
    for a in articles:
        assert a.published_at is not None
        assert a.published_at.tzinfo is not None
        taipei = a.published_at.astimezone(TAIPEI)
        assert (taipei.hour, taipei.minute, taipei.second) == (9, 2, 3)


# ── filtering ───────────────────────────────────────────────────────

def test_reuters_deduplicates_duplicate_locs():
    entries = [
        {"loc": "https://www.reuters.com/world/dup-1/", "title": "First",
         "date": "2026-08-13T01:00:00Z"},
        {"loc": "https://www.reuters.com/world/dup-1/", "title": "First again",
         "date": "2026-08-13T01:00:00Z"},
    ]
    articles = _collector(_standard_client(entries)).collect()
    assert len(articles) == 1
    assert articles[0].title == "First"


def test_reuters_skips_non_english_path_prefixes():
    entries = [
        {"loc": f"https://www.reuters.com{prefix}noticia-1/", "title": f"Lang {prefix}",
         "date": "2026-08-13T01:00:00Z"}
        for prefix in ("/pt/", "/es/", "/fr/", "/de/", "/it/", "/jp/", "/latam/")
    ]
    entries.append({"loc": "https://www.reuters.com/world/uk-1/", "title": "English",
                    "date": "2026-08-13T01:00:00Z"})
    articles = _collector(_standard_client(entries)).collect()
    assert [a.title for a in articles] == ["English"]


def test_reuters_skips_placeholder_titles():
    entries = [
        {"loc": "https://www.reuters.com/world/s-1/", "title": "Markets Daily Summary",
         "date": "2026-08-13T01:00:00Z"},
        {"loc": "https://www.reuters.com/world/s-2/", "title": "Weekend Roundup Summary",
         "date": "2026-08-13T01:00:00Z"},
        {"loc": "https://www.reuters.com/world/o-1/", "title": "OFR: rates placeholder",
         "date": "2026-08-13T01:00:00Z"},
        {"loc": "https://www.reuters.com/world/o-2/", "title": "Policy decision OFR preview",
         "date": "2026-08-13T01:00:00Z"},
        {"loc": "https://www.reuters.com/world/ok-1/", "title": "Real news story",
         "date": "2026-08-13T01:00:00Z"},
    ]
    articles = _collector(_standard_client(entries)).collect()
    assert [a.title for a in articles] == ["Real news story"]


def test_reuters_skips_empty_title_or_loc():
    entries = [
        {"loc": "", "title": "No loc", "date": "2026-08-13T01:00:00Z"},
        {"loc": "https://www.reuters.com/world/no-title-1/", "title": "",
         "date": "2026-08-13T01:00:00Z"},
        {"loc": "https://www.reuters.com/world/ok-1/", "title": "Kept",
         "date": "2026-08-13T01:00:00Z"},
    ]
    articles = _collector(_standard_client(entries)).collect()
    assert [a.title for a in articles] == ["Kept"]


def test_reuters_skips_entries_without_time():
    entries = [
        {"loc": "https://www.reuters.com/world/no-time-1/", "title": "No time at all"},
        {"loc": "https://www.reuters.com/world/bad-time-1/", "title": "Garbage time",
         "date": "not-a-date"},
        {"loc": "https://www.reuters.com/world/ok-1/", "title": "Kept",
         "date": "2026-08-13T01:00:00Z"},
    ]
    articles = _collector(_standard_client(entries)).collect()
    assert [a.title for a in articles] == ["Kept"]


def test_reuters_old_article_is_kept_with_aware_time():
    entries = [
        {"loc": "https://www.reuters.com/world/old-1/", "title": "Old article",
         "date": "2025-01-01T00:00:00Z"},
    ]
    articles = _collector(_standard_client(entries)).collect()
    assert len(articles) == 1
    dt = articles[0].published_at
    assert dt.year == 2025
    assert dt.tzinfo is not None
    assert dt.astimezone(TAIPEI).hour == 8  # 00:00Z -> 08:00 Taipei


def test_reuters_section_fallback_from_source_config_or_none():
    entries = [
        {"loc": "https://www.reuters.com/odd/unknown-1/", "title": "Unknown section",
         "date": "2026-08-13T01:00:00Z"},
    ]
    with_cfg = _collector(_standard_client(entries), section="world").collect()
    assert with_cfg[0].section == "world"
    without_cfg = _collector(_standard_client(entries)).collect()
    assert without_cfg[0].section is None


# ── pagination / caps ───────────────────────────────────────────────

def test_reuters_caps_at_max_items_single_page():
    entries = [
        {"loc": f"https://www.reuters.com/world/cap-{i}/", "title": f"Story {i}",
         "date": f"2026-08-13T{(1 + i) % 24:02d}:00:00Z"}
        for i in range(25)
    ]
    client = _standard_client(entries)
    articles = _collector(client).collect()
    assert len(articles) == ReutersCollector.MAX_ITEMS
    assert client.requested == [INDEX_URL, PAGE_URL]


def test_reuters_fetches_more_pages_until_max_items():
    page1 = [
        {"loc": f"https://www.reuters.com/world/p1-{i}/", "title": f"P1 {i}",
         "date": f"2026-08-13T{(1 + i) % 24:02d}:00:00Z"}
        for i in range(15)
    ]
    page2 = [
        {"loc": f"https://www.reuters.com/world/p2-{i}/", "title": f"P2 {i}",
         "date": f"2026-08-13T{(1 + i) % 24:02d}:00:00Z"}
        for i in range(10)
    ]
    client = RoutingClient({
        INDEX_URL: _resp(index_xml(PAGE_URL, PAGE_URL_2), INDEX_URL),
        PAGE_URL: _resp(page_xml(*page1), PAGE_URL),
        PAGE_URL_2: _resp(page_xml(*page2), PAGE_URL_2),
    })
    articles = _collector(client).collect()
    assert len(articles) == ReutersCollector.MAX_ITEMS
    assert client.requested == [INDEX_URL, PAGE_URL, PAGE_URL_2]
    assert articles[0].title == "P1 0"
    assert articles[15].title == "P2 0"
    assert [a.position for a in articles] == list(range(1, 21))


def test_reuters_ignores_non_news_sitemap_index_entries():
    entries = [
        {"loc": "https://www.reuters.com/world/ok-1/", "title": "Kept",
         "date": "2026-08-13T01:00:00Z"},
    ]
    client = RoutingClient({
        INDEX_URL: _resp(index_xml(OTHER_SITEMAP_URL, PAGE_URL), INDEX_URL),
        PAGE_URL: _resp(page_xml(*entries), PAGE_URL),
    })
    articles = _collector(client).collect()
    assert len(articles) == 1
    assert OTHER_SITEMAP_URL not in client.requested


def test_reuters_empty_index_returns_empty():
    client = RoutingClient({INDEX_URL: _resp(index_xml(), INDEX_URL)})
    assert _collector(client).collect() == []
