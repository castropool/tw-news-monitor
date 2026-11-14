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
