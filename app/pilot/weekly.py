"""Program 4 weekly situation assessment: draft, automatic checks, analyst approval.

A draft for the seven days ending ``--week-ending`` is assembled from:

1. **Data**: PLA activity against its 90-day baseline (``pla_baseline``).
2. **Facts**: valid, not-rejected claims of the week (``pilot.claims``), each
   numbered and tied to its source article and verbatim quote.
3. **Hypotheses**: three rival explanations scored on explicit, documented
   indicator rules (no model involved):
   H1 structural rise · H2 trigger-driven spikes on a stable baseline ·
   H3 training/seasonal cycle.
4. **Key judgments** (optional): written by the strong-tier model from the
   above only (``prompts/weekly_p4.md``), then checked automatically: each
   must cite existing facts or the data table, and every number it states
   must appear in what it cites.  Failing judgments stay in the draft,
   marked with the reasons, for the analyst to fix or delete.

Output goes to ``ASSESSMENT_DIR`` (default ``data/assessments/p4``), one
folder per week end date: ``generated.md`` (as produced), ``draft.md`` (the
analyst edits this), ``meta.json``.  Approval records the reviewer and how
much the analyst changed (the pilot gate is "approved with only minor
edits").  Nothing is sent anywhere.

CLI::

    python -m app.pilot.weekly generate --week-ending 2026-11-15 [--no-llm]
    python -m app.pilot.weekly approve  --week-ending 2026-11-15 --reviewer NAME
    python -m app.pilot.weekly reject   --week-ending 2026-11-15 --reviewer NAME --note "..."
    python -m app.pilot.weekly status
"""

from __future__ import annotations

import argparse
import difflib
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from ..military import pla_baseline as pb
from . import claims as cl

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "weekly_p4.md"
TYPE_LABELS = {
    "pla_activity": "共軍活動",
    "exercise": "演習與戰備警巡",
    "incident": "事件",
    "statement": "官方表態",
    "arms": "軍購與交付",
    "policy": "國防政策",
    "other_security": "其他安全事實",
}
CONFIDENCE = ("高", "中", "低")
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")


def assessment_dir() -> Path:
    value = os.getenv("ASSESSMENT_DIR", "").strip()
    return Path(value) if value else Path(__file__).resolve().parents[2] / "data" / "assessments" / "p4"


# ── hypotheses (deterministic indicator rules) ────────────────────────


@dataclass(slots=True)
class Indicator:
    hypothesis: str
    name: str
    reading: str
    direction: str  # 支持 | 不支持 | 中性 | 資料不足


def _same_week_last_year(series, start: date) -> float | None:
    values = []
    for i in range(7):
        d = (start - timedelta(days=364) + timedelta(days=i)).isoformat()
        v = (series.get(d) or {}).get("aircraft")
        if v is not None:
            values.append(v)
    return sum(values) / len(values) if len(values) >= 4 else None


