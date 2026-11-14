"""Revision suggestions against the last approved weekly assessment."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.pilot import claims as cl
from app.pilot import revisions as rv
from app.pilot import weekly as wk

from tests import test_pilot_weekly as base  # fixtures and helpers: series, article, claims

db = base.db
NOW = base.NOW
WEEK1 = date(2026, 11, 15)
WEEK2 = WEEK1 + timedelta(days=7)


def prev(**over):
    meta = {"week_end": "2026-11-15", "status": "approved",
            "tally": {"H1": {"支持": 1, "不支持": 0}, "H2": {"支持": 3, "不支持": 0}},
            "aircraft_ratio": 1.5, "judgments": [], "fact_claim_ids": []}
    meta.update(over)
    return meta


def test_no_previous_assessment_means_no_suggestions(db):
    assert rv.suggest(db.conn, None, {"tally": {}}) == []
    assert "尚無已核准" in rv.render_markdown([], None)


def test_hypothesis_shift_and_regime_change(db):
    current = {"tally": {"H1": {"支持": 1, "不支持": 0}, "H2": {"支持": 0, "不支持": 1}}, "aircraft_ratio": 0.7}
    s = rv.suggest(db.conn, prev(), current)
    kinds = [x["kind"] for x in s]
    assert kinds == ["hypothesis_shift", "activity_regime"]
    assert "H2 的指標淨支持由 +3 變為 -1" in s[0]["text"]
    assert "「偏高」（1.50× 基線）轉為「偏低」（0.70×）" in s[1]["text"]
    assert all(x["status"] == "open" for x in s) and [x["id"] for x in s] == [1, 2]


def test_small_changes_raise_nothing(db):
    current = {"tally": {"H1": {"支持": 2, "不支持": 0}, "H2": {"支持": 2, "不支持": 0}}, "aircraft_ratio": 1.4}
    assert rv.suggest(db.conn, prev(), current) == []


def test_withdrawn_evidence(db):
    claim_id = cl.claims_between(db.conn, date(2026, 11, 9), WEEK1)[0]["id"]
    p = prev(judgments=[{"text": "共機逾越中線18架次。", "citations": [1]}], fact_claim_ids=[claim_id])
    assert rv.suggest(db.conn, p, {"tally": {}, "aircraft_ratio": None}) == []
    cl.set_review(db.conn, claim_id, "rejected", "analyst")
    s = rv.suggest(db.conn, p, {"tally": {}, "aircraft_ratio": None})
    assert s[0]["kind"] == "evidence_withdrawn" and "第 1 條要點所引事實 [1]" in s[0]["text"]


def test_full_cycle_blocks_approval_until_decided(db):
    judgment = {"text": "本週共機逾越中線18架次。", "citations": [1], "uses_data": False, "confidence": "中"}
    d1 = wk.build_draft(db.conn, WEEK1, client=base.FakeClient({"status": "success", "judgments": [judgment]}), now=NOW)
    assert "尚無已核准的週報可供比較" in d1.markdown
    wk.write_draft(d1, WEEK1, now=NOW)
    wk.decide(WEEK1, "approved", "analyst", now=NOW)
    meta1 = wk.read_meta(WEEK1)
    assert meta1["judgments"][0]["citations"] == [1] and len(meta1["fact_claim_ids"]) == 2

    cl.set_review(db.conn, meta1["fact_claim_ids"][0], "rejected", "analyst")
    d2 = wk.build_draft(db.conn, WEEK2, use_llm=False, now=NOW)
    assert "## 七、建議修訂" in d2.markdown and "[R1]" in d2.markdown
    kinds = [r["kind"] for r in d2.revisions]
    assert "evidence_withdrawn" in kinds
    wk.write_draft(d2, WEEK2, now=NOW)
    with pytest.raises(RuntimeError, match="revision suggestions still open"):
        wk.decide(WEEK2, "approved", "analyst")
    for r in d2.revisions:
        wk.decide_revision(WEEK2, r["id"], "accepted", "analyst", "已修訂上週要點")
    with pytest.raises(KeyError):
        wk.decide_revision(WEEK2, 99, "accepted", "analyst")
    assert wk.decide(WEEK2, "approved", "analyst", now=NOW)["status"] == "approved"


def test_cli_revision_and_status(db, capsys):
    d1 = wk.build_draft(db.conn, WEEK1, use_llm=False, now=NOW)
    wk.write_draft(d1, WEEK1, now=NOW)
    wk.decide(WEEK1, "approved", "analyst", now=NOW)
    meta = wk.read_meta(WEEK1)
    meta["aircraft_ratio"] = 3.0  # force a regime change next week
    (wk._folder(WEEK1) / "meta.json").write_text(__import__("json").dumps(meta), encoding="utf-8")
    wk.write_draft(wk.build_draft(db.conn, WEEK2, use_llm=False, now=NOW), WEEK2, now=NOW)
    assert wk.main(["status"]) == 0
    assert "open_revisions=1" in capsys.readouterr().out
    assert wk.main(["revision", "--week-ending", WEEK2.isoformat(), "--id", "1", "dismiss", "--reviewer", "analyst"]) == 0
    assert "R1 dismissed by analyst" in capsys.readouterr().out
