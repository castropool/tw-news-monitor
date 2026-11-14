"""MND daily PLA activity parser, storage and run hook.

Samples follow the report's published wording; the live HTML layout has not
been verified from the development environment (see module docstring).
"""

from datetime import datetime, timezone

import httpx
import pytest

from app.database import Database
from app.military import pla_activity as pla

STANDARD = (
    "一、日期：中華民國114年11月12日（星期三）0600時至中華民國114年11月13日（星期四）0600時止。\n"
    "二、臺海周邊海、空域動態：共機13架次、共艦6艘、公務船1艘。\n"
    "三、共機12架次逾越中線進入我北部、中部及西南空域。"
)


def detail_html(body: str) -> str:
    return (
        "<html><head><title>國防部</title><script>var x='共機99架次';</script></head><body>"
        "<nav>首頁 新聞稿 即時軍事動態</nav>"
        "<h2>中共解放軍臺海周邊海、空域動態</h2>"
        f"<div class='maincontent'><p>{body.replace(chr(10), '</p><p>')}</p>"
        "<p><img src='/img/map.jpg' alt='航跡圖'></p></div>"
        "<footer>更新日期：民國115年1月1日</footer></body></html>"
    )


LIST_HTML = """
<html><body><ul class="list">
  <li><a href="/news/plaact/84123"><span>中共解放軍臺海周邊海、空域動態</span><span>114-11-13</span></a></li>
  <li><a href="/news/plaact/84100" title="中共解放軍台海周邊海、空域動態">114-11-12</a></li>
  <li><a href="/news/detail/999">國防部長出席立法院外交及國防委員會</a></li>
  <li><a href="/news/plaact/84123">中共解放軍臺海周邊海、空域動態</a></li>
</ul></body></html>
"""


def test_standard_report():
    r = pla.parse_report_text(STANDARD, "https://www.mnd.gov.tw/news/plaact/84123")
    assert (r.report_date, r.period_start, r.period_end) == ("2025-11-13", "2025-11-12", "2025-11-13")
    assert (r.aircraft, r.aircraft_crossed, r.vessels, r.official_ships, r.balloons) == (13, 12, 6, 1, None)
    assert r.raw_text.endswith("西南空域。")


@pytest.mark.parametrize(
    "line2, line3, expected",
    [
        ("共機0架次、共艦8艘。", "", (0, 0, 8, None, None)),
        ("未偵獲共機、共艦7艘。", "", (0, 0, 7, None, None)),
        ("共機21架次、共艦7艘、空飄氣球2顆。", "三、共機17架次逾越中線及進入北部、西南空域。", (21, 17, 7, None, 2)),
        ("共機13架次（其中12架次逾越中線進入北部空域）、共艦6艘。", "", (13, 12, 6, None, None)),
        ("共機5架次、共艦4艘。", "", (5, 0, 4, None, None)),
        ("共機５架次、共艦４艘、公務船２艘。", "三、共機３架次進入我西南空域。", (5, 3, 4, 2, None)),
    ],
)
def test_wording_variants(line2, line3, expected):
    text = (
        "一、日期：中華民國114年3月1日（星期六）0600時至中華民國114年3月2日（星期日）0600時止。\n"
        f"二、臺海周邊海、空域動態：{line2}\n{line3}"
    )
    r = pla.parse_report_text(text)
    assert (r.aircraft, r.aircraft_crossed, r.vessels, r.official_ships, r.balloons) == expected
    assert r.report_date == "2025-03-02"


def test_simplified_text_is_folded():
    simplified = STANDARD.replace("臺海周邊海、空域動態", "台海周边海、空域动态").replace("共機", "共机").replace("共艦", "共舰")
    r = pla.parse_report_text(simplified)
    assert (r.aircraft, r.aircraft_crossed, r.vessels) == (13, 12, 6)


def test_crossings_mentioned_but_unrecognised_stay_unknown():
    text = STANDARD.replace("三、共機12架次逾越中線進入我北部、中部及西南空域。", "三、部分共機逾越中線。")
    assert pla.parse_report_text(text).aircraft_crossed is None