def evaluate_hypotheses(series, view: pb.WeekView, facts: list[dict]) -> list[Indicator]:
    a = view.metrics["aircraft"]
    b = a.baseline
    start = date.fromisoformat(view.start)
    out: list[Indicator] = []

    # H1: structural rise
    if b.slope_per_day is None:
        out.append(Indicator("H1", "90日趨勢", "基線不足", "資料不足"))
    else:
        d = "支持" if b.slope_per_day > 0.05 else "不支持" if b.slope_per_day < -0.05 else "中性"
        out.append(Indicator("H1", "90日趨勢", f"{b.slope_per_day:+.3f} 架次/日", d))
    if a.ratio_to_baseline is None:
        out.append(Indicator("H1", "本週/90日基線", "資料不足", "資料不足"))
    else:
        r = a.ratio_to_baseline
        out.append(Indicator("H1", "本週/90日基線", f"{r:.2f}×",
                             "支持" if r >= 1.3 else "不支持" if r <= 0.8 else "中性"))
    last_year = _same_week_last_year(series, start)
    yoy = (a.week_mean / last_year) if (a.week_mean is not None and last_year) else None
    if yoy is None:
        out.append(Indicator("H1", "較去年同週", "無去年資料", "資料不足"))
    else:
        out.append(Indicator("H1", "較去年同週", f"{yoy:.2f}×",
                             "支持" if yoy >= 1.3 else "不支持" if yoy <= 0.8 else "中性"))

    # H2: trigger-driven spikes on a stable baseline
    spike_days = sorted({f.day for f in a.flags if f.flag == "spike"})
    if not b.sufficient:
        out.append(Indicator("H2", "異常偏高天數", "基線不足", "資料不足"))
    else:
        out.append(Indicator("H2", "異常偏高天數", f"{len(spike_days)} 天" + (f"（{'、'.join(d[5:] for d in spike_days)}）" if spike_days else ""),
                             "支持" if spike_days else "不支持"))
    if b.slope_per_day is None:
        out.append(Indicator("H2", "基線穩定度", "基線不足", "資料不足"))
    else:
        out.append(Indicator("H2", "基線穩定度", f"趨勢絕對值 {abs(b.slope_per_day):.3f}",
                             "支持" if abs(b.slope_per_day) <= 0.05 else "中性"))
    if spike_days:
        near = set()
        for d in spike_days:
            day = date.fromisoformat(d)
            near |= {(day + timedelta(days=k)).isoformat() for k in (-1, 0, 1)}
        triggers = [f for f in facts if f["claim_type"] in ("statement", "exercise", "incident")
                    and (f["event_date"] or "") in near]
        out.append(Indicator("H2", "尖峰前後±1日的表態/演習/事件", f"{len(triggers)} 則",
                             "支持" if triggers else "中性"))

    # H3: training / seasonal cycle
    if yoy is None:
        out.append(Indicator("H3", "與去年同週水準相近", "無去年資料", "資料不足"))
    else:
        out.append(Indicator("H3", "與去年同週水準相近", f"{yoy:.2f}×",
                             "支持" if 0.8 < yoy < 1.25 else "不支持"))
    exercises = [f for f in facts if f["claim_type"] == "exercise"]
    out.append(Indicator("H3", "本週演習事實", f"{len(exercises)} 則", "中性"))
    return out


HYPOTHESES = {
    "H1": "共軍對台壓力結構性上升",
    "H2": "穩定基線上、圍繞政治觸發點的短期尖峰",
    "H3": "主要隨訓練與季節週期起伏",
}
RULES_NOTE = (
    "方向規則（固定、可稽核）：90日趨勢 >+0.05 支持 H1、<-0.05 不支持；本週/基線 ≥1.3 支持 H1、≤0.8 不支持；"
    "較去年同週 ≥1.3 支持 H1、≤0.8 不支持、介於 0.8–1.25 支持 H3；有異常偏高日支持 H2，尖峰前後±1日有表態/演習/事件則再支持 H2。"
)


# ── judgments and the automatic critic ────────────────────────────────


@dataclass(slots=True)
class Judgment:
    text: str
    citations: list[int]
    uses_data: bool
    confidence: str | None
    notes: list[str] = field(default_factory=list)


def critic(judgments: list[Judgment], facts: list[dict], data_text: str, week: tuple[str, str]) -> None:
    """Attach notes to judgments that cite nothing, cite missing facts or state unverified numbers."""
    known = set(range(1, len(facts) + 1))
    data_numbers = set(NUMBER_RE.findall(data_text))
    date_numbers = set(NUMBER_RE.findall(" ".join(week))) | {str(int(x)) for x in NUMBER_RE.findall(" ".join(week))}
    for j in judgments:
        missing = [n for n in j.citations if n not in known]
        if missing:
            j.notes.append(f"引用不存在的編號 {missing}")
        if not j.citations and not j.uses_data:
            j.notes.append("無依據：未引用事實或數據")
        allowed = set(date_numbers)
        if j.uses_data:
            allowed |= data_numbers
        for n in j.citations:
            if n in known:
                f = facts[n - 1]
                allowed |= set(NUMBER_RE.findall(" ".join(str(f.get(k) or "") for k in ("quote", "quantity", "event_date"))))
        unverified = sorted({x for x in NUMBER_RE.findall(j.text) if x not in allowed}, key=lambda s: float(s))
        if unverified:
            j.notes.append(f"未核實數字 {unverified}")
        if j.confidence not in CONFIDENCE:
            j.notes.append("未標把握程度")


