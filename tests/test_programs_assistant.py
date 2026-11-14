"""Research assistant: retrieval with record ids, refusal, citation checks, eval set."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.database import Database
from app.models import Article
from app.pilot import claims as cl
from app.programs import assistant as ra
from app.programs import registry as rg

NOW = datetime.now(timezone.utc)
DAY = (NOW - timedelta(days=1)).date()
QUOTE = "其中18架次逾越海峽中線進入我北部、西南空域"


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "n.db")
    d.connect()
    when = datetime.combine(DAY, datetime.min.time()).replace(hour=9)
    d.save_articles([
        Article(source_id="s", source_name="中央社", category="military", title="國防部：共機25架次擾台 18架次逾越中線",
                url="https://x/1", published_at=when, fetched_at=when, position=1),
        Article(source_id="s", source_name="自由時報", category="military", title="共機逾越中線 國軍嚴密監控",
                url="https://x/2", published_at=when, fetched_at=when, position=1),
    ])
    d.save_article_texts({"https://x/1": f"國防部表示，偵獲共機25架次，{QUOTE}。"}, extractor="t", fetched_at=NOW)
    fake = type("C", (), {"model": "flash", "analyze": lambda self, s, u: {"status": "success", "claims": [
        {"claim_type": "pla_activity", "actor": "共軍", "action": "共機逾越中線", "event_date": DAY.isoformat(),
         "quantity": 18, "unit": "架次", "quote": QUOTE}]}})()
    cl.run_extraction(d.conn, client=fake, now=NOW)
    yield d
    d.close()


class Fake:
    model = "deepseek/deepseek-v4-pro"

    def __init__(self, result):
        self.result, self.calls = result, []

    def analyze(self, system, user):
        self.calls.append(user)
        return self.result


def test_terms_detected_from_question_and_registry(db):
    rg.upsert_entity(db.conn, "person", "顧立雄", aliases=["國防部長顧立雄"])
    terms = ra.question_terms(db.conn, "顧立雄怎麼看共機逾越海峽中線？")
    assert "顧立雄" in terms and "海峽中線" in terms and "共機" in terms


def test_retrieval_returns_facts_articles_with_ids(db):
    recs = ra.retrieve(db.conn, ["共機", "中線"])
    ids = [r.id for r in recs]
    assert ids[0].startswith("C") and any(i.startswith("A") for i in ids)
    assert QUOTE in recs[0].text


def test_insufficient_evidence_refuses_without_model(db):
    fake = Fake({"status": "success"})
    a = ra.ask(db.conn, "2030年會怎樣？", terms=["不存在的詞彙組合"], client=fake)
    assert a.insufficient and a.sentences[0].text.startswith("證據不足") and fake.calls == []


def test_answer_is_checked_against_cited_records(db):
    recs = ra.retrieve(db.conn, ["共機", "中線"])
    cid = next(r.id for r in recs if r.id.startswith("C"))
    fake = Fake({"status": "success", "insufficient": False, "answer": [
        {"text": "最近一天共機有18架次逾越中線。", "citations": [cid]},
        {"text": "數量達99架次。", "citations": [cid]},
        {"text": "情勢升高。", "citations": []},
        {"text": "見報導。", "citations": ["A999"]},
    ]})
    a = ra.ask(db.conn, "共機逾越中線情況？", terms=["共機", "中線"], client=fake)
    notes = [s.notes for s in a.sentences]
    assert notes[0] == [] and "未核實數字 ['99']" in notes[1]
    assert "無引用" in notes[2] and any("A999" in n for n in notes[3])
    out = ra.render(a)
    assert f"[{cid}]" in out and "⚠" in out and "記錄（" in out


def test_eval_harness(db):
    report = ra.evaluate(db.conn, ra.load_eval())
    rows = {r["id"]: r for r in report["questions"]}
    assert rows["p4-crossings"]["recall"] == 1.0
    assert rows["unanswerable-forecast"]["insufficient"] is True
    assert report["refusal_rate_when_expected"] == 1.0


def test_cli_retrieval_only(db, capsys):
    path = str(db._db_path)
    db.close()
    assert ra.main(["ask", "共機逾越中線？", "--terms", "共機,中線", "--no-llm", "--db", path]) == 0
    out = capsys.readouterr().out
    assert "檢索詞：共機、中線" in out and "未使用模型" in out
    db.connect()
