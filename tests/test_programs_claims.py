"""Claim extraction for programs other than P4: kinds, evidence ladder, scoping."""

from datetime import date, datetime, timezone

import pytest

from app.database import Database
from app.models import Article
from app.pilot import claims as cl
from app.programs import tagging

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)
TEXT = ("美國國防安全合作署今日通知國會，擬對台出售價值20億美元的防空系統。"
        "國務院發言人表示，美方對台承諾堅若磐石。據傳某議員曾私下反對此案。")


class Fake:
    model = "flash"

    def __init__(self, claims):
        self.claims, self.prompts = claims, []

    def analyze(self, system, user):
        self.prompts.append(system)
        return {"status": "success", "claims": self.claims}


def c(**over):
    base = {"claim_type": "decision", "kind": "fact", "ladder": "decision", "actor": "美國國防安全合作署",
            "action": "通知國會對台軍售", "quantity": 20, "unit": "億美元", "event_date": "2026-11-14",
            "question_ids": ["P3-Q2", "P1-Q4"], "quote": "美國國防安全合作署今日通知國會"}
    base.update(over)
    return base


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "n.db")
    d.connect()
    d.save_articles([Article(source_id="s", source_name="中央社", category="international",
                             title="美國宣布對台軍售 DSCA 通知國會", url="https://x/1",
                             published_at=datetime(2026, 11, 14, 8), fetched_at=datetime(2026, 11, 14, 8), position=1)])
    d.save_article_texts({"https://x/1": TEXT}, extractor="t", fetched_at=NOW)
    tagging.tag_range(d.conn, datetime(2026, 11, 13, tzinfo=timezone.utc), NOW, now=NOW)
    yield d
    d.close()


def test_generic_prompt_names_the_program_questions():
    prompt = cl.system_prompt("P3")
    assert "P3 美國與盟友防衛支持" in prompt and "P3-Q2" in prompt and '{"claims": [ ... ]}' in prompt
    assert cl.prompt_version("P3") != cl.prompt_version("P1") != cl.prompt_version("P4")


def test_validation_of_kind_ladder_and_attribution():
    ok = cl.validate(c(), TEXT, date(2026, 11, 14), "P3")
    assert ok.valid and ok.ladder == "decision" and ok.question_ids == ("P3-Q2",)
    assert "kind_or_ladder_missing" in cl.validate(c(kind=None), TEXT, None, "P3").reject_reason
    assert "ladder:promise" in cl.validate(c(ladder="promise"), TEXT, None, "P3").reject_reason
    stmt = c(claim_type="statement", kind="attributed_statement", ladder="rhetoric",
             quote="國務院發言人表示，美方對台承諾堅若磐石", attribution=None)
    assert "attribution_missing" in cl.validate(stmt, TEXT, None, "P3").reject_reason
    assert "claim_type:pla_activity" in cl.validate(c(claim_type="pla_activity"), TEXT, None, "P3").reject_reason
    assert cl.validate(c(kind=None, ladder=None, claim_type="arms"), TEXT, None, "P4").valid  # P4 unchanged


def test_extraction_uses_program_tags_and_scopes_by_program(db):
    fake = Fake([c(), c(claim_type="other", kind="allegation", ladder="rhetoric", quote="據傳某議員曾私下反對此案",
                        quantity=None, unit=None)])
    out = cl.run_extraction(db.conn, client=fake, now=NOW, program="P3")
    assert out["articles"] == 1 and out["valid"] == 2
    assert "P3 美國與盟友防衛支持" in fake.prompts[0]
    day = date(2026, 11, 14)
    visible = cl.claims_between(db.conn, day, day, program="P3")
    assert [r["kind"] for r in visible] == ["fact"]  # allegation hidden until confirmed
    allegation = [r for r in cl.claims_between(db.conn, day, day, program="P3",
                                               include_unconfirmed_allegations=True) if r["kind"] == "allegation"][0]
    cl.set_review(db.conn, allegation["id"], "confirmed", "analyst")
    assert len(cl.claims_between(db.conn, day, day, program="P3")) == 2
    assert cl.claims_between(db.conn, day, day, program="P4") == []
    q = db.conn.execute("SELECT question_id FROM claim_questions").fetchall()
    assert ("P3-Q2",) in q
    # P1 extraction on the same article does not delete P3 claims
    cl.run_extraction(db.conn, client=Fake([c(question_ids=["P1-Q4"])]), now=NOW, program="P1")
    assert len(cl.claims_between(db.conn, day, day, program="P3")) == 2
    assert len(cl.claims_between(db.conn, day, day, program="P1")) == 1


def test_untagged_program_has_nothing_to_extract(db):
    assert cl.run_extraction(db.conn, client=Fake([]), now=NOW, program="P6")["articles"] == 0


def test_run_hook_runs_listed_programs(db, monkeypatch):
    monkeypatch.setenv("PILOT_EXTRACTION_ENABLED", "true")
    monkeypatch.setenv("PILOT_EXTRACTION_PROGRAMS", "P1,P3")
    fake = Fake([c()])
    out = cl.run_safe(db, client=fake, now=NOW)
    assert set(out["programs"]) == {"P1", "P3"} and out["status"] == "ok"
