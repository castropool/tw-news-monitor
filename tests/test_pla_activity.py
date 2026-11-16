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


def _report(day: int, month: int = 11) -> str:
    return (
        f"一、日期：中華民國114年{month}月{day - 1}日（星期一）0600時至中華民國114年{month}月{day}日（星期二）0600時止。\n"
        f"二、臺海周邊海、空域動態：共機{day}架次、共艦3艘。"
    )


def test_next_page_link_variants():
    base = "https://www.mnd.gov.tw/news/plaactlist"
    assert pla.next_page_link('<a href="?page=2">下一頁</a>', base) == "https://www.mnd.gov.tw/news/plaactlist?page=2"
    assert pla.next_page_link('<a rel="next" href="/p/3">3</a>', base) == "https://www.mnd.gov.tw/p/3"
    assert pla.next_page_link('<a class="page-next" href="/p/4"></a>', base) == "https://www.mnd.gov.tw/p/4"
    assert pla.next_page_link('<a href="javascript:__doPostBack()">下一頁</a>', base) is None


def test_backfill_walks_pages_until_since_and_skips_newer_duplicates():
    base = "https://www.mnd.gov.tw/news/plaactlist"
    page1 = ('<a href="/r/10">中共解放軍臺海周邊海、空域動態</a><a href="/r/9">中共解放軍臺海周邊海、空域動態</a>'
             '<a href="?page=2">下一頁</a>')
    page2 = ('<a href="/r/9">中共解放軍臺海周邊海、空域動態</a><a href="/r/8">中共解放軍臺海周邊海、空域動態</a>'
             '<a href="/r/7">中共解放軍臺海周邊海、空域動態</a><a href="?page=3">下一頁</a>')
    pages = {
        base: page1, base + "?page=2": page2,
        "https://www.mnd.gov.tw/r/10": detail_html(_report(10)),
        "https://www.mnd.gov.tw/r/9": detail_html(_report(9)),
        "https://www.mnd.gov.tw/r/8": detail_html(_report(8)),
        "https://www.mnd.gov.tw/r/7": detail_html(_report(7)),
    }
    from datetime import date

    reports, reason = pla.backfill(date(2025, 11, 8), client=mock_client(pages), sleep=lambda s: None)
    assert reason == "reached_since"
    assert [r.report_date for r in reports] == ["2025-11-10", "2025-11-09", "2025-11-08"]


def test_backfill_reports_why_it_stopped():
    from datetime import date

    base = "https://www.mnd.gov.tw/news/plaactlist"
    pages = {base: '<a href="/r/10">中共解放軍臺海周邊海、空域動態</a>',
             "https://www.mnd.gov.tw/r/10": detail_html(_report(10))}
    reports, reason = pla.backfill(date(2025, 1, 1), client=mock_client(pages), sleep=lambda s: None)
    assert reason == "no_next_page" and len(reports) == 1


def test_missing_dates_lists_gaps(db):
    from datetime import date

    pla.save_reports(db.conn, [pla.parse_report_text(_report(d), "u") for d in (10, 12)])
    assert pla.missing_dates(db.conn, date(2025, 11, 10), date(2025, 11, 13)) == ["2025-11-11", "2025-11-13"]


def test_links_honour_base_href():
    html = ('<html><head><base href="/"></head><body>'
            '<a href="news/plaact/88211">中共解放軍臺海周邊海、空域動態</a></body></html>')
    assert pla.report_links(html, "https://www.mnd.gov.tw/news/plaactlist") == [
        "https://www.mnd.gov.tw/news/plaact/88211"]


def test_url_variants_collapse_repeated_segments():
    assert pla.url_variants("https://www.mnd.gov.tw/news/news/plaact/1") == [
        "https://www.mnd.gov.tw/news/news/plaact/1", "https://www.mnd.gov.tw/news/plaact/1"]
    assert pla.url_variants("https://www.mnd.gov.tw/news/plaact/1") == ["https://www.mnd.gov.tw/news/plaact/1"]


