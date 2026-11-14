"""Program 4 weekly assessment: hypotheses, critic, draft files, approval."""

from datetime import date, datetime, timedelta, timezone

import pytest

from app.database import Database
from app.military import pla_activity as pla
from app.models import Article
from app.pilot import claims as cl
from app.pilot import weekly as wk

END = date(2026, 11, 15)
NOW = datetime(2026, 11, 16, 1, 0, tzinfo=timezone.utc)
QUOTE = "其中18架次逾越海峽中線進入我北部、西南空域"
TEXT = f"國防部今（13）日表示，偵獲共機45架次、共艦7艘。{QUOTE}。國防部長顧立雄表示將嚴密監控。"


def roc(d: date) -> str:
    return f"中華民國{d.year - 1911}年{d.month}月{d.day}日"


def store_series(conn, days=130, spike_day=None):
    reports = []
    for i in range(days):
        d = END - timedelta(days=days - 1 - i)
        n = 45 if d == spike_day else 10 + (i % 5) - 2
        text = (f"一、日期：{roc(d - timedelta(days=1))}（星期一）0600時至{roc(d)}（星期二）0600時止。\n"
                f"二、臺海周邊海、空域動態：共機{n}架次、共艦6艘。\n三、共機{max(0, n - 5)}架次逾越中線。")
        reports.append(pla.parse_report_text(text, f"https://mnd/{i}"))
    pla.save_reports(conn, reports)


class FakeClient:
    model = "deepseek/deepseek-v4-pro"

    def __init__(self, result):
        self.result = result
        self.calls = []

    def analyze(self, system, user):
        self.calls.append(user)
        return self.result


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "news.db")
    d.connect()
    store_series(d.conn, spike_day=date(2026, 11, 13))
    d.save_articles([Article(source_id="mna", source_name="軍聞社", category="military", title="共機45架次擾台",
                             url="https://m/1", published_at=datetime(2026, 11, 13, 9),
                             fetched_at=datetime(2026, 11, 13, 9), position=1)])
    d.save_article_texts({"https://m/1": TEXT}, extractor="trafilatura", fetched_at=NOW)
    claim = {"claim_type": "pla_activity", "actor": "共軍", "action": "共機逾越中線", "event_date": "2026-11-13",
             "quantity": 18, "unit": "架次", "quote": QUOTE}
    stmt = {"claim_type": "statement", "actor": "國防部長顧立雄", "action": "表示將嚴密監控",
            "event_date": "2026-11-13", "quote": "國防部長顧立雄表示將嚴密監控"}
    fake = type("C", (), {"model": "flash", "analyze": lambda self, s, u: {"status": "success", "claims": [claim, stmt]}})()
    cl.run_extraction(d.conn, client=fake, now=NOW)
    yield d
    d.close()


GOOD = {"text": "本週共機單日最高45架次，明顯高於基線。", "citations": [], "uses_data": True, "confidence": "中"}
UNVERIFIED = {"text": "共機逾越中線達99架次。", "citations": [1], "uses_data": False, "confidence": "高"}
MISSING = {"text": "國防部表態強硬。", "citations": [7], "uses_data": False, "confidence": "低"}
NOBASIS = {"text": "局勢可能升級。", "citations": [], "uses_data": False}


def test_hypothesis_indicators_follow_the_rules(db):
    series = wk.pb.load_series(db.conn)
    view = wk.pb.week_view(series, END)
    facts = cl.claims_between(db.conn, date(2026, 11, 9), END)
    inds = {(i.hypothesis, i.name): i for i in wk.evaluate_hypotheses(series, view, facts)}
    assert inds[("H2", "異常偏高天數")].direction == "支持" and "11-13" in inds[("H2", "異常偏高天數")].reading
    assert inds[("H2", "尖峰前後±1日的表態/演習/事件")].direction == "支持"
    assert inds[("H2", "基線穩定度")].direction == "支持"
    assert inds[("H1", "較去年同週")].direction == "資料不足"
    assert inds[("H1", "90日趨勢")].direction == "中性"


