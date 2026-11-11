"""半月报告周期计算（Asia/Taipei）。"""

from __future__ import annotations

import calendar
from dataclasses import dataclass
from datetime import date, datetime

from dateutil import tz


class PeriodError(ValueError):
    """报告周期参数或计算错误。"""


DEFAULT_PERIOD_RULES = {
    9: "previous_month_16_to_end",
    22: "current_month_01_to_15",
}


@dataclass(frozen=True)
class ReportingPeriod:
    timezone: str
    run_at: str
    run_date: str
    resolution_mode: str
    period_start: date
    period_end: date
    period_label: str
    previous_period_start: date
    previous_period_end: date
    period_complete: bool
    scheduled_run_date: str
    calendar_lag_days: int
    full_preparation_days: int
    period_definition: str = "natural_half_month"
    schedule_definition: str = "delayed_generation"

    def to_dict(self) -> dict:
        return {
            "timezone": self.timezone,
            "run_at": self.run_at,
            "run_date": self.run_date,
            "resolution_mode": self.resolution_mode,
            "period_start": self.period_start.isoformat(),
            "period_end": self.period_end.isoformat(),
            "period_label": self.period_label,
            "previous_period_start": self.previous_period_start.isoformat(),
            "previous_period_end": self.previous_period_end.isoformat(),
            "period_complete": self.period_complete,
            "scheduled_run_date": self.scheduled_run_date,
            "calendar_lag_days": self.calendar_lag_days,
            "full_preparation_days": self.full_preparation_days,
            # 兼容旧字段：只表示自然日期差，不再是权威准备期字段
            "preparation_lag_days": self.calendar_lag_days,
            "preparation_lag_semantics": "calendar_date_difference",
            "preparation_lag_deprecated": True,
            "period_definition": self.period_definition,
            "schedule_definition": self.schedule_definition,
        }


def get_timezone(name: str):
    """Return a tzinfo for the configured timezone (dateutil-backed)."""
    tzinfo = tz.gettz(name)
    if tzinfo is None:
        raise PeriodError(f"未知时区: {name}")
    return tzinfo


def month_last_day(year: int, month: int) -> date:
    return date(year, month, calendar.monthrange(year, month)[1])


def scheduled_period_for(
    run_date: date,
    run_days: tuple[int, ...],
    period_rules: dict[int, str] | None = None,
) -> tuple[date, date]:
    """Return (period_start, period_end) for a scheduled run date.

    报告统计周期仍为自然半月；9日、22日是生成日，不是统计周期起止日。
    延迟生成用于事实采集、审核、入库、快照和证据包更新。
    """
    rules = period_rules or DEFAULT_PERIOD_RULES
    if run_date.day not in run_days:
        raise PeriodError(
            f"自动模式运行日 {run_date.isoformat()} 不在计划运行日 {sorted(run_days)} "
            "之列；请使用 --period-start/--period-end 显式补跑。"
        )
    rule = rules.get(run_date.day)
    if rule == "previous_month_16_to_end":
        if run_date.month == 1:
            prev_year, prev_month_num = run_date.year - 1, 12
        else:
            prev_year, prev_month_num = run_date.year, run_date.month - 1
        start = date(prev_year, prev_month_num, 16)
        end = month_last_day(prev_year, prev_month_num)
    elif rule == "current_month_01_to_15":
        start = run_date.replace(day=1)
        end = run_date.replace(day=15)
    else:
        raise PeriodError(
            f"运行日 {run_date.day} 没有已定义的周期规则（配置：{rules}）"
        )
    return start, end


def previous_period_for(period_start: date, period_end: date) -> tuple[date, date]:
    """Return the immediately preceding half-month period."""
    if period_start.day == 16:
        return period_start.replace(day=1), period_start.replace(day=15)
    if period_start.day != 1:
        raise PeriodError(
            f"无法推断上一周期：period_start={period_start.isoformat()}"
        )
    prev_month_start = (period_start.replace(day=1)).replace(day=1)
    if prev_month_start.month == 1:
        anchor = date(prev_month_start.year - 1, 12, 1)
    else:
        anchor = date(prev_month_start.year, prev_month_start.month - 1, 1)
    prev_start = date(anchor.year, anchor.month, 16)
    prev_end = month_last_day(anchor.year, anchor.month)
    return prev_start, prev_end


def resolve_reporting_period(
    *,
    timezone_name: str,
    run_days: tuple[int, ...],
    period_rules: dict[int, str] | None = None,
    as_of: date | None = None,
    explicit_start: date | None = None,
    explicit_end: date | None = None,
    now: datetime | None = None,
) -> ReportingPeriod:
    """Resolve a reporting period from CLI-style inputs.

    ``as_of`` triggers scheduled-mode resolution against that local date;
    explicit start/end trigger manual backfill mode.
    """
    tzinfo = get_timezone(timezone_name)
    if now is None:
        now = datetime.now(tzinfo)
    run_at = now.astimezone(tzinfo).isoformat()

    if explicit_start is not None or explicit_end is not None:
        if explicit_start is None or explicit_end is None:
            raise PeriodError("显式周期必须同时提供 --period-start 和 --period-end")
        if explicit_end < explicit_start:
            raise PeriodError("--period-end 不得早于 --period-start")
        start, end = explicit_start, explicit_end
        mode = "explicit"
        run_date = now.astimezone(tzinfo).date()
    else:
        if as_of is None:
            as_of = now.astimezone(tzinfo).date()
        start, end = scheduled_period_for(as_of, run_days, period_rules)
        mode = "scheduled"
        run_date = as_of

    prev_start, prev_end = previous_period_for(start, end)
    complete = end <= run_date
    calendar_lag = (run_date - end).days
    if mode == "explicit":
        calendar_lag = max(0, calendar_lag)
    full_preparation_days = max(calendar_lag - 1, 0)
    return ReportingPeriod(
        timezone=timezone_name,
        run_at=run_at,
        run_date=run_date.isoformat(),
        resolution_mode=mode,
        period_start=start,
        period_end=end,
        period_label=f"{start.isoformat()}至{end.isoformat()}",
        previous_period_start=prev_start,
        previous_period_end=prev_end,
        period_complete=complete,
        scheduled_run_date=run_date.isoformat() if mode == "scheduled" else "",
        calendar_lag_days=calendar_lag,
        full_preparation_days=full_preparation_days,
    )