def test_fetch_falls_back_when_relative_links_resolve_too_deep():
    # The production failure: list links "news/plaact/N" with no <base>,
    # resolved to /news/news/plaact/N (404); real pages live at /news/plaact/N.
    listing = '<a href="news/plaact/88211">中共解放軍臺海周邊海、空域動態</a>'
    pages = {pla.DEFAULT_LIST_URL: listing,
             "https://www.mnd.gov.tw/news/plaact/88211": detail_html(STANDARD)}
    reports = pla.fetch_reports(client=mock_client(pages))
    assert [(r.report_date, r.source_url) for r in reports] == [
        ("2025-11-13", "https://www.mnd.gov.tw/news/plaact/88211")]


# Verbatim text of report 88211 as served by www.mnd.gov.tw on 2026-11-14.
LIVE_88211 = (
    "一、日期:\n"
    "中華民國115年11月13日(星期五)0600時至115年11月14日(星期六)0600時止。\n"
    "二、活動動態:\n"
    "迄0600時止,偵獲共機10架次(逾越中線進入中部、西南及東部空域計4架次)、共艦8艘及公務船3艘,持續在臺海周邊活動。"
)


def test_live_report_format():
    r = pla.parse_report_text(LIVE_88211, "https://www.mnd.gov.tw/news/plaact/88211")
    assert (r.period_start, r.period_end, r.report_date) == ("2026-11-13", "2026-11-14", "2026-11-14")
    assert (r.aircraft, r.aircraft_crossed, r.vessels, r.official_ships, r.balloons) == (10, 4, 8, 3, None)


def test_live_format_with_page_chrome_and_later_dates():
    text = "國防部 首頁\n" + LIVE_88211 + "\n發布日期:115年11月14日\n更新日期:115年11月15日"
    assert pla.parse_report_text(text).report_date == "2026-11-14"


def test_detail_timeout_is_retried_once():
    calls = []

    def handler(request):
        calls.append(str(request.url))
        if len(calls) == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, text=detail_html(LIVE_88211))

    client = httpx.Client(transport=httpx.MockTransport(handler))
    assert pla.get_detail(client, "https://www.mnd.gov.tw/news/plaact/88211").status_code == 200
    assert len(calls) == 2


def test_official_ships_written_without_chuan():
    # Report 88194 (2026-11-11) on www.mnd.gov.tw: "共艦7艘及公務3艘".
    text = (
        "一、日期:\n中華民國115年11月10日(星期二)0600時至115年11月11日(星期三)0600時止。\n"
        "二、活動動態:\n迄0600時止,偵獲共機4架次(逾越中線進入北部及西南空域計2架次)、共艦7艘及公務3艘,持續在臺海周邊活動。"
    )
    r = pla.parse_report_text(text)
    assert (r.report_date, r.aircraft, r.aircraft_crossed, r.vessels, r.official_ships) == (
        "2026-11-11", 4, 2, 7, 3)


