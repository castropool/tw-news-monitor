"""Program 4 event grouping on top of the existing military event pipeline."""

from datetime import date, datetime, timezone

import pytest

from app.database import Database
from app.models import Article
from app.pilot import claims as cl
from app.pilot import events as pe
from app.pilot import weekly as wk

NOW = datetime(2026, 11, 16, 1, tzinfo=timezone.utc)
QUOTE = "共機45架次、共艦7艘在臺海周邊活動"


def art(url, title, source="中央社", hour=9, category="military"):
    return Article(source_id="s", source_name=source, category=category, title=title, url=url,
                   published_at=datetime(2026, 11, 13, hour), fetched_at=datetime(2026, 11, 13, hour),
                   position=1)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "n.db")
    d.connect()
    d.save_articles([
        art("https://a/1", "國防部：共機45架次、共艦7艘擾台", "中央社", 9),
        art("https://a/2", "共機45架次、共艦7艘擾台 國防部嚴密監控", "自由時報", 10),
        art("https://a/3", "立法院三讀國防預算", "聯合報", 11, category="politics"),
    ])
    d.save_article_texts({"https://a/1": f"國防部今日表示，{QUOTE}，國軍嚴密監控。",
                          "https://a/2": f"根據國防部公布，{QUOTE}。"}, extractor="t", fetched_at=NOW)
    fake = type("C", (), {"model": "flash", "analyze": lambda self, s, u: {"status": "success", "claims": [
        {"claim_type": "pla_activity", "actor": "共軍", "action": "共機擾台", "event_date": "2026-11-13",
         "quantity": 45, "unit": "架次", "quote": QUOTE}]}})()
    cl.run_extraction(d.conn, client=fake, now=NOW)
    yield d
    d.close()


def test_week_articles_are_in_scope_only(db):
    urls = {a.url for a in pe.week_articles(db.conn, date(2026, 11, 9), date(2026, 11, 15))}
    assert urls == {"https://a/1", "https://a/2"}


def test_same_story_from_two_outlets_is_one_event_with_its_fact(db):
    facts = cl.claims_between(db.conn, date(2026, 11, 9), date(2026, 11, 15))
    events = pe.week_events(db.conn, date(2026, 11, 9), date(2026, 11, 15), facts)
    assert len(events) == 1
    e = events[0]
    assert e["article_count"] == 2 and e["fact_numbers"] == [1, 2] and e["merged_by_facts"]
    md = pe.render_events_markdown(events)
    assert "2 篇" in md and "事實：[1]" in md and "中央社" in md


def test_no_articles_and_broken_rules_degrade_to_no_events(db, monkeypatch):
    assert pe.week_events(db.conn, date(2025, 1, 1), date(2025, 1, 7), []) == []
    import app.military.config as mc

    monkeypatch.setattr(mc, "load_rules", lambda *a, **k: (_ for _ in ()).throw(ValueError("bad rules")))
    assert pe.week_events(db.conn, date(2026, 11, 9), date(2026, 11, 15), []) == []
    assert "沒有可歸併" in pe.render_events_markdown([])


def test_weekly_draft_lists_events(db):
    md = wk.build_draft(db.conn, date(2026, 11, 15), use_llm=False, now=NOW).markdown
    assert "### 本週事件" in md and "事實：[1][2]" in md


def test_vague_facts_do_not_merge():
    events = [{"event_id": f"e{i}", "importance": 45, "article_count": 1, "articles": [{"url": f"u{i}"}],
               "sources": [f"s{i}"], "fact_numbers": [i + 1], "first_seen": "2026-11-13T09:00",
               "last_updated": "2026-11-13T09:00"} for i in range(2)]
    facts = [{"claim_type": "statement", "event_date": "2026-11-13", "quantity": None, "actor": "國防部"}] * 2
    assert len(pe.merge_by_shared_facts(events, facts)) == 2
