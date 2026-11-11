from pathlib import Path

import httpx

from app.collectors.zaobao import ZaobaoCollector
from app.main import COLLECTOR_MAP
from app.time_utils import TAIPEI


FIXTURES = Path(__file__).resolve().parent / "fixtures"


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
        headers={"content-type": "application/xml; charset=utf-8"},
        request=httpx.Request("GET", "https://www.zaobao.com.sg/googlenews.xml"),
    )


def _collector(path_prefix: str = "/news/world/") -> ZaobaoCollector:
    collector = ZaobaoCollector({
        "id": "zaobao_international",
        "name": "联合早报·国际",
        "type": "zaobao",
        "category": "international",
        "url": "https://www.zaobao.com.sg/googlenews.xml",
        "path_prefix": path_prefix,
    })
    collector._client = StaticClient(_response(_fixture()))
    return collector


def _fixture() -> bytes:
    return (FIXTURES / "sample_zaobao_googlenews.xml").read_bytes()


def test_zaobao_registered_in_collector_map():
    assert "zaobao" in COLLECTOR_MAP
    assert COLLECTOR_MAP["zaobao"] is ZaobaoCollector


def test_zaobao_parses_sitemap_with_publish_times():
    articles = _collector().collect()

    assert len(articles) == 2
    assert articles[0].title == "测试新闻一：世界局势"
    assert articles[0].url == "https://www.zaobao.com.sg/news/world/story20260811-9500001"
    assert articles[0].source_id == "zaobao_international"
    assert articles[0].category == "international"
    assert articles[0].position == 1
    assert articles[0].published_at is not None
    assert articles[0].published_at.astimezone(TAIPEI).hour == 22
    assert articles[1].position == 2
    assert articles[1].title == "测试新闻二：区域动态"


def test_zaobao_filters_by_path_prefix():
    finance = _collector(path_prefix="/finance/").collect()
    assert [a.title for a in finance] == ["测试财经新闻：财经栏目专用"]


def test_zaobao_deduplicates_duplicate_links():
    articles = _collector().collect()
    urls = [a.url for a in articles]
    assert len(urls) == len(set(urls))


def test_zaobao_skips_missing_time_or_title():
    articles = _collector().collect()
    assert all(a.title for a in articles)
    assert all(a.published_at is not None for a in articles)


def test_zaobao_caps_at_max_items():
    entries = []
    for i in range(25):
        entries.append(
            "<url>"
            f"<loc>https://www.zaobao.com.sg/news/world/story20260811-{9501000 + i}</loc>"
            "<news:news><news:publication><news:name>Lianhe Zaobao</news:name>"
            "<news:language>zh-cn</news:language></news:publication>"
            f"<news:publication_date>2026-08-11T1{i % 10}:00:00+08:00</news:publication_date>"
            f"<news:title>新闻{i}</news:title></news:news></url>"
        )
    xml = (
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" '
        'xmlns:news="http://www.google.com/schemas/sitemap-news/0.9">'
        + "".join(entries)
        + "</urlset>"
    )
    collector = _collector()
    collector._client = StaticClient(_response(xml.encode("utf-8")))
    articles = collector.collect()
    assert len(articles) == ZaobaoCollector.MAX_ITEMS