def test_backfill_saves_as_it_goes_and_resumes_without_refetching(db):
    from datetime import date

    base = "https://www.mnd.gov.tw/news/plaactlist"
    page1 = ('<a href="/r/10">中共解放軍臺海周邊海、空域動態</a><a href="/r/9">中共解放軍臺海周邊海、空域動態</a>'
             '<a href="?page=2">下一頁</a>')
    page2 = ('<a href="/r/8">中共解放軍臺海周邊海、空域動態</a><a href="/r/7">中共解放軍臺海周邊海、空域動態</a>')
    pages = {base: page1, base + "?page=2": page2}
    for d in (10, 9, 8, 7):
        pages[f"https://www.mnd.gov.tw/r/{d}"] = detail_html(_report(d))
    fetched = []
    r8_attempts = []

    def handler(request):
        fetched.append(str(request.url))
        if str(request.url) == "https://www.mnd.gov.tw/r/8":
            r8_attempts.append(1)
            if len(r8_attempts) <= 2:
                raise httpx.ReadTimeout("slow", request=request)  # first run: /r/8 times out twice
        body = pages.get(str(request.url))
        return httpx.Response(200, text=body) if body is not None else httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(handler))
    saved = []
    first, _ = pla.backfill(date(2025, 11, 7), client=client, sleep=lambda s: None,
                            on_report=lambda r: (saved.append(r.report_date), pla.save_reports(db.conn, [r])))
    assert saved == ["2025-11-10", "2025-11-09", "2025-11-07"]  # stored immediately, /r/8 skipped
    fetched.clear()
    second, reason = pla.backfill(date(2025, 11, 7), client=client, sleep=lambda s: None,
                                  known=pla.stored_urls(db.conn),
                                  on_report=lambda r: pla.save_reports(db.conn, [r]))
    assert [r.report_date for r in second] == ["2025-11-08"]
    assert not any(u.endswith(("/r/10", "/r/9", "/r/7")) for u in fetched)  # not refetched
    assert reason == "reached_since" or reason == "no_next_page"
    assert pla.missing_dates(db.conn, date(2025, 11, 7), date(2025, 11, 10)) == []


@pytest.mark.parametrize("line, start, end", [
    ("中華民國114年12月9日(星期二)0600時至12月10日(星期三)0600時止。", "2025-12-09", "2025-12-10"),
    ("中華民國114年12月31日(星期三)0600時至1月1日(星期四)0600時止。", "2025-12-31", "2026-01-01"),
    ("中華民國115年11月13日(星期五)0600時至115年11月14日(星期六)0600時止。", "2026-11-13", "2026-11-14"),
])
def test_end_date_without_year(line, start, end):
    r = pla.parse_report_text(f"一、日期:\n{line}\n二、活動動態:\n偵獲共機15架次、共艦6艘。")
    assert (r.period_start, r.period_end, r.report_date) == (start, end, end)


def test_unparsed_pages_are_recorded_with_a_sample(db):
    from datetime import date

    base = "https://www.mnd.gov.tw/news/plaactlist"
    odd = "<html><body><h2>中共解放軍臺海周邊海、空域動態</h2><p>一、日期：民國113年5月23日。二、本日共機活動另行公布。</p></body></html>"
    pages = {base: '<a href="/r/1">中共解放軍臺海周邊海、空域動態</a>', "https://www.mnd.gov.tw/r/1": odd}
    pla.backfill(date(2020, 1, 1), client=mock_client(pages), sleep=lambda s: None,
                 on_unparsed=lambda url, text: pla.record_unparsed(db.conn, url, text))
    rows = pla.unparsed(db.conn)
    assert rows[0][0] == "https://www.mnd.gov.tw/r/1" and "本日共機活動另行公布" in rows[0][1]


def test_stored_text_and_cli_print(db, capsys):
    pla.save_reports(db.conn, [pla.parse_report_text(LIVE_88211, "https://www.mnd.gov.tw/news/plaact/88211")])
    assert pla.stored_text(db.conn, "2026-11-14")[0].endswith("88211")
    path = str(db._db_path)
    db.close()
    assert pla.main(["--text", "2026-11-14", "2026-01-01", "--db", path]) == 0
    out = capsys.readouterr().out
    assert "=== 2026-11-14" in out and "'aircraft_crossed': 4" in out and "=== 2026-01-01: no stored report" in out
    db.connect()