def test_non_report_text_is_rejected():
    assert pla.parse_report_text("國防部長出席立法院外交及國防委員會。") is None
    assert pla.parse_report_text("民國114年11月13日 國防部記者會") is None


def test_page_text_ignores_scripts_and_parses_detail_page():
    r = pla.parse_report_text(pla.page_text(detail_html(STANDARD)), "u")
    assert r.aircraft == 13 and r.report_date == "2025-11-13"


def test_report_links_by_link_text_and_title_deduplicated():
    links = pla.report_links(LIST_HTML, "https://www.mnd.gov.tw/news/plaactlist")
    assert links == [
        "https://www.mnd.gov.tw/news/plaact/84123",
        "https://www.mnd.gov.tw/news/plaact/84100",
    ]


def mock_client(pages: dict[str, str]) -> httpx.Client:
    def handler(request: httpx.Request) -> httpx.Response:
        body = pages.get(str(request.url))
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(handler))


OLDER = STANDARD.replace("114年11月12日（星期三）", "114年11月11日（星期二）").replace(
    "114年11月13日（星期四）", "114年11月12日（星期三）").replace("共機13架次", "共機9架次")
PAGES = {
    pla.DEFAULT_LIST_URL: LIST_HTML,
    "https://www.mnd.gov.tw/news/plaact/84123": detail_html(STANDARD),
    "https://www.mnd.gov.tw/news/plaact/84100": detail_html(OLDER),
}


def test_fetch_reports_follows_list_newest_first():
    reports = pla.fetch_reports(client=mock_client(PAGES))
    assert [(r.report_date, r.aircraft) for r in reports] == [("2025-11-13", 13), ("2025-11-12", 9)]


def test_fetch_reports_stops_at_already_stored_date():
    reports = pla.fetch_reports(client=mock_client(PAGES), skip_dates_through="2025-11-13")
    assert [r.report_date for r in reports] == ["2025-11-13"]


def test_fetch_reports_skips_broken_detail_pages():
    pages = dict(PAGES)
    del pages["https://www.mnd.gov.tw/news/plaact/84123"]
    assert [r.report_date for r in pla.fetch_reports(client=mock_client(pages))] == ["2025-11-12"]


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "news.db")
    d.connect()
    yield d
    d.close()


def test_storage_is_idempotent_and_takes_revisions(db):
    r = pla.parse_report_text(STANDARD, "u")
    assert pla.save_reports(db.conn, [r]) == 1
    assert pla.save_reports(db.conn, [r]) == 0
    revised = pla.parse_report_text(STANDARD.replace("共艦6艘", "共艦7艘"), "u")
    assert pla.save_reports(db.conn, [revised]) == 1
    assert pla.recent_reports(db.conn)[0]["vessels"] == 7


def test_connect_does_not_create_the_table(db):
    names = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master")}
    assert "pla_activity_daily" not in names


def test_run_hook_is_off_by_default(db):
    def boom(*a, **k):
        raise AssertionError("must not fetch")

    assert pla.collect_safe(db, fetcher=boom) == {"status": "disabled"}


def test_run_hook_fetches_once_per_day_and_never_raises(db, monkeypatch):
    monkeypatch.setenv("PLA_ACTIVITY_ENABLED", "true")
    now = datetime(2025, 11, 13, 2, 0, tzinfo=timezone.utc)  # 10:00 Taipei
    calls = []

    def fake_fetch(url, skip_dates_through=None):
        calls.append(skip_dates_through)
        return [pla.parse_report_text(STANDARD, "u")]

    assert pla.collect_safe(db, now=now, fetcher=fake_fetch)["stored"] == 1
    assert pla.collect_safe(db, now=now, fetcher=fake_fetch)["status"] == "up_to_date"
    assert calls == [None]

    def broken(url, skip_dates_through=None):
        raise httpx.ConnectError("blocked")

    later = datetime(2025, 11, 14, 2, 0, tzinfo=timezone.utc)
    assert pla.collect_safe(db, now=later, fetcher=broken)["status"] == "error"
