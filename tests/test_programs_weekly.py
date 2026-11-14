"""Weekly drafts for programs other than P4: ladder, matrix, checks, approval."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.database import Database
from app.models import Article
from app.pilot import claims as cl
from app.pilot import weekly as pw
from app.programs import tagging
from app.programs import weekly as gw

NOW = datetime(2026, 11, 16, 1, tzinfo=timezone.utc)
END = date(2026, 11, 15)
TEXT = ("美國國防安全合作署今日通知國會，擬對台出售價值20億美元的防空系統。"
        "國務院發言人表示，美方對台承諾堅若磐石。")


def claim(**over):
    base = {"claim_type": "decision", "kind": "fact", "ladder": "decision", "actor": "美國國防安全合作署",
            "action": "通知國會對台軍售", "quantity": 20, "unit": "億美元", "event_date": "2026-11-13",
            "question_ids": ["P3-Q2"], "quote": "美國國防安全合作署今日通知國會"}
    base.update(over)
    return base


class Fake:
    model = "deepseek/deepseek-v4-pro"

    def __init__(self, result):
        self.result, self.calls = result, []

    def analyze(self, system, user):
        self.calls.append((system, user))
        return self.result


def add_article(d, url, day, text):
    d.save_articles([Article(source_id="s", source_name="中央社", category="international",
                             title="美國宣布對台軍售 DSCA 通知國會", url=url, published_at=datetime(2026, 11, day, 8),
                             fetched_at=datetime(2026, 11, day, 8), position=1)])
    d.save_article_texts({url: text}, extractor="t", fetched_at=NOW)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "n.db")
    d.connect()
    add_article(d, "https://x/old", 1, TEXT)
    add_article(d, "https://x/new", 13, TEXT)
    tagging.tag_range(d.conn, datetime(2026, 10, 1, tzinfo=timezone.utc), NOW, now=NOW)
    old_fact = claim(ladder="rhetoric", claim_type="statement", kind="attributed_statement",
                     attribution="國務院發言人", quote="國務院發言人表示，美方對台承諾堅若磐石", event_date="2026-11-01")
    per_url = {"https://x/old": old_fact, "https://x/new": claim()}

    class ByUrl:
        model = "flash"

        def analyze(self, system, user):
            url = next(u for u in per_url if u in user)
            return {"status": "success", "claims": [per_url[url]]}

    cl.run_extraction(d.conn, client=ByUrl(), now=NOW, program="P3", days=30)
    yield d
    d.close()


def test_ladder_climb_is_flagged(db):
    facts = cl.claims_between(db.conn, END - timedelta(days=6), END, program="P3")
    rows = {r.question: r for r in gw.ladder_table(db.conn, "P3", END - timedelta(days=6), END, facts)}
    assert rows["P3-Q2"].top == "decision" and rows["P3-Q2"].previous_top == "rhetoric" and rows["P3-Q2"].climbed
    assert rows["P3-Q1"].top is None and not rows["P3-Q1"].climbed


def test_matrix_parsing_drops_unknowns_and_tallies():
    m = gw.parse_matrix([{"fact": 1, "H1": "+", "H2": "-", "H9": "+"}, {"fact": 7, "H1": "+"},
                         {"fact": 2, "H1": "?"}, "junk"], 2, ["H1", "H2", "H3"])
    assert m == {1: {"H1": "+", "H2": "-"}}
    assert gw.tally_matrix(m, ["H1", "H2", "H3"])["H2"] == {"支持": 0, "不支持": 1}


def test_draft_with_model(db):
    fake = Fake({"status": "success",
                 "judgments": [{"text": "美方通知國會20億美元軍售，支持從表態走向正式決定。", "citations": [1],
                                "uses_data": True, "confidence": "中"},
                               {"text": "支持金額達99億。", "citations": [1], "confidence": "高"}],
                 "matrix": [{"fact": 1, "H1": "+", "H2": "-", "H3": "0", "H4": "-"}]})
    d = gw.build_draft(db.conn, "P3", END, client=fake, now=NOW)
    md = d.markdown
    assert "美國與盟友防衛支持週報（草稿）2026-11-09 至 2026-11-15" in md
    assert "| P3-Q2 | 正式決定 1 | 正式決定 ⬆ | 表態 |" in md
    assert "| [1] | 支持 | 削弱 | 無關 | 削弱 |" in md and "H1 支持 1、削弱 0" in md
    assert "⚠ 自動檢查：未核實數字 ['99']" in md
    assert "### P3-Q2" in md and "［決定／正式決定］" in md
    assert "P3-Q1 本週沒有相關事實" in md
    system, user = fake.calls[0]
    assert "P3 美國與盟友防衛支持" in system and '"ladder_table"' in user
    assert d.state["tally"]["H1"] == {"支持": 1, "不支持": 0} and d.program == "P3"


def test_files_are_per_program_and_approval_rules_apply(db):
    d = gw.build_draft(db.conn, "P3", END, use_llm=False, now=NOW)
    folder = pw.write_draft(d, END, now=NOW)
    assert folder.parent.name == "p3"
    assert pw.read_meta(END, "P3")["program"] == "P3" and pw.read_meta(END) is None
    assert pw.decide(END, "approved", "analyst", now=NOW, program="P3")["status"] == "approved"


def test_hypothesis_shift_suggestion_next_week(db):
    d1 = gw.build_draft(db.conn, "P3", END, client=Fake({"status": "success", "judgments": [],
                        "matrix": [{"fact": 1, "H1": "+", "H2": "+", "H3": "+", "H4": "+"}]}), now=NOW)
    pw.write_draft(d1, END, now=NOW)
    pw.decide(END, "approved", "analyst", now=NOW, program="P3")
    add_article(db, "https://x/next", 20, TEXT)
    tagging.tag_range(db.conn, datetime(2026, 11, 19, tzinfo=timezone.utc), NOW + timedelta(days=7), now=NOW)
    fake = type("C", (), {"model": "flash", "analyze": lambda self, s, u: {"status": "success", "claims": [claim(event_date="2026-11-20")]}})()
    cl.run_extraction(db.conn, client=fake, now=NOW + timedelta(days=7), program="P3", days=3)
    d2 = gw.build_draft(db.conn, "P3", END + timedelta(days=7), client=Fake({"status": "success", "judgments": [],
                        "matrix": [{"fact": 1, "H1": "-", "H2": "-", "H3": "-", "H4": "-"}]}), now=NOW)
    assert any(r["kind"] == "hypothesis_shift" for r in d2.revisions)


def test_p4_is_refused_and_no_facts_needs_no_model(db):
    with pytest.raises(ValueError):
        gw.build_draft(db.conn, "P4", END)
    d = gw.build_draft(db.conn, "P6", END, client=Fake({"status": "error"}), now=NOW)
    assert "未生成：本週無有效事實" in d.markdown


def test_cli(db, capsys):
    path = str(db._db_path)
    db.close()
    assert gw.main(["generate", "--program", "P3", "--week-ending", "2026-11-15", "--no-llm", "--db", path]) == 0
    assert "Draft written" in capsys.readouterr().out
    db.connect()