# Verbatim report texts from www.mnd.gov.tw, captured on the server 2026-11-15.
LIVE_OLDER = {
    "2026-01-15": ("一、日期:\n中華民國115年1月14日(星期三)0600時至115年1月15日(星期四)0600時止。\n二、活動動態:\n"
                   "迄0600時止,偵獲共機9架次(逾越中線進入北部、西南及東部空域9架次)及共艦11艘,持續在臺海周邊活動。"
                   "國軍運用任務機、艦及岸置飛彈系統嚴密監控與應處。\n三、中共空飄氣球活動:\n中共空飄氣球計偵獲1顆。",
                   (9, 9, 11, None, 1)),
    "2026-07-18": ("一、日期:\n中華民國115年7月17日(星期五)0600時至115年7月18日(星期六)0600時止。\n二、活動動態:\n"
                   "迄0600時止,偵獲共艦8艘及公務船4艘,持續在臺海周邊活動。國軍運用任務機、艦及岸置飛彈系統嚴密監控與應處。\n"
                   "三、上述期間未偵獲共機,故無提供航跡圖。",
                   (0, 0, 8, 4, None)),
    "2025-10-26": ("一、日期:\n中華民國114年10月25日(星期六)0600時至10月26日(星期日)0600時止。\n二、活動動態:\n"
                   "迄0600時止,偵獲共機8架次(逾越中線進入北部、中部及西南空域5架次)及共艦7艘,持續在臺海周邊活動。",
                   (8, 5, 7, None, None)),
    "2025-02-20": ("一、日期:\n中華民國114年2月19日(星期三)0600時至2月20日(星期四)0600時止。\n二、活動動態 :\n"
                   "迄0600時止,偵獲共機36架次(其中逾越海峽中線進入北部、中部、西南及東南空域29架次)及共艦8艘,持續在臺海周邊活動。"
                   "國軍運用任務機、艦及岸置飛彈系統嚴密監控與應處。\n三、中共空飄氣球活動:\n中共空飄氣球計偵獲1顆。",
                   (36, 29, 8, None, 1)),
    "2025-12-10": ("一、日期:\n中華民國114年12月9日(星期二)0600時至12月10日(星期三)0600時止。\n二、活動動態:\n"
                   "迄0600時止,偵獲共機7架次(逾越中線進入北部及西南空域2架次)及共艦6艘,持續在臺海周邊活動。",
                   (7, 2, 6, None, None)),
}


@pytest.mark.parametrize("day", sorted(LIVE_OLDER))
def test_live_older_wordings(day):
    text, expected = LIVE_OLDER[day]
    r = pla.parse_report_text(text)
    assert r.report_date == day
    assert (r.aircraft, r.aircraft_crossed, r.vessels, r.official_ships, r.balloons) == expected


def test_reparse_stored_fixes_rows_without_downloading(db):
    text, _ = LIVE_OLDER["2025-02-20"]
    pla.save_reports(db.conn, [pla.parse_report_text(text, "https://www.mnd.gov.tw/news/plaact/84042")])
    db.conn.execute("UPDATE pla_activity_daily SET aircraft_crossed = NULL, balloons = NULL")  # as the old parser left it
    db.conn.commit()
    stats = pla.reparse_stored(db.conn)
    assert stats == {"rows": 1, "changed": 1, "date_mismatch": 0, "unparsed": 0}
    row = db.conn.execute("SELECT aircraft_crossed, balloons FROM pla_activity_daily").fetchone()
    assert row == (29, 1)
    assert pla.reparse_stored(db.conn)["changed"] == 0


def test_inspect_page_finds_content_and_images_not_menus():
    html = ("<html><body><nav><a href='/a'>本部簡介</a><a href='/b'>部長</a></nav>"
            "<div class='menu'>" + "".join(f"<a href='/m{i}'>選單項目{i}</a>" for i in range(30)) + "</div>"
            "<div class='content'><h2>中共解放軍臺海周邊海、空域動態(113年1月12日)</h2>"
            "<p>國防部今日公布共機動態如附圖。</p><img src='/img/plaact-0112.jpg' alt='共機活動示意圖'></div>"
            "</body></html>")
    info = pla.inspect_page(html, "https://www.mnd.gov.tw/news/plaact/82454")
    assert "如附圖" in info["content"] and "選單項目" not in info["content"]
    assert info["images"] == [{"src": "https://www.mnd.gov.tw/img/plaact-0112.jpg", "alt": "共機活動示意圖"}]
    assert info["mentions_aircraft"] is True and info["mentions_vessels"] is False
