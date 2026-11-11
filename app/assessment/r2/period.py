"""Phase R2 period helpers (Asia/Taipei, 9th/22nd rules)."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from app.time_utils import TAIPEI
from app.assessment.reporting_period import scheduled_period_for


RUN_DAYS = (9, 22)
RUN_TIME = time(9, 0, 0)


def report_run_key(election_id: str, period_start: date, period_end: date) -> str:
    return (
        f"{election_id}__{period_start.isoformat().replace('-', '')}"
        f"__{period_end.isoformat().replace('-', '')}"
    )


def period_for_run_date(run_date: date) -> tuple[date, date]:
    """Run-date -> (period_start, period_end) using the frozen schedule rules."""
    if run_date.day not in RUN_DAYS:
        raise ValueError(f"运行日 {run_date.isoformat()} 不在计划运行日 {RUN_DAYS} 之列")
    return scheduled_period_for(run_date, RUN_DAYS)


def next_scheduled_datetime(after: datetime | None = None) -> datetime:
    """Next 9th/22nd 09:00 Asia/Taipei at-or-after the given moment."""
    after = after or datetime.now(TAIPEI)
    if after.tzinfo is None:
        after = after.replace(tzinfo=TAIPEI)
    else:
        after = after.astimezone(TAIPEI)
    cursor = after.date()
    for _ in range(62):
        if cursor.day in RUN_DAYS:
            candidate = datetime.combine(cursor, RUN_TIME, tzinfo=TAIPEI)
            if candidate >= after:
                return candidate
        cursor += timedelta(days=1)
    raise RuntimeError("未能计算下一个计划运行时间")


def as_of_run_date(as_of: date) -> date:
    return as_of