def test_critic_flags_unverified_numbers_missing_citations_and_no_basis(db):
    facts = cl.claims_between(db.conn, date(2026, 11, 9), END)
    js = [wk.Judgment(**{**j, "confidence": j.get("confidence")}) for j in (GOOD, UNVERIFIED, MISSING, NOBASIS)]
    data_md = wk.pb.render_week_markdown(wk.pb.week_view(wk.pb.load_series(db.conn), END))
    wk.critic(js, facts, data_md, ("2026-11-09", "2026-11-15"))
    assert js[0].notes == []
    assert "未核實數字 ['99']" in js[1].notes
    assert any("引用不存在的編號 [7]" in n for n in js[2].notes)
    assert "無依據：未引用事實或數據" in js[3].notes and "未標把握程度" in js[3].notes


def test_draft_contains_all_sections_citations_and_flags(db):
    client = FakeClient({"status": "success", "judgments": [GOOD, UNVERIFIED]})
    d = wk.build_draft(db.conn, END, client=client, now=NOW)
    md = d.markdown
    for heading in ("## 一、研判要點", "## 二、共軍活動數據", "## 三、假設評估", "## 四、本週事實", "## 五、資料缺口", "## 六、來源"):
        assert heading in md
    assert "[數據]（把握：中）" in md and "⚠ 自動檢查：未核實數字 ['99']" in md
    assert f"[1] 2026-11-13 共軍 共機逾越中線 18架次（未審核） — 「{QUOTE}」" in md
    assert "[1] 軍聞社〈共機45架次擾台〉https://m/1" in md
    assert "有全文 1 篇（100%）" in md and "2 則事實尚未經分析員審核" in md
    assert d.n_facts == 2 and d.n_flagged == 1 and d.model == "deepseek/deepseek-v4-pro"
    assert '"facts": [{"n": 1' in client.calls[0]


def test_no_llm_and_model_errors_still_produce_a_draft(db):
    assert "以 --no-llm 產生" in wk.build_draft(db.conn, END, use_llm=False, now=NOW).markdown
    d = wk.build_draft(db.conn, END, client=FakeClient({"status": "error", "error": "budget_exceeded"}), now=NOW)
    assert "未生成：budget_exceeded" in d.markdown and d.judgment_error == "budget_exceeded"


def test_files_approval_edit_ratio_and_guards(db):
    d = wk.build_draft(db.conn, END, client=FakeClient({"status": "success", "judgments": [GOOD, UNVERIFIED]}), now=NOW)
    folder = wk.write_draft(d, END, now=NOW)
    assert (folder / "generated.md").exists() and wk.read_meta(END)["status"] == "draft"
    with pytest.raises(RuntimeError, match="automatic-check"):
        wk.decide(END, "approved", "analyst")
    draft = folder / "draft.md"
    text = draft.read_text(encoding="utf-8")
    text = "\n".join(l for l in text.splitlines() if "99架次" not in l and "⚠ 自動檢查" not in l) + "\n"
    draft.write_text(text, encoding="utf-8")
    meta = wk.decide(END, "approved", "analyst", now=NOW)
    assert meta["status"] == "approved" and 0 < meta["edit_ratio"] < 0.1
    assert (folder / "approved.md").read_text(encoding="utf-8") == text
    with pytest.raises(RuntimeError, match="already approved"):
        wk.write_draft(d, END)
    wk.write_draft(d, END, force=True)
    assert wk.read_meta(END)["status"] == "draft"


def test_reject_records_note(db):
    wk.write_draft(wk.build_draft(db.conn, END, use_llm=False, now=NOW), END, now=NOW)
    meta = wk.decide(END, "rejected", "analyst", "資料不足", now=NOW)
    assert meta["status"] == "rejected" and meta["history"][-1]["note"] == "資料不足"


def test_empty_database_gives_a_gap_report(tmp_path):
    with Database(tmp_path / "e.db") as d:
        md = wk.build_draft(d.conn, END, use_llm=False, now=NOW).markdown
    assert "共機基線不足" in md and "本週無已抽取的有效事實" in md


def test_cli_generate_and_status(db, capsys):
    path = str(db._db_path)
    db.close()
    assert wk.main(["generate", "--week-ending", "2026-11-15", "--no-llm", "--db", path]) == 0
    assert wk.main(["status"]) == 0
    out = capsys.readouterr().out
    assert "Draft written" in out and "2026-11-09 – 2026-11-15  draft" in out
    db.connect()
