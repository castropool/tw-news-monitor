"""PLA activity baselines: windows, flags, weekly view, rendering."""

from datetime import date, timedelta

import pytest

from app.database import Database
from app.military import pla_activity as pla
from app.military import pla_baseline as pb

END = date(2026, 11, 14)


def make_series(days=120, base=10, overrides=None, crossed=None):
    series = {}
    for i in range(days):
        d = END - timedelta(days=days - 1 - i)
        value = base + (i % 5) - 2  # 8..12, stable
        series[d.isoformat()] = {"aircraft": value, "aircraft_crossed": crossed, "vessels": 6,
                                 "official_ships": None, "balloons": None}
    for day, row in (overrides or {}).items():
        series[day].update(row)
    return series


def test_baseline_excludes_the_day_itself_and_needs_enough_points():
    s = make_series()
    b = pb.baseline(s, "aircraft", END)
    assert b.points == 90 and b.sufficient and 9.5 < b.mean < 10.5
    assert not pb.baseline(make_series(days=20), "aircraft", END).sufficient
    assert not pb.baseline(s, "official_ships", END).sufficient  # never reported


def test_spike_and_lull_flags():
    s = make_series(overrides={END.isoformat(): {"aircraft": 45}})
    r = pb.read_day(s, "aircraft", END)
    assert r.flag == "spike" and r.z > 2
    s = make_series(overrides={END.isoformat(): {"aircraft": 0}})
    assert pb.read_day(s, "aircraft", END).flag == "lull"
    assert pb.read_day(make_series(), "aircraft", END).flag is None


def test_constant_metric_has_no_z_and_no_flag():
    r = pb.read_day(make_series(overrides={END.isoformat(): {"vessels": 9}}), "vessels", END)
    assert r.z is None and r.flag is None  # zero variance: no score invented


def test_insufficient_baseline_raises_no_flags():
    s = make_series(days=15, overrides={END.isoformat(): {"aircraft": 80}})
    assert pb.read_day(s, "aircraft", END).flag is None


def test_week_view_ratio_peak_missing_and_trend():
    s = make_series(overrides={(END - timedelta(days=2)).isoformat(): {"aircraft": 40}})
    del s[(END - timedelta(days=4)).isoformat()]
    w = pb.week_view(s, END)
    a = w.metrics["aircraft"]
    assert (w.start, w.end) == ("2026-11-08", "2026-11-14")
    assert w.missing_days == ["2026-11-10"]
    assert a.days_with_data == 6 and a.max_value == 40 and a.max_day == "2026-11-12"
    assert a.ratio_to_baseline > 1.3 and a.days_above_p90 >= 1
    assert [f.day for f in a.flags] == ["2026-11-12"]
    assert abs(a.baseline.slope_per_day) < 0.05  # stable series


def test_rising_series_has_positive_slope():
    s = {}
    for i in range(120):
        d = END - timedelta(days=119 - i)
        s[d.isoformat()] = {"aircraft": 5 + i // 4, "aircraft_crossed": None, "vessels": None,
                            "official_ships": None, "balloons": None}
    assert pb.week_view(s, END).metrics["aircraft"].baseline.slope_per_day == pytest.approx(0.25, abs=0.02)


def test_markdown_rendering():
    s = make_series(overrides={(END - timedelta(days=2)).isoformat(): {"aircraft": 40}})
    md = pb.render_week_markdown(pb.week_view(s, END))
    assert "| 共機架次 |" in md and "異常偏高" in md and "2026-11-12 共機架次 40" in md
    md2 = pb.render_week_markdown(pb.week_view(make_series(days=10), END))
    assert "基線不足" in md2


def test_load_series_from_db(tmp_path):
    with Database(tmp_path / "n.db") as db:
        text = ("一、日期：中華民國115年11月13日（星期五）0600時至中華民國115年11月14日（星期六）0600時止。\n"
                "二、臺海周邊海、空域動態：共機13架次、共艦6艘。\n三、共機12架次逾越中線。")
        pla.save_reports(db.conn, [pla.parse_report_text(text, "u")])
        series = pb.load_series(db.conn)
    assert series["2026-11-14"]["aircraft"] == 13 and series["2026-11-14"]["aircraft_crossed"] == 12


def test_history_finds_earlier_peaks_rank_and_last_year():
    s = make_series(days=420, overrides={
        (END - timedelta(days=2)).isoformat(): {"aircraft": 40},    # this week's peak
        (END - timedelta(days=50)).isoformat(): {"aircraft": 60},   # earlier, higher
        (END - timedelta(days=200)).isoformat(): {"aircraft": 40},  # earlier, equal
    })
    h = pb.history(s, END)
    assert h.week_peak == 40 and h.week_peak_day == "2026-11-12"
    assert h.count_at_or_above_peak == 2
    assert [d for d, _ in h.earlier_days_at_or_above_peak] == [
        (END - timedelta(days=50)).isoformat(), (END - timedelta(days=200)).isoformat()]
    assert h.week_rank == 2 and h.weeks_on_record >= 58  # only the week holding the 60 is higher
    assert h.same_week_last_year_end == (END - timedelta(days=364)).isoformat()
    assert h.same_week_last_year_total is not None
    md = pb.render_history_markdown(h)
    assert "有 2 天達到或超過此數" in md and "排第" in md and "去年同週" in md


def test_history_record_high_and_no_history():
    s = make_series(days=120, overrides={(END - timedelta(days=1)).isoformat(): {"aircraft": 99}})
    md = pb.render_history_markdown(pb.history(s, END))
    assert "高於此前 113 天紀錄中的任何一天" in md and "此前紀錄中沒有任何 7 日合計達到此數" in md
    assert "尚無歷史資料" in pb.render_history_markdown(pb.history(make_series(days=7), END))


def test_history_skips_week_total_with_gaps():
    s = make_series(days=120)
    for k in (1, 3):
        del s[(END - timedelta(days=k)).isoformat()]
    h = pb.history(s, END)
    assert h.week_total is None and "缺報超過 1 天" in pb.render_history_markdown(h)