def draft_judgments(client, payload: dict) -> tuple[list[Judgment], str | None]:
    result = client.analyze(PROMPT_PATH.read_text(encoding="utf-8"), json.dumps(payload, ensure_ascii=False))
    if result.get("status") != "success":
        return [], str(result.get("error") or "error")
    out = []
    for raw in result.get("judgments") or []:
        if not isinstance(raw, dict) or not str(raw.get("text") or "").strip():
            continue
        cites = []
        for c in raw.get("citations") or []:
            try:
                cites.append(int(c))
            except (TypeError, ValueError):
                continue
        out.append(Judgment(str(raw["text"]).strip(), cites, bool(raw.get("uses_data")),
                            str(raw.get("confidence") or "").strip() or None))
    return out, None


# ── assembling the draft ──────────────────────────────────────────────


@dataclass(slots=True)
class Draft:
    start: str
    end: str
    markdown: str
    model: str | None
    judgment_error: str | None
    n_facts: int
    n_flagged: int


def build_draft(conn: sqlite3.Connection, week_ending: date, *, client=None, use_llm: bool = True,
                now: datetime | None = None) -> Draft:
    series = pb.load_series(conn)
    view = pb.week_view(series, week_ending)
    start, end = date.fromisoformat(view.start), week_ending
    facts = cl.claims_between(conn, start, end)
    data_md = pb.render_week_markdown(view)
    indicators = evaluate_hypotheses(series, view, facts)

    judgments: list[Judgment] = []
    model = error = None
    if use_llm:
        if client is None:
            from ..ai_client import make_client

            client = make_client("weekly_assessment", "strong")
        if client is None:
            error = "未配置模型"
        else:
            model = client.model
            payload = {
                "week": [view.start, view.end],
                "data_table": data_md,
                "hypotheses": {k: v for k, v in HYPOTHESES.items()},
                "indicators": [vars_(i) for i in indicators],
                "facts": [_fact_payload(n, f) for n, f in enumerate(facts, 1)],
            }
            judgments, error = draft_judgments(client, payload)
            critic(judgments, facts, data_md, (view.start, view.end))

    md = _render(view, data_md, indicators, facts, judgments, model=model, error=error, use_llm=use_llm,
                 now=now or datetime.now(timezone.utc), text_coverage=_text_coverage(conn, start, end))
    return Draft(view.start, view.end, md, model, error, len(facts), sum(1 for j in judgments if j.notes))


def vars_(ind: Indicator) -> dict:
    return {"hypothesis": ind.hypothesis, "name": ind.name, "reading": ind.reading, "direction": ind.direction}


def _fact_payload(n: int, f: dict) -> dict:
    return {"n": n, "type": f["claim_type"], "date": f["event_date"], "actor": f["actor"], "action": f["action"],
            "quantity": f["quantity"], "unit": f["unit"], "location": f["location"],
            "review": f["review_status"], "quote": f["quote"], "source": f["source_name"]}


def _text_coverage(conn: sqlite3.Connection, start: date, end: date) -> tuple[int, int]:
    cats = sorted(cl.inscope_categories())
    marks = ",".join("?" * len(cats))
    row = conn.execute(
        f"SELECT COUNT(*), SUM(t.url IS NOT NULL) FROM articles a LEFT JOIN article_texts t ON t.url = a.url "
        f"WHERE a.category IN ({marks}) AND substr(COALESCE(a.published_at, a.fetched_at), 1, 10) BETWEEN ? AND ?",
        (*cats, start.isoformat(), end.isoformat()),
    ).fetchone()
    return int(row[0] or 0), int(row[1] or 0)


