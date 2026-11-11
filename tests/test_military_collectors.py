from datetime import datetime
from pathlib import Path

import httpx
import pytest

from app.collectors.military import (
    LtnMilitaryCollector,
    MNAMilitaryCollector,
    NownewsMilitaryCollector,
    parse_roc_date,
)
from app.collectors.rss import RSSCollector
from app.collectors.udn import UDNCollector
from app.main import COLLECTOR_MAP, collect_all
from app.database import Database
from app.time_utils import TAIPEI


ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "tests" / "fixtures" / "military"


class FakeResponse:
    def __init__(self, text: str, status_code: int = 200):
        self.text = text
        self.content = text.encode("utf-8")
        self.status_code = status_code
        self.headers = {"content-type": "text/html; charset=utf-8"}

    def raise_for_status(self):
        if self.status_code >= 400:
            request = httpx.Request("GET", "https://example.test")
            response = httpx.Response(self.status_code, request=request)
            raise httpx.HTTPStatusError("http failure", request=request, response=response)


class FakeClient:
    def __init__(self, pages: dict[str, str] | None = None, error: Exception | None = None):
        self.pages = pages or {}
        self.error = error
        self.calls: list[str] = []

    def get(self, url: str):
        self.calls.append(url)
        if self.error:
            raise self.error
        return FakeResponse(self.pages[url])

    def close(self):
        return None


def source(source_id: str, url: str, collector_type: str) -> dict:
    return {
        "id": source_id,
        "name": source_id,
        "type": collector_type,
        "category": "military",
        "topic": "military",
        "military_source_type": "commercial_military",
        "url": url,
        "enabled": True,
    }


