"""Daily cross-program digest/alerts and the P4 spike backtest."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.database import Database
from app.military import pla_activity as pla
from app.military import pla_backtest as bt
from app.models import Article
from app.pilot import claims as cl
from app.programs import daily, tagging

DAY = date(2026, 11, 13)
NOW = datetime(2026, 11, 14, 2, tzinfo=timezone.utc)


def roc(d):
    return f"中華民國{d.year - 1911}年{d.month}月{d.day}日"


def store_series(conn, end, days=150, spikes=()):
    reports = []
    for i in range(days):
        d = end - timedelta(days=days - 1 - i)
        n = 60 if d in spikes else 10 + (i % 5) - 2
        text = (f"一、日期：{roc(d - timedelta(days=1))}0600時至{roc(d)}0600時止。\n"
                f"二、臺海周邊海、空域動態：共機{n}架次、共艦6艘。")
        reports.append(pla.parse_report_text(text, f"u{i}"))
    pla.save_reports(conn, reports)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "n.db")
    d.connect()
    store_series(d.conn, DAY, spikes=(DAY,))
    when = datetime(2026, 11, 13, 9)
    d.save_articles([
        Article(source_id="s", source_name="中央社", category="international", title="美國宣布對台軍售 DSCA 通知國會",
                url="https://x/1", published_at=when, fetched_at=when, position=1),
    ])
    d.save_article_texts({"https://x/1": "美國國防安全合作署今日通知國會，擬對台出售防空系統。"}, extractor="t", fetched_at=NOW)
    tagging.tag_range(d.conn, datetime(2026, 11, 12, tzinfo=timezone.utc), NOW, now=NOW)
    fake = type("C", (), {"model": "flash", "analyze": lambda self, s, u: {"status": "success", "claims": [
        {"claim_type": "decision", "kind": "fact", "ladder": "decision", "actor": "美國國防安全合作署",
         "action": "通知國會對台軍售", "event_date": "2026-11-13", "question_ids": ["P3-Q2"],
         "quote": "美國國防安全合作署今日通知國會"}]}})()
    cl.run_extraction(d.conn, client=fake, now=NOW, program="P3")
    yield d
    d.close()


def test_daily_items_and_alert_classes(db):
    items = daily.collect(db.conn, DAY)
    classes = {i["class"] for i in items}
    assert {"pla_anomaly", "ladder_formal", "cross_program"} <= classes
    cross = next(i for i in items if i["class"] == "cross_program")
    assert cross["programs"] == ["P1", "P3"]
    assert {i["class"] for i in items if i["alert"]} == {"pla_anomaly", "ladder_formal"}


def test_alert_classes_configurable(db, monkeypatch):
    monkeypatch.setenv("DAILY_ALERT_CLASSES", "cross_program,unknown")
    assert {i["class"] for i in daily.collect(db.conn, DAY) if i["alert"]} == {"cross_program"}


def test_daily_render_and_write(db):
    items = daily.collect(db.conn, DAY)
    md = daily.render(DAY, items, NOW)
    assert "# 每日戰略摘要 2026-11-13" in md and "⚠ [P4] 共機架次 60" in md and "## 跨計畫事件（1）" in md
    path = daily.write(DAY, md, items)
    assert path.exists() and path.with_suffix(".json").exists()


def test_quiet_day_has_no_alerts(db):
    items = daily.collect(db.conn, DAY - timedelta(days=3))
    assert not any(i["alert"] for i in items)
    assert "## 警示\n\n- 無" in daily.render(DAY - timedelta(days=3), items, NOW)


def test_backtest_recall_precision_and_misses(db):
    series = bt.pb.load_series(db.conn)
    labels = [{"name": "演習甲", "start": DAY, "end": DAY, "confirmed": True},
              {"name": "演習乙", "start": DAY - timedelta(days=20), "end": DAY - timedelta(days=20), "confirmed": False}]
    r = bt.backtest(series, labels, DAY - timedelta(days=30), DAY)
    assert r["labels"] == 2 and r["spike_days"] == 1
    assert r["recall"] == 0.5 and r["precision"] == 1.0 and r["missed"][0].endswith("演習乙")
    r2 = bt.backtest(series, labels, DAY - timedelta(days=30), DAY, confirmed_only=True)
    assert r2["labels"] == 1 and r2["recall"] == 1.0


def test_seed_labels_load_and_are_unconfirmed():
    labels = bt.load_labels()
    assert labels and all(not l["confirmed"] for l in labels)
