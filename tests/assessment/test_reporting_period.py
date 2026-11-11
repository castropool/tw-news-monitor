from datetime import date, datetime, timezone

import pytest

from app.assessment.evidence_pack_builder import load_yaml
from app.assessment.build_evidence_pack import business_hash
from app.assessment.reporting_period import (
    DEFAULT_PERIOD_RULES,
    PeriodError,
    get_timezone,
    previous_period_for,
    resolve_reporting_period,
    scheduled_period_for,
)
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
CONFIG = load_yaml(PROJECT_ROOT / "config" / "election_assessment.yaml")


def _resolve(**kwargs):
    return resolve_reporting_period(
        timezone_name="Asia/Taipei",
        run_days=(9, 22),
        period_rules=DEFAULT_PERIOD_RULES,
        **kwargs,
    )


class TestScheduledPeriod:
    def test_day9_previous_month_second_half(self):
        start, end = scheduled_period_for(date(2026, 8, 9), (9, 22))
        assert (start, end) == (date(2026, 7, 16), date(2026, 7, 31))

    def test_day22_current_month_first_half(self):
        start, end = scheduled_period_for(date(2026, 8, 22), (9, 22))
        assert (start, end) == (date(2026, 8, 1), date(2026, 8, 15))

    def test_january_cross_year(self):
        start, end = scheduled_period_for(date(2027, 1, 9), (9, 22))
        assert (start, end) == (date(2026, 12, 16), date(2026, 12, 31))

    def test_february_non_leap(self):
        start, end = scheduled_period_for(date(2027, 2, 9), (9, 22))
        assert (start, end) == (date(2027, 1, 16), date(2027, 1, 31))

    def test_march_leap_year(self):
        start, end = scheduled_period_for(date(2028, 3, 9), (9, 22))
        assert (start, end) == (date(2028, 2, 16), date(2028, 2, 29))

    def test_30_day_month(self):
        start, end = scheduled_period_for(date(2026, 10, 9), (9, 22))
        assert (start, end) == (date(2026, 9, 16), date(2026, 9, 30))

    def test_31_day_month(self):
        start, end = scheduled_period_for(date(2026, 8, 9), (9, 22))
        assert (start, end) == (date(2026, 7, 16), date(2026, 7, 31))

    def test_day1_rejected(self):
        with pytest.raises(PeriodError):
            scheduled_period_for(date(2026, 8, 1), (9, 22))

    def test_day16_rejected(self):
        with pytest.raises(PeriodError):
            scheduled_period_for(date(2026, 8, 16), (9, 22))

    def test_other_day_rejected(self):
        with pytest.raises(PeriodError):
            scheduled_period_for(date(2026, 8, 10), (9, 22))


class TestResolve:
    def test_as_of_day9(self):
        p = _resolve(as_of=date(2026, 8, 9))
        assert p.resolution_mode == "scheduled"
        assert p.period_start == date(2026, 7, 16)
        assert p.period_end == date(2026, 7, 31)
        assert p.scheduled_run_date == "2026-08-09"
        assert p.calendar_lag_days == 9
        assert p.full_preparation_days == 8
        d = p.to_dict()
        assert d["preparation_lag_days"] == 9
        assert d["preparation_lag_semantics"] == "calendar_date_difference"
        assert d["preparation_lag_deprecated"] is True
        assert p.period_definition == "natural_half_month"
        assert p.schedule_definition == "delayed_generation"

    def test_as_of_day22_lag(self):
        p = _resolve(as_of=date(2026, 8, 22))
        assert p.period_start == date(2026, 8, 1)
        assert p.period_end == date(2026, 8, 15)
        assert p.calendar_lag_days == 7
        assert p.full_preparation_days == 6

    def test_lag_programmatic_cross_year(self):
        p = _resolve(as_of=date(2027, 1, 9))
        assert p.period_end == date(2026, 12, 31)
        assert p.calendar_lag_days == 9
        assert p.full_preparation_days == 8

    def test_explicit_any_day(self):
        p = _resolve(explicit_start=date(2026, 7, 16), explicit_end=date(2026, 7, 31))
        assert p.resolution_mode == "explicit"
        assert p.period_start == date(2026, 7, 16)
        assert p.period_end == date(2026, 7, 31)
        assert p.scheduled_run_date == ""

    def test_explicit_missing_end_fails(self):
        with pytest.raises(PeriodError):
            _resolve(explicit_start=date(2026, 7, 16))

    def test_explicit_reversed_fails(self):
        with pytest.raises(PeriodError):
            _resolve(explicit_start=date(2026, 7, 31), explicit_end=date(2026, 7, 16))

    def test_asia_taipei_not_utc(self):
        # 2026-08-08 17:30 UTC == 2026-08-09 01:30 Asia/Taipei
        now = datetime(2026, 8, 8, 17, 30, tzinfo=timezone.utc)
        p = _resolve(now=now)
        assert p.run_date == "2026-08-09"
        assert p.period_start == date(2026, 7, 16)

    def test_previous_period(self):
        assert previous_period_for(date(2026, 7, 16), date(2026, 7, 31)) == (
            date(2026, 7, 1),
            date(2026, 7, 15),
        )
        assert previous_period_for(date(2026, 8, 1), date(2026, 8, 15)) == (
            date(2026, 7, 16),
            date(2026, 7, 31),
        )

    def test_timezone(self):
        assert get_timezone("Asia/Taipei") is not None


class TestConfigConsistency:
    def test_schedule_config_matches_code(self):
        schedule = CONFIG["schedule"]
        assert list(schedule["run_days"]) == [9, 22]
        assert schedule["periods"]["day_9"] == "previous_month_16_to_end"
        assert schedule["periods"]["day_22"] == "current_month_01_to_15"
        assert DEFAULT_PERIOD_RULES == {9: "previous_month_16_to_end", 22: "current_month_01_to_15"}

    def test_default_provider_deepseek(self):
        assert CONFIG["llm"]["default_provider"] == "deepseek"


class TestBusinessHashIncludesPeriodSemantics:
    def test_new_period_fields_change_business_hash(self):
        base = {"report_period": {"period_start": "2026-07-16", "period_end": "2026-07-31"}}
        with_fields = dict(base)
        with_fields["report_period"] = {
            "period_start": "2026-07-16",
            "period_end": "2026-07-31",
            "calendar_lag_days": 9,
            "full_preparation_days": 8,
        }
        assert business_hash(with_fields) != business_hash(base)
