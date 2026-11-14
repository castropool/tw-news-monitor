"""Scheduled Sunday drafts and once-only Feishu delivery (approved weeks, daily digest, alerts)."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.notifier import RecordingNotifier
from app.pilot import weekly as pw
from app.programs import delivery as dl
from tests import test_programs_daily_backtest as base  # db fixture: PLA spike on 2026-11-13, P3 decision fact

db = base.db
DAY = base.DAY  # 2026-11-13 (Friday)


def at_taipei(d: date, hour: int) -> datetime:
    return datetime(d.year, d.month, d.day, hour, tzinfo=timezone(timedelta(hours=8))).astimezone(timezone.utc)


@pytest.fixture
def delivering(monkeypatch):
    monkeypatch.setenv("PROGRAM_DELIVERY_ENABLED", "true")
    monkeypatch.delenv("DISABLE_FEISHU_SEND", raising=False)


def test_everything_off_by_default(db):
    rec = RecordingNotifier()
    assert dl.run(db, now=at_taipei(DAY + timedelta(days=1), 10), notifier=rec) == {}
    assert rec.messages == []


def test_alerts_sent_immediately_and_only_once(db, delivering):
    rec = RecordingNotifier()
    now = at_taipei(DAY, 7)  # before the digest hour: alerts only
    out = dl.run(db, now=now, notifier=rec)
    assert len(out["sent"]) == 2 and all(k.startswith("alert:") for k in out["sent"])
    assert any("共機架次 60" in m for m in rec.messages) and any("正式決定" in m for m in rec.messages)
    assert dl.run(db, now=now, notifier=rec)["sent"] == []
    assert len(rec.messages) == 2


def test_daily_digest_after_the_hour_once(db, delivering):
    rec = RecordingNotifier()
    morning = at_taipei(DAY + timedelta(days=1), 8)
    out = dl.run(db, now=morning, notifier=rec)
    assert f"daily:{DAY}" in out["sent"]
    digest = next(m for m in rec.messages if "每日戰略摘要" in m)
    assert "未經分析員審核" in digest and "跨計畫事件" in digest
    assert f"daily:{DAY}" not in dl.run(db, now=morning + timedelta(hours=1), notifier=rec)["sent"]


def test_disable_feishu_send_holds_messages_back(db, delivering, monkeypatch):
    monkeypatch.setenv("DISABLE_FEISHU_SEND", "1")
    rec = RecordingNotifier()
    assert dl.run(db, now=at_taipei(DAY, 7), notifier=rec)["sent"] == [] and rec.messages == []
    monkeypatch.delenv("DISABLE_FEISHU_SEND")
    assert len(dl.run(db, now=at_taipei(DAY, 7), notifier=rec)["sent"]) == 2


def test_failed_send_is_retried_next_run(db, delivering):
    class Broken(RecordingNotifier):
        def send(self, text):
            return False

    assert dl.run(db, now=at_taipei(DAY, 7), notifier=Broken())["sent"] == []
    assert len(dl.run(db, now=at_taipei(DAY, 7), notifier=RecordingNotifier())["sent"]) == 2


def test_sunday_auto_draft_and_only_approved_weeks_are_sent(db, delivering, monkeypatch):
    monkeypatch.setenv("WEEKLY_AUTO_DRAFT", "true")
    monkeypatch.setenv("WEEKLY_PROGRAMS", "P4,P3")
    monkeypatch.setenv("SUMMARIZER_API_KEY", "")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "")
    sunday = DAY + timedelta(days=2)  # 2026-11-15
    saturday = DAY + timedelta(days=1)
    rec = RecordingNotifier()
    assert dl.run(db, now=at_taipei(sunday, 8), notifier=rec)["drafted"] == []  # before 09:00
    out = dl.run(db, now=at_taipei(sunday, 9), notifier=rec)
    assert out["drafted"] == ["P4", "P3"]
    assert pw.read_meta(saturday)["status"] == "draft" and pw.read_meta(saturday, "P3")["status"] == "draft"
    assert not any("週報" in m for m in rec.messages)  # drafts are never sent
    assert dl.run(db, now=at_taipei(sunday, 10), notifier=rec)["drafted"] == []  # not redrafted
    pw.decide(saturday, "approved", "analyst")
    out = dl.run(db, now=at_taipei(sunday, 11), notifier=rec)
    assert f"weekly:P4:{saturday}" in out["sent"]
    weekly = next(m for m in rec.messages if "週報（已經分析員核准）" in m)
    assert "P4 台海安全與突發情勢" in weekly
    assert f"weekly:P4:{saturday}" not in dl.run(db, now=at_taipei(sunday, 12), notifier=rec)["sent"]


def test_last_saturday():
    assert dl.last_saturday(date(2026, 11, 15)) == date(2026, 11, 14)
    assert dl.last_saturday(date(2026, 11, 14)) == date(2026, 11, 7)


def test_run_safe_never_raises(db, delivering):
    class Exploding:
        def send_long(self, text):
            raise RuntimeError("network")

    out = dl.run_safe(db, now=at_taipei(DAY, 7), notifier=Exploding())
    assert out.get("sent") == []