def _render(view, data_md, indicators, facts, judgments, *, model, error, use_llm, now, text_coverage) -> str:
    L: list[str] = [
        f"# 台海安全週報（草稿）{view.start} 至 {view.end}",
        "",
        f"> 狀態：草稿，待分析員審核。生成於 {now.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC；"
        f"研判要點模型：{model or '未使用'}。",
        "> 本稿只使用系統已存資料；數據與事實段落為自動整理，研判要點須經分析員核對。",
        "",
        "## 一、研判要點",
        "",
    ]
    if not use_llm:
        L.append("（本次未生成：以 --no-llm 產生，請分析員撰寫。）")
    elif error:
        L.append(f"（未生成：{error}。請分析員撰寫，或稍後重新產生。）")
    elif not judgments:
        L.append("（模型未給出要點。）")
    for i, j in enumerate(judgments, 1):
        cites = "".join(f"[{n}]" for n in j.citations) + ("[數據]" if j.uses_data else "")
        L.append(f"{i}. {j.text} {cites}（把握：{j.confidence or '未標'}）")
        if j.notes:
            L.append(f"   - ⚠ 自動檢查：{'；'.join(j.notes)}")
    L += ["", "## 二、共軍活動數據（國防部每日公布）", "", data_md, "",
          "## 三、假設評估", ""]
    for key, label in HYPOTHESES.items():
        L.append(f"- **{key}** {label}")
    L += ["", "| 假設 | 指標 | 讀數 | 方向 |", "|---|---|---|---|"]
    for ind in indicators:
        L.append(f"| {ind.hypothesis} | {ind.name} | {ind.reading} | {ind.direction} |")
    tally = {}
    for ind in indicators:
        t = tally.setdefault(ind.hypothesis, {"支持": 0, "不支持": 0})
        if ind.direction in t:
            t[ind.direction] += 1
    L += ["", "合計：" + "；".join(f"{h} 支持 {t['支持']}、不支持 {t['不支持']}" for h, t in tally.items()),
          "", f"> {RULES_NOTE}", "", "## 四、本週事實", ""]
    if not facts:
        L.append("（本週無已抽取的有效事實。可執行 `python -m app.pilot.claims extract --days 7`。）")
    by_type: dict[str, list[tuple[int, dict]]] = {}
    for n, f in enumerate(facts, 1):
        by_type.setdefault(f["claim_type"], []).append((n, f))
    for ctype, label in TYPE_LABELS.items():
        rows = by_type.get(ctype)
        if not rows:
            continue
        L += [f"### {label}", ""]
        for n, f in rows:
            qty = f" {f['quantity']}{f['unit'] or ''}" if f["quantity"] is not None else ""
            mark = "" if f["review_status"] == "confirmed" else "（未審核）"
            L.append(f"- [{n}] {f['event_date'] or '日期不明'} {f['actor'] or ''} {f['action'] or ''}{qty}{mark}"
                     f" — 「{f['quote']}」")
        L.append("")
    total, with_text = text_coverage
    gaps = []
    if view.missing_days:
        gaps.append(f"國防部缺報日期：{'、'.join(view.missing_days)}")
    if not view.metrics["aircraft"].baseline.sufficient:
        gaps.append("共機基線不足（前 90 日資料少於 30 天），異常判斷暫停")
    if total:
        gaps.append(f"本週試點範圍文章 {total} 篇，有全文 {with_text} 篇（{with_text / total:.0%}）")
    unreviewed = sum(1 for f in facts if f["review_status"] != "confirmed")
    if unreviewed:
        gaps.append(f"{unreviewed} 則事實尚未經分析員審核")
    L += ["## 五、資料缺口", ""] + ([f"- {g}" for g in gaps] or ["- 無"]) + ["", "## 六、來源", ""]
    for n, f in enumerate(facts, 1):
        L.append(f"[{n}] {f['source_name']}〈{f['title']}〉{f['url']}")
    return "\n".join(L).rstrip() + "\n"


# ── files and approval ────────────────────────────────────────────────


