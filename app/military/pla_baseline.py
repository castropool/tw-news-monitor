"""Baselines and anomaly flags for the MND daily PLA activity series.

Deterministic statistics only (no model calls), computed from
``pla_activity_daily``.  For a given day each metric is compared with the
preceding ``window`` days (default 90, the day itself excluded):

- ``z``: standard score against that window's mean and standard deviation;
- ``spike``: z >= 2 and at or above the window's 90th percentile;
- ``lull``: z <= -1.5 and at or below the 10th percentile.

A weekly view adds the 7-day mean against the prior window's mean, the days
above the 90th percentile, and a least-squares slope over the window, so
the weekly assessment can separate one-off spikes from a moving baseline.
Fewer than ``min_points`` (default 30) days with data in the window means
"insufficient baseline": no flags are raised rather than misleading ones.

CLI::

    python -m app.military.pla_baseline                 # last 7 days
    python -m app.military.pla_baseline --week-ending 2026-11-14
"""

from __future__ import annotations

import argparse
import sqlite3
import statistics
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

from . import pla_activity

METRICS = ("aircraft", "aircraft_crossed", "vessels", "official_ships", "balloons")
LABELS = {
    "aircraft": "共機架次",
    "aircraft_crossed": "逾越中線/進入空域架次",
    "vessels": "共艦艘數",
    "official_ships": "公務船艘數",
    "balloons": "空飄氣球",
}
DEFAULT_WINDOW = 90
MIN_POINTS = 30


def load_series(conn: sqlite3.Connection) -> dict[str, dict[str, int | None]]:
    pla_activity.ensure_table(conn)
    cur = conn.execute(
        f"SELECT report_date, {', '.join(METRICS)} FROM pla_activity_daily ORDER BY report_date"
    )
    return {row[0]: dict(zip(METRICS, row[1:])) for row in cur.fetchall()}


def _percentile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    pos = (len(ordered) - 1) * q
    lo = int(pos)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (pos - lo)


def _slope(points: list[tuple[int, float]]) -> float | None:
    if len(points) < 2:
        return None
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    mx, my = statistics.fmean(xs), statistics.fmean(ys)
    den = sum((x - mx) ** 2 for x in xs)
    if den == 0:
        return None
    return sum((x - mx) * (y - my) for x, y in points) / den


@dataclass(slots=True)
class Baseline:
    metric: str
    points: int
    mean: float | None = None
    stdev: float | None = None
    p10: float | None = None
    p90: float | None = None
    slope_per_day: float | None = None

    @property
    def sufficient(self) -> bool:
        return self.mean is not None


def baseline(
    series: dict[str, dict[str, int | None]],
    metric: str,
    before: date,
    *,
    window: int = DEFAULT_WINDOW,
    min_points: int = MIN_POINTS,
) -> Baseline:
    """Statistics over the ``window`` days before ``before`` (exclusive)."""
    start = before - timedelta(days=window)
    points: list[tuple[int, float]] = []
    for day_str, row in series.items():
        day = date.fromisoformat(day_str)
        value = row.get(metric)
        if start <= day < before and value is not None:
            points.append(((day - start).days, float(value)))
    result = Baseline(metric=metric, points=len(points))
    if len(points) < min_points:
        return result
    values = [p[1] for p in points]
    result.mean = statistics.fmean(values)
    result.stdev = statistics.pstdev(values)
    result.p10 = _percentile(values, 0.10)
    result.p90 = _percentile(values, 0.90)
    result.slope_per_day = _slope(points)
    return result


@dataclass(slots=True)
class DayReading:
    day: str
    metric: str
    value: int | None
    z: float | None
    flag: str | None  # "spike" | "lull" | None
    baseline: Baseline


def read_day(series, metric: str, day: date, **kw) -> DayReading:
    base = baseline(series, metric, day, **kw)
    value = (series.get(day.isoformat()) or {}).get(metric)
    z = flag = None
    if value is not None and base.sufficient:
        if base.stdev:
            z = (value - base.mean) / base.stdev
        if z is not None and z >= 2 and value >= base.p90:
            flag = "spike"
        elif z is not None and z <= -1.5 and value <= base.p10:
            flag = "lull"
    return DayReading(day.isoformat(), metric, value, z, flag, base)


