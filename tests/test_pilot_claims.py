"""Program 4 claim extraction: validation, storage, re-run rules, review."""

from datetime import date, datetime, timezone

import pytest

from app.database import Database
from app.models import Article
from app.pilot import claims as cl

NOW = datetime(2026, 11, 14, 8, 0, tzinfo=timezone.utc)
TEXT = ("國防部今（14）日表示，今日上午6時起至明日上午6時，偵獲共機25架次、共艦7艘在臺海周邊活動。"
        "其中18架次逾越海峽中線進入我北部、西南空域。國防部長顧立雄說，國軍以聯合情監偵嚴密監控。")


def good(**over):
    claim = {
        "claim_type": "pla_activity", "actor": "共軍", "action": "共機逾越中線", "target": None,
        "location": "北部、西南空域", "event_date": "2026-11-14", "quantity": 18, "unit": "架次",
        "attribution": "國防部表示", "quote": "其中18架次逾越海峽中線進入我北部、西南空域",
    }
    claim.update(over)
    return claim


def test_quote_must_be_verbatim():
    assert cl.validate(good(), TEXT, date(2026, 11, 14)).valid
    c = cl.validate(good(quote="其中18架次越過海峽中線"), TEXT, date(2026, 11, 14))
    assert not c.valid and c.reject_reason == "quote_not_in_source"


def test_quote_tolerates_whitespace_and_fullwidth_only():
    source = "偵獲共機２５架次、共艦7艘\n在臺海周邊活動。"
    c = cl.validate(good(quote="偵獲共機25架次、共艦7艘在臺海周邊活動", quantity=25), source, None)
    assert c.valid and c.quote_start == 0
    assert cl.locate_quote("abc 國防部 表示今日", "國防部表示今日") == 4


def test_simplified_rewrite_of_quote_is_rejected():
    c = cl.validate(good(quote="其中18架次逾越海峡中线进入我北部、西南空域"), TEXT, None)
    assert c.reject_reason == "quote_not_in_source"


@pytest.mark.parametrize("over, reason", [
    ({"claim_type": "rumour"}, "claim_type:rumour"),
    ({"quote": "共機"}, "quote_too_short"),
    ({"event_date": "2026-11-20"}, "event_date_after_article"),
    ({"event_date": "11/14"}, "event_date_format"),
    ({"quantity": -3}, "quantity"),
    ({"quantity": "多架"}, "quantity"),
    ({"quantity": 2.5}, "quantity"),
])
def test_rejections(over, reason):
    c = cl.validate(good(**over), TEXT, date(2026, 11, 14))
    assert not c.valid and reason in c.reject_reason


def test_numeric_string_quantity_is_accepted():
    assert cl.validate(good(quantity="18"), TEXT, None).quantity == 18


class FakeClient:
    model = "deepseek/deepseek-v4-flash"

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def analyze(self, system, user):
        self.calls.append(user)
        return self.responses.pop(0)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "news.db")
    d.connect()
    d.save_articles([
        Article(source_id="mna", source_name="軍聞社", category="military", title="國防部：共機25架次",
                url="https://m/1", published_at=datetime(2026, 11, 14, 9), fetched_at=datetime(2026, 11, 14, 9),
                position=1, summary="國防部公布共軍動態。"),
        Article(source_id="cna", source_name="中央社", category="politics", title="立法院審查預算",
                url="https://p/1", published_at=datetime(2026, 11, 14, 9), fetched_at=datetime(2026, 11, 14, 9),
                position=1),
    ])
    d.save_article_texts({"https://m/1": TEXT}, extractor="trafilatura", fetched_at=NOW)
    yield d
    d.close()


def test_extraction_only_in_scope_and_once_per_text_version(db):
    client = FakeClient([{"status": "success", "claims": [good(), good(quote="編造的引文不存在於原文")]}])
    totals = cl.run_extraction(db.conn, client=client, now=NOW)
    assert totals == {"status": "ok", "articles": 1, "valid": 1, "rejected": 1, "failed": 0}
    assert len(client.calls) == 1 and '"text": "國防部今' in client.calls[0]
    assert cl.run_extraction(db.conn, client=FakeClient([]), now=NOW)["articles"] == 0
    rows = cl.claims_between(db.conn, date(2026, 11, 14), date(2026, 11, 14))
    assert len(rows) == 1 and rows[0]["source_field"] == "text" and rows[0]["quantity"] == 18
    assert len(cl.claims_between(db.conn, date(2026, 11, 14), date(2026, 11, 14), include_invalid=True)) == 2


def test_changed_text_reextracts_but_keeps_reviewed_claims(db):
    cl.run_extraction(db.conn, client=FakeClient([{"status": "success", "claims": [good()]}]), now=NOW)
    claim_id = cl.claims_between(db.conn, date(2026, 11, 14), date(2026, 11, 14))[0]["id"]
    assert cl.set_review(db.conn, claim_id, "confirmed", "analyst")
    db.save_article_texts({"https://m/1": TEXT + "國軍持續監控。"}, extractor="trafilatura", fetched_at=NOW)
    cl.run_extraction(db.conn, client=FakeClient([{"status": "success", "claims": [good()]}]), now=NOW)
    statuses = sorted(r["review_status"] for r in cl.claims_between(db.conn, date(2026, 11, 14), date(2026, 11, 14)))
    assert statuses == ["confirmed", "unreviewed"]


def test_failed_calls_are_retried_next_run_and_budget_stops_the_run(db):
    out = cl.run_extraction(db.conn, client=FakeClient([{"status": "error", "error": "budget_exceeded"}]), now=NOW)
    assert out["status"] == "budget_exceeded" and out["failed"] == 1
    again = FakeClient([{"status": "success", "claims": []}])
    assert cl.run_extraction(db.conn, client=again, now=NOW)["articles"] == 1


def test_rejected_review_hides_claim_and_bad_status_raises(db):
    cl.run_extraction(db.conn, client=FakeClient([{"status": "success", "claims": [good()]}]), now=NOW)
    cid = cl.claims_between(db.conn, date(2026, 11, 14), date(2026, 11, 14))[0]["id"]
    cl.set_review(db.conn, cid, "rejected", "analyst")
    assert cl.claims_between(db.conn, date(2026, 11, 14), date(2026, 11, 14)) == []
    with pytest.raises(ValueError):
        cl.set_review(db.conn, cid, "approved", "analyst")


def test_run_hook_off_by_default_and_safe(db, monkeypatch):
    assert cl.run_safe(db) == {"status": "disabled"}
    monkeypatch.setenv("PILOT_EXTRACTION_ENABLED", "true")
    monkeypatch.delenv("SUMMARIZER_API_KEY", raising=False)
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)
    assert cl.run_safe(db)["status"] == "no_model_configured"


def test_prompt_exists_and_is_versioned():
    assert cl.prompt_version() != "missing" and len(cl.prompt_version()) == 12