def _folder(week_ending: date) -> Path:
    return assessment_dir() / week_ending.isoformat()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def read_meta(week_ending: date) -> dict | None:
    path = _folder(week_ending) / "meta.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def write_draft(draft: Draft, week_ending: date, *, force: bool = False, now: datetime | None = None) -> Path:
    folder = _folder(week_ending)
    meta = read_meta(week_ending)
    if meta and meta.get("status") == "approved" and not force:
        raise RuntimeError(f"{week_ending} is already approved; use --force to regenerate")
    folder.mkdir(parents=True, exist_ok=True)
    (folder / "generated.md").write_text(draft.markdown, encoding="utf-8")
    (folder / "draft.md").write_text(draft.markdown, encoding="utf-8")
    meta = {
        "program": "P4", "week_start": draft.start, "week_end": draft.end, "status": "draft",
        "generated_at": (now or datetime.now(timezone.utc)).isoformat(), "model": draft.model,
        "judgment_error": draft.judgment_error, "facts": draft.n_facts, "flagged_judgments": draft.n_flagged,
        "generated_sha256": _sha(draft.markdown), "history": [],
    }
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return folder


def edit_ratio(generated: str, final: str) -> float:
    """Share of the text changed by the analyst (0 = untouched, 1 = rewritten)."""
    return round(1 - difflib.SequenceMatcher(None, generated, final, autojunk=False).ratio(), 4)


def decide(week_ending: date, decision: str, reviewer: str, note: str = "", *, now: datetime | None = None) -> dict:
    folder = _folder(week_ending)
    meta = read_meta(week_ending)
    if meta is None:
        raise FileNotFoundError(f"no draft for {week_ending}")
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be approved or rejected")
    generated = (folder / "generated.md").read_text(encoding="utf-8")
    final = (folder / "draft.md").read_text(encoding="utf-8")
    if decision == "approved" and "⚠ 自動檢查" in final:
        raise RuntimeError("draft still contains ⚠ automatic-check notes; fix or delete those judgments first")
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    ratio = edit_ratio(generated, final)
    meta.update(status=decision, reviewer=reviewer, reviewed_at=stamp, edit_ratio=ratio,
                final_sha256=_sha(final), note=note or None)
    meta.setdefault("history", []).append({"at": stamp, "decision": decision, "reviewer": reviewer,
                                           "edit_ratio": ratio, "note": note or None})
    if decision == "approved":
        (folder / "approved.md").write_text(final, encoding="utf-8")
    (folder / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return meta


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.pilot.weekly")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("generate", "approve", "reject"):
        p = sub.add_parser(name)
        p.add_argument("--week-ending", type=date.fromisoformat,
                       help="YYYY-MM-DD, last day of the week (default: yesterday, Taipei)")
        if name == "generate":
            p.add_argument("--no-llm", action="store_true", help="data, facts and hypotheses only")
            p.add_argument("--force", action="store_true", help="regenerate even if approved")
            p.add_argument("--db", help="news DB path (default: the configured news DB)")
        else:
            p.add_argument("--reviewer", required=True)
            p.add_argument("--note", default="")
    sub.add_parser("status")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from ..time_utils import TAIPEI

    if args.cmd == "status":
        root = assessment_dir()
        for folder in sorted(root.glob("*/meta.json")) if root.exists() else []:
            m = json.loads(folder.read_text(encoding="utf-8"))
            print(f"{m['week_start']} – {m['week_end']}  {m['status']:<8} facts={m['facts']} "
                  f"flagged={m['flagged_judgments']} edit_ratio={m.get('edit_ratio', '—')} {m.get('reviewer') or ''}")
        return 0
    week_ending = args.week_ending or (datetime.now(TAIPEI).date() - timedelta(days=1))
    if args.cmd == "generate":
        from ..database import Database
        from ..settings import get_settings

        with Database(args.db or get_settings().news_db_path) as db:
            draft = build_draft(db.conn, week_ending, use_llm=not args.no_llm)
        folder = write_draft(draft, week_ending, force=args.force)
        print(f"Draft written: {folder / 'draft.md'}  (facts={draft.n_facts}, flagged={draft.n_flagged}"
              f"{', judgments not generated: ' + draft.judgment_error if draft.judgment_error else ''})")
        return 0
    meta = decide(week_ending, "approved" if args.cmd == "approve" else "rejected", args.reviewer, args.note)
    print(f"{meta['status']} by {meta['reviewer']}; edit ratio {meta['edit_ratio']:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