@dataclass(slots=True)
class WeekMetric:
    metric: str
    days_with_data: int
    total: int | None
    week_mean: float | None
    max_value: int | None
    max_day: str | None
    baseline: Baseline
    ratio_to_baseline: float | None
    days_above_p90: int
    flags: list[DayReading] = field(default_factory=list)


@dataclass(slots=True)
class WeekView:
    start: str
    end: str
    missing_days: list[str]
    metrics: dict[str, WeekMetric]


def week_view(series, week_ending: date, **kw) -> WeekView:
    """Seven days ending ``week_ending`` against the window before them."""
    start = week_ending - timedelta(days=6)
    days = [start + timedelta(days=i) for i in range(7)]
    missing = [d.isoformat() for d in days if d.isoformat() not in series]
    out: dict[str, WeekMetric] = {}
    for metric in METRICS:
        values = [(d, (series.get(d.isoformat()) or {}).get(metric)) for d in days]
        present = [(d, v) for d, v in values if v is not None]
        base = baseline(series, metric, start, **kw)
        readings = [read_day(series, metric, d, **kw) for d, _ in present]
        week_mean = statistics.fmean(v for _, v in present) if present else None
        max_day, max_value = max(present, key=lambda p: p[1]) if present else (None, None)
        out[metric] = WeekMetric(
            metric=metric,
            days_with_data=len(present),
            total=sum(v for _, v in present) if present else None,
            week_mean=week_mean,
            max_value=max_value,
            max_day=max_day.isoformat() if max_day else None,
            baseline=base,
            ratio_to_baseline=(week_mean / base.mean) if (week_mean is not None and base.mean) else None,
            days_above_p90=sum(1 for _, v in present if base.sufficient and v > base.p90),
            flags=[r for r in readings if r.flag],
        )
    return WeekView(start.isoformat(), week_ending.isoformat(), missing, out)


def _fmt(value, digits: int = 1) -> str:
    return "—" if value is None else (f"{value:.{digits}f}" if isinstance(value, float) else str(value))


def render_week_markdown(view: WeekView, metrics: tuple[str, ...] = ("aircraft", "aircraft_crossed", "vessels")) -> str:
    lines = [
        f"| 指標 | 本週合計 | 日均 | 單日最高 | 前90日日均 | 本週/基線 | 高於P90天數 | 90日趨勢(每日) |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for m in metrics:
        w = view.metrics[m]
        b = w.baseline
        peak = f"{w.max_value}（{w.max_day[5:]}）" if w.max_value is not None else "—"
        ratio = f"{w.ratio_to_baseline:.2f}×" if w.ratio_to_baseline is not None else "—"
        trend = f"{b.slope_per_day:+.3f}" if b.slope_per_day is not None else "基線不足"
        lines.append(
            f"| {LABELS[m]} | {_fmt(w.total)} | {_fmt(w.week_mean)} | {peak} | {_fmt(b.mean)} | {ratio} | "
            f"{w.days_above_p90 if b.sufficient else '—'} | {trend} |"
        )
    flags = [r for m in metrics for r in view.metrics[m].flags]
    if flags:
        lines.append("")
        for r in sorted(flags, key=lambda r: (r.day, r.metric)):
            kind = "異常偏高" if r.flag == "spike" else "異常偏低"
            lines.append(f"- {r.day} {LABELS[r.metric]} {r.value}（z={r.z:+.1f}），{kind}")
    if view.missing_days:
        lines.append("")
        lines.append(f"- 缺報日期：{'、'.join(view.missing_days)}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.military.pla_baseline")
    parser.add_argument("--week-ending", type=date.fromisoformat, help="YYYY-MM-DD (default: today, Taipei)")
    parser.add_argument("--db", help="news DB path (default: the configured news DB)")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    from ..database import Database
    from ..settings import get_settings
    from ..time_utils import TAIPEI

    end = args.week_ending or datetime.now(TAIPEI).date()
    with Database(args.db or get_settings().news_db_path) as db:
        view = week_view(load_series(db.conn), end)
    print(f"PLA activity {view.start} – {view.end}\n")
    print(render_week_markdown(view))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