def fixture(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_roc_year_conversion_supports_both_variants():
    assert parse_roc_date("民国115年09月04日") == datetime(2026, 9, 4, tzinfo=TAIPEI)
    assert parse_roc_date("民國114年1月2日") == datetime(2025, 1, 2, tzinfo=TAIPEI)
    assert parse_roc_date("2026-09-04") is None


def test_ltn_military_parses_only_legal_list_articles(monkeypatch):
    url = "https://def.ltn.com.tw/breakingnewslist"
    collector = LtnMilitaryCollector(source("ltn_military", url, "ltn_military"))
    collector._client = FakeClient({url: fixture("ltn_list.html")})
    monkeypatch.setattr(
        "app.collectors.military._now_taipei",
        lambda: datetime(2026, 9, 4, 20, 0, tzinfo=TAIPEI),
    )
    articles = collector.collect()
    assert [a.title for a in articles] == [
        "地狱火飞弹整合物流车 陆军采购新系统",
        "国防预算追加计划曝光",
    ]
    assert articles[0].published_at == datetime(2026, 9, 4, 14, 34, tzinfo=TAIPEI)
    assert articles[1].published_at == datetime(2026, 9, 3, 16, 42, tzinfo=TAIPEI)
    assert collector.last_outcome.schema_valid is True


def test_udn_military_reuses_existing_collector():
    assert COLLECTOR_MAP["udn"] is UDNCollector
    cfg = source(
        "udn_military",
        "https://udn.com/news/cate/2/6638sub_122173",
        "udn",
    )
    html = (ROOT / "tests" / "fixtures" / "sample_udn.html").read_text(encoding="utf-8")
    collector = UDNCollector(cfg)
    collector._client = FakeClient({cfg["url"]: html})
    articles = collector.collect()
    assert articles
    assert all(article.category == "military" for article in articles)


def test_nownews_paginates_and_stops_at_old_article(monkeypatch):
    url = "https://www.nownews.com/cat/news-summary/military/"
    cfg = source("nownews_military", url, "nownews_military")
    cfg.update({"max_pages": 3, "stop_after_hours": 72})
    page_1 = f"{url}page/1/"
    client = FakeClient(
        {url: fixture("nownews_base.html"), page_1: fixture("nownews_page_1.html")}
    )
    collector = NownewsMilitaryCollector(cfg)
    collector._client = client
    monkeypatch.setattr(
        "app.collectors.military._now_taipei",
        lambda: datetime(2026, 9, 4, 12, 0, tzinfo=TAIPEI),
    )
    articles = collector.collect()
    assert [a.title for a in articles] == [
        "新型无人机完成测试",
        "防空部队实施演练",
        "军售案进入审查",
    ]
    assert client.calls == [url, page_1]
    assert collector.last_outcome.item_count == 3


def test_nownews_valid_stale_page_is_healthy_empty_result(monkeypatch):
    url = "https://www.nownews.com/cat/news-summary/military/"
    cfg = source("nownews_military", url, "nownews_military")
    cfg.update({"max_pages": 3, "stop_after_hours": 72})
    client = FakeClient({url: fixture("nownews_base.html")})
    collector = NownewsMilitaryCollector(cfg)
    collector._client = client
    monkeypatch.setattr(
        "app.collectors.military._now_taipei",
        lambda: datetime(2026, 9, 10, 12, 0, tzinfo=TAIPEI),
    )

    assert collector.collect() == []
    assert client.calls == [url]
    assert collector.last_outcome.schema_valid is True
    assert collector.last_outcome.error_code is None


def test_nownews_collects_stable_banner_cards_before_stale_list(monkeypatch):
    url = "https://www.nownews.com/cat/news-summary/military/"
    html = """
    <html><body>
      <div class="card"><div class="item">
        <a href="https://www.nownews.com/news/9001" data-sec="banner_news"
           aria-label="首批无人机抵台"><h2 class="title">首批无人机抵台</h2></a>
      </div></div>
      <ul id="ulNewsList" class="list-wrap"><li class="item">
        <a href="https://www.nownews.com/news/8001">
          <h3 class="title">旧军演新闻</h3>
          <time datetime="2026-06-01 10:00">2026-06-01 10:00</time>
        </a>
      </li></ul>
    </body></html>
    """
    cfg = source("nownews_military", url, "nownews_military")
    cfg.update({"max_pages": 3, "stop_after_hours": 72})
    collector = NownewsMilitaryCollector(cfg)
    collector._client = FakeClient({url: html})
    monkeypatch.setattr(
        "app.collectors.military._now_taipei",
        lambda: datetime(2026, 9, 10, 12, 0, tzinfo=TAIPEI),
    )

    articles = collector.collect()
    assert [article.title for article in articles] == ["首批无人机抵台"]
    assert articles[0].published_at is None
    assert collector.last_outcome.schema_valid is True


@pytest.mark.parametrize(
    ("fixture_name", "source_id"),
    [
        ("ydn_defense.xml", "ydn_defense_focus"),
        ("ydn_weapons.xml", "ydn_weapons_tour"),
        ("ydn_world.xml", "ydn_military_world"),
    ],
)
def test_ydn_official_rss_reuses_rss_collector(fixture_name, source_id):
    url = f"https://www.ydn.com.tw/tw/Home/RSS.aspx?feed={source_id}"
    cfg = source(source_id, url, "rss")
    cfg["military_source_type"] = "official_military"
    collector = RSSCollector(cfg)
    collector._client = FakeClient({url: fixture(fixture_name)})
    articles = collector.collect()
    assert len(articles) == 1
    assert articles[0].source_id == source_id
    assert articles[0].published_at.tzinfo == TAIPEI


def test_mna_parses_title_summary_and_roc_date():
    url = "https://mna.mnd.gov.tw/news/overview/"
    cfg = source("mna_military", url, "mna_military")
    cfg["military_source_type"] = "official_military"
    collector = MNAMilitaryCollector(cfg)
    collector._client = FakeClient({url: fixture("mna_overview.html")})
    articles = collector.collect()
    assert len(articles) == 2
    assert articles[0].title == "国防科技论坛聚焦无人载具"
    assert articles[0].summary == "论坛讨论无人机自主导航与通讯。"
    assert articles[0].published_at == datetime(2026, 9, 4, tzinfo=TAIPEI)
    assert articles[0].url.startswith("https://mna.mnd.gov.tw/news/detail?UserKey=")


@pytest.mark.parametrize(
    "collector_cls,collector_type,url",
    [
        (LtnMilitaryCollector, "ltn_military", "https://def.ltn.com.tw/breakingnewslist"),
        (NownewsMilitaryCollector, "nownews_military", "https://www.nownews.com/cat/news-summary/military/"),
        (MNAMilitaryCollector, "mna_military", "https://mna.mnd.gov.tw/news/overview/"),
    ],
)
def test_malformed_html_marks_schema_failure(collector_cls, collector_type, url):
    collector = collector_cls(source("broken", url, collector_type))
    collector._client = FakeClient({url: "<html><body>layout changed</body></html>"})
    articles = collector.collect()
    assert articles == []
    assert collector.last_outcome.schema_valid is False
    assert collector.last_outcome.error_code == "schema"


def test_network_error_isolated_by_collect_all(tmp_path):
    class BrokenCollector(LtnMilitaryCollector):
        MAX_RETRIES = 0

        @property
        def client(self):
            request = httpx.Request("GET", self.url)
            return FakeClient(error=httpx.ConnectError("offline", request=request))

    class GoodCollector:
        def __init__(self, cfg):
            self.cfg = cfg
            self.last_outcome = None

        def collect(self):
            from app.models import Article

            now = datetime(2026, 9, 4, 12, 0)
            return [
                Article(
                    self.cfg["id"], self.cfg["name"], "military", "飞弹战备新闻",
                    "https://example.test/good", now, now, 1,
                )
            ]

        def close(self):
            return None

    db = Database(tmp_path / "news.db")
    db.connect()
    db.create_tables()
    broken = source("broken", "https://def.ltn.com.tw/breakingnewslist", "broken")
    good = source("good", "https://example.test/good", "good")
    inserted, _, _, failed, *_ = collect_all(
        [broken, good], db, collector_map={"broken": BrokenCollector, "good": GoodCollector}
    )
    db.close()
    assert failed == ["broken"]
    assert [article.title for article in inserted] == ["飞弹战备新闻"]


def test_collect_all_filters_noise_and_persists_military_topic(tmp_path):
    class StubCollector:
        def __init__(self, cfg):
            self.cfg = cfg
            self.last_outcome = None

        def collect(self):
            from app.models import Article

            now = datetime(2026, 9, 4, 12, 0)
            return [
                Article(
                    self.cfg["id"], self.cfg["name"], "military",
                    "视导飞弹战备", "https://example.test/keep", now, now, 1,
                ),
                Article(
                    self.cfg["id"], self.cfg["name"], "military",
                    "军人节优惠活动", "https://example.test/drop", now, now, 2,
                ),
            ]

        def close(self):
            return None

    db = Database(tmp_path / "news.db")
    db.connect()
    db.create_tables()
    cfg = source("stub", "https://example.test/list", "stub")
    inserted, total, _, failed, _, _, filtered = collect_all(
        [cfg],
        db,
        military_config={
            "enabled": True,
            "keep_keywords": ["飞弹", "战备"],
            "drop_phrases": ["军人节优惠"],
        },
        collector_map={"stub": StubCollector},
    )

    assert total == 2
    assert failed == []
    assert filtered == 1
    assert [article.url for article in inserted] == ["https://example.test/keep"]
    assert db.get_topic_urls(["https://example.test/keep"], "military") == {
        "https://example.test/keep"
    }
    assert not db.article_exists("https://example.test/drop")
    db.close()
