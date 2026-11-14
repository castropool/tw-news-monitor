"""Research gaps and human-approved search proposals."""

from datetime import date, datetime, timezone

import pytest

from app.database import Database
from app.models import Article
from app.programs import leads, tagging

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "n.db")
    d.connect()
    yield d
    d.close()


class FakeCollector:
    calls = []

    def __init__(self, source):
        self.source = source
        FakeCollector.calls.append(source)

    def collect(self):
        return [Article(source_id=self.source["id"], source_name="中央社", category=self.source["category"],
                        title="國會通過涉台條款", url="https://x/lead1", published_at=datetime(2026, 11, 13),
                        fetched_at=datetime(2026, 11, 13), position=1)]


def test_every_question_is_a_gap_without_facts(db):
    rows = leads.gaps(db.conn, "P1", today=NOW.date())
    assert [r["question_id"] for r in rows] == ["P1-Q1", "P1-Q2", "P1-Q3", "P1-Q4"] and all(r["gap"] for r in rows)


def test_proposals_are_not_duplicated_and_nothing_runs_unapproved(db):
    assert leads.propose(db.conn, "P1", now=NOW) == 4
    assert leads.propose(db.conn, "P1", now=NOW) == 0
    FakeCollector.calls = []
    assert leads.run_approved(db, collector_factory=FakeCollector, now=NOW) == []
    assert FakeCollector.calls == []
    first = leads.list_leads(db.conn)[-1]
    assert first["status"] == "proposed" and "近 14 天沒有" in first["reason"]


def test_approved_proposal_runs_once_saves_and_tags(db):
    leads.propose(db.conn, "P1", now=NOW)
    lead = leads.list_leads(db.conn)[-1]
    assert leads.decide(db.conn, lead["id"], "approved", "analyst", query="NDAA 台灣 條款", now=NOW)
    assert not leads.decide(db.conn, lead["id"], "rejected", "analyst")  # already decided
    out = leads.run_approved(db, collector_factory=FakeCollector, now=NOW)
    assert out == [{"id": lead["id"], "status": "executed", "found": 1, "new": 1}]
    assert "NDAA" in FakeCollector.calls[-1]["url"] and FakeCollector.calls[-1]["category"] == "international"
    assert tagging.programs_for(db.conn, "https://x/lead1") == ["P1"]
    assert db.count_articles() == 1
    assert leads.run_approved(db, collector_factory=FakeCollector, now=NOW) == []  # executed once


def test_only_allowed_hosts(db, monkeypatch):
    monkeypatch.setenv("LEADS_ALLOWED_HOSTS", "example.org")
    leads.propose(db.conn, "P2", now=NOW)
    lead = leads.list_leads(db.conn)[-1]
    leads.decide(db.conn, lead["id"], "approved", "analyst")
    assert leads.run_approved(db, collector_factory=FakeCollector, now=NOW)[0]["status"] == "host_not_allowed"


def test_query_built_from_indicator():
    assert leads._query_from_indicator("NDAA 與撥款法案台灣條款") == "NDAA 撥款法案台灣條款 台灣"
