"""Weekly assessment drafts for programs P1, P2, P3, P5 and P6.

P4 has its own draft (``app/pilot/weekly.py``) built on the MND daily
series.  The other programs have no such series, so their drafts rest on:

1. **Evidence ladder per question**: how far this week's facts climbed
   (rhetoric → commitment → formal decision → resources → implementation →
   effect), against the highest stage of the previous four weeks.  A higher
   stage than before is flagged: that is the measurable "did X move up the
   ladder?" the design asks for.
2. **Hypothesis evidence matrix**: the strong-tier model rates every fact as
   supporting (+), weakening (−) or irrelevant (0) to each rival hypothesis.
   Ratings for unknown facts or hypotheses are dropped; the tally per
   hypothesis is computed here, not by the model.
3. **Key judgments**: model-written, then checked exactly like P4's
   (citations must exist, numbers must appear in what is cited).
4. Facts per question, data gaps, sources, and revision suggestions against
   the program's last approved week.  Same files and approval rules as P4.

CLI::

    python -m app.programs.weekly generate --program P1 --week-ending 2026-11-15 [--no-llm]
    python -m app.programs.weekly approve  --program P1 --week-ending 2026-11-15 --reviewer NAME
    python -m app.programs.weekly revision --program P1 --week-ending 2026-11-15 --id 1 accept --reviewer NAME
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from .. import programs as prog
from ..pilot import claims as cl
from ..pilot import revisions as pilot_revisions
from ..pilot.weekly import (
    Draft, Judgment, assessment_dir, critic, decide, decide_revision, write_draft,
)

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "weekly_generic.md"
TYPE_LABELS = {
    "decision": "決定", "statement": "表態", "action": "行動", "agreement": "協議", "budget": "預算",
    "appointment": "任命", "vote": "表決", "investment": "投資", "visit": "訪問與過境", "other": "其他",
}
RATINGS = {"+": "支持", "-": "削弱", "0": "無關"}


def _ladder_rank(stage: str | None) -> int:
    return cl.LADDER.index(stage) if stage in cl.LADDER else -1


def question_links(conn: sqlite3.Connection, claim_ids: list[int]) -> dict[int, list[str]]:
    if not claim_ids:
        return {}
    marks = ",".join("?" * len(claim_ids))
    out: dict[int, list[str]] = {}
    for cid, qid in conn.execute(f"SELECT claim_id, question_id FROM claim_questions WHERE claim_id IN ({marks})",
                                 claim_ids):
        out.setdefault(cid, []).append(qid)
    return out


@dataclass(slots=True)
class LadderRow:
    question: str
    counts: dict
    top: str | None
    previous_top: str | None

    @property
    def climbed(self) -> bool:
        """Higher stage than the previous four weeks (both weeks must have evidence)."""
        return (self.top is not None and self.previous_top is not None
                and _ladder_rank(self.top) > _ladder_rank(self.previous_top))


def ladder_table(conn, program: str, start: date, end: date, facts: list[dict]) -> list[LadderRow]:
    cfg = prog.program(program)
    links = question_links(conn, [f["id"] for f in facts])
    prior = cl.claims_between(conn, start - timedelta(days=28), start - timedelta(days=1), program=program)
    prior_links = question_links(conn, [f["id"] for f in prior])
    rows = []
    for q in cfg["questions"]:
        qid = q["id"]
        stages = [f["ladder"] for f in facts if qid in links.get(f["id"], [])]
        before = [f["ladder"] for f in prior if qid in prior_links.get(f["id"], [])]
        counts = {s: stages.count(s) for s in cl.LADDER if stages.count(s)}
        top = max(stages, key=_ladder_rank) if stages else None
        prev = max(before, key=_ladder_rank) if before else None
        rows.append(LadderRow(qid, counts, top, prev))
    return rows


def render_ladder(rows: list[LadderRow], labels: dict) -> str:
    lines = ["| 問題 | 本週各階段事實數 | 本週最高階段 | 前四週最高階段 |", "|---|---|---|---|"]
    for r in rows:
        counts = "、".join(f"{labels[s]} {n}" for s, n in r.counts.items()) or "—"
        top = labels.get(r.top, "—") if r.top else "—"
        prev = labels.get(r.previous_top, "—") if r.previous_top else "—"
        mark = " ⬆" if r.climbed else ""
        lines.append(f"| {r.question} | {counts} | {top}{mark} | {prev} |")
    if any(r.climbed for r in rows):
        lines.append("")
        lines.append("⬆ = 本週證據階梯高於前四週，代表從表態走向更具約束力的行動，需特別留意。")
    return "\n".join(lines)


def parse_matrix(raw, n_facts: int, hypotheses: list[str]) -> dict[int, dict[str, str]]:
    out: dict[int, dict[str, str]] = {}
    for row in raw or []:
        if not isinstance(row, dict):
            continue
        try:
            n = int(row.get("fact"))
        except (TypeError, ValueError):
            continue
        if not 1 <= n <= n_facts:
            continue
        ratings = {h: str(row.get(h)) for h in hypotheses if str(row.get(h)) in RATINGS}
        if ratings:
            out[n] = ratings
    return out


def tally_matrix(matrix: dict[int, dict[str, str]], hypotheses: list[str]) -> dict[str, dict[str, int]]:
    tally = {h: {"支持": 0, "不支持": 0} for h in hypotheses}
    for ratings in matrix.values():
        for h, r in ratings.items():
            if r == "+":
                tally[h]["支持"] += 1
            elif r == "-":
                tally[h]["不支持"] += 1
    return tally


def build_draft(conn: sqlite3.Connection, program: str, week_ending: date, *, client=None, use_llm: bool = True,
                now: datetime | None = None) -> Draft:
    if program == "P4":
        raise ValueError("P4 uses app.pilot.weekly")
    cfg_all = prog.load()
    cfg = prog.program(program)
    labels = cfg_all["ladder_labels"]
    hyps = list(cfg["hypotheses"])
    start = week_ending - timedelta(days=6)
    facts = cl.claims_between(conn, start, week_ending, program=program)
    links = question_links(conn, [f["id"] for f in facts])
    ladder_rows = ladder_table(conn, program, start, week_ending, facts)
    ladder_md = render_ladder(ladder_rows, labels)

    judgments: list[Judgment] = []
    matrix: dict[int, dict[str, str]] = {}
    model = error = None
    if use_llm:
        if client is None:
            from ..ai_client import make_client

            client = make_client(f"weekly_{program.lower()}", "strong")
        if client is None:
            error = "未配置模型"
        elif not facts:
            error = "本週無有效事實"
        else:
            model = client.model
            payload = {
                "week": [start.isoformat(), week_ending.isoformat()],
                "questions": [{"id": q["id"], "text": q["text"], "indicators": q["indicators"]} for q in cfg["questions"]],
                "hypotheses": cfg["hypotheses"],
                "ladder_table": ladder_md,
                "facts": [{"n": n, "type": f["claim_type"], "kind": f["kind"], "ladder": f["ladder"],
                           "questions": links.get(f["id"], []), "date": f["event_date"], "actor": f["actor"],
                           "action": f["action"], "quantity": f["quantity"], "unit": f["unit"],
                           "review": f["review_status"], "quote": f["quote"], "source": f["source_name"]}
                          for n, f in enumerate(facts, 1)],
            }
            system = PROMPT_PATH.read_text(encoding="utf-8").format(program_name=f"{program} {cfg['name']}")
            result = client.analyze(system, json.dumps(payload, ensure_ascii=False))
            if result.get("status") != "success":
                error = str(result.get("error") or "error")
            else:
                for raw in result.get("judgments") or []:
                    if isinstance(raw, dict) and str(raw.get("text") or "").strip():
                        cites = []
                        for c in raw.get("citations") or []:
                            try:
                                cites.append(int(c))
                            except (TypeError, ValueError):
                                pass
                        judgments.append(Judgment(str(raw["text"]).strip(), cites, bool(raw.get("uses_data")),
                                                  str(raw.get("confidence") or "").strip() or None))
                critic(judgments, facts, ladder_md, (start.isoformat(), week_ending.isoformat()))
                matrix = parse_matrix(result.get("matrix"), len(facts), hyps)

    tally = tally_matrix(matrix, hyps) if matrix else {}
    state = {
        "tally": tally, "aircraft_ratio": None,
        "judgments": [{"text": j.text, "citations": j.citations, "uses_data": j.uses_data,
                       "confidence": j.confidence} for j in judgments],
        "fact_claim_ids": [f["id"] for f in facts],
        "ladder_top": {r.question: r.top for r in ladder_rows},
    }
    previous = pilot_revisions.previous_approved(assessment_dir(program), start.isoformat())
    suggestions = pilot_revisions.suggest(conn, previous, state)
    now = now or datetime.now(timezone.utc)
    md = render(program, cfg, labels, start, week_ending, ladder_md, judgments, matrix, tally, facts, links,
                model=model, error=error, use_llm=use_llm, now=now,
                revisions_md=pilot_revisions.render_markdown(suggestions, previous),
                hidden_allegations=_hidden_allegations(conn, program, start, week_ending))
    return Draft(start.isoformat(), week_ending.isoformat(), md, model, error, len(facts),
                 sum(1 for j in judgments if j.notes), state=state, revisions=suggestions, program=program)


def _hidden_allegations(conn, program: str, start: date, end: date) -> int:
    rows = cl.claims_between(conn, start, end, program=program, include_unconfirmed_allegations=True)
    return sum(1 for r in rows if r["kind"] == "allegation" and r["review_status"] != "confirmed")


def render(program, cfg, labels, start, end, ladder_md, judgments, matrix, tally, facts, links, *,
           model, error, use_llm, now, revisions_md, hidden_allegations) -> str:
    L = [
        f"# {cfg['name']}週報（草稿）{start} 至 {end}",
        "",
        f"> 狀態：草稿，待分析員審核。研究計畫 {program}。生成於 {now.astimezone(timezone.utc).strftime('%Y-%m-%d %H:%M')} UTC；"
        f"模型：{model or '未使用'}。本稿只使用系統已存資料。",
        "",
        "## 一、研判要點",
        "",
    ]
    if not use_llm:
        L.append("（本次未生成：以 --no-llm 產生，請分析員撰寫。）")
    elif error:
        L.append(f"（未生成：{error}。）")
    for i, j in enumerate(judgments, 1):
        cites = "".join(f"[{n}]" for n in j.citations) + ("[數據]" if j.uses_data else "")
        L.append(f"{i}. {j.text} {cites}（把握：{j.confidence or '未標'}）")
        if j.notes:
            L.append(f"   - ⚠ 自動檢查：{'；'.join(j.notes)}")
    L += ["", "## 二、證據階梯（表態 → 承諾 → 正式決定 → 資源投入 → 執行 → 效果）", "", ladder_md, "",
          "## 三、假設證據矩陣", ""]
    for h, text in cfg["hypotheses"].items():
        L.append(f"- **{h}** {text}")
    L.append("")
    if matrix:
        hyps = list(cfg["hypotheses"])
        L += ["| 事實 | " + " | ".join(hyps) + " |", "|---|" + "---|" * len(hyps)]
        for n in sorted(matrix):
            L.append(f"| [{n}] | " + " | ".join(RATINGS.get(matrix[n].get(h, ""), "—") for h in hyps) + " |")
        L += ["", "合計：" + "；".join(f"{h} 支持 {t['支持']}、削弱 {t['不支持']}" for h, t in tally.items()),
              "", "> 矩陣由模型逐條評定、系統計數；請分析員核對有疑義的評定。"]
    else:
        L.append("（矩陣未生成：需模型或分析員填寫。）")
    L += ["", "## 四、本週事實（依問題）", ""]
    if not facts:
        L.append(f"（本週無已抽取的有效事實。可執行 `python -m app.pilot.claims extract --program {program} --days 7`。）")
    by_q: dict[str, list[int]] = {}
    for n, f in enumerate(facts, 1):
        for q in links.get(f["id"], []) or ["未歸問題"]:
            by_q.setdefault(q, []).append(n)
    for q in [q["id"] for q in cfg["questions"]] + ["未歸問題"]:
        if q not in by_q:
            continue
        text = next((x["text"] for x in cfg["questions"] if x["id"] == q), "")
        L += [f"### {q} {text}".rstrip(), ""]
        for n in by_q[q]:
            f = facts[n - 1]
            qty = f" {f['quantity']}{f['unit'] or ''}" if f["quantity"] is not None else ""
            mark = "" if f["review_status"] == "confirmed" else "（未審核）"
            ladder = labels.get(f["ladder"], "?")
            L.append(f"- [{n}] {f['event_date'] or '日期不明'}［{TYPE_LABELS.get(f['claim_type'], f['claim_type'])}／{ladder}］"
                     f"{f['actor'] or ''} {f['action'] or ''}{qty}{mark} — 「{f['quote']}」")
        L.append("")
    gaps = [f"{q['id']} 本週沒有相關事實" for q in cfg["questions"]
            if not any(q["id"] in links.get(f["id"], []) for f in facts)]
    if hidden_allegations:
        gaps.append(f"{hidden_allegations} 則未經確認的指控／傳聞未列入（須分析員確認後才會出現）")
    unreviewed = sum(1 for f in facts if f["review_status"] != "confirmed")
    if unreviewed:
        gaps.append(f"{unreviewed} 則事實尚未經分析員審核")
    L += ["## 五、資料缺口", ""] + ([f"- {g}" for g in gaps] or ["- 無"]) + ["", "## 六、來源", ""]
    for n, f in enumerate(facts, 1):
        L.append(f"[{n}] {f['source_name']}〈{f['title']}〉{f['url']}")
    L += ["", "## 七、建議修訂（與上次核准週報比較）", "", revisions_md]
    return "\n".join(L).rstrip() + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.programs.weekly")
    sub = parser.add_subparsers(dest="cmd", required=True)
    choices = ("P1", "P2", "P3", "P5", "P6")
    for name in ("generate", "approve", "reject", "revision"):
        p = sub.add_parser(name)
        p.add_argument("--program", required=True, choices=choices)
        p.add_argument("--week-ending", type=date.fromisoformat)
        if name == "generate":
            p.add_argument("--no-llm", action="store_true")
            p.add_argument("--force", action="store_true")
            p.add_argument("--db")
        elif name == "revision":
            p.add_argument("--id", type=int, required=True)
            p.add_argument("decision", choices=("accept", "dismiss"))
            p.add_argument("--reviewer", required=True)
            p.add_argument("--note", default="")
        else:
            p.add_argument("--reviewer", required=True)
            p.add_argument("--note", default="")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    from ..time_utils import TAIPEI

    week_ending = args.week_ending or (datetime.now(TAIPEI).date() - timedelta(days=1))
    if args.cmd == "generate":
        from ..database import Database
        from ..settings import get_settings

        with Database(args.db or get_settings().news_db_path) as db:
            draft = build_draft(db.conn, args.program, week_ending, use_llm=not args.no_llm)
        folder = write_draft(draft, week_ending, force=args.force)
        print(f"Draft written: {folder / 'draft.md'}  (facts={draft.n_facts}, flagged={draft.n_flagged})")
        return 0
    if args.cmd == "revision":
        r = decide_revision(week_ending, args.id, "accepted" if args.decision == "accept" else "dismissed",
                            args.reviewer, args.note, program=args.program)
        print(f"R{r['id']} {r['status']} by {r['decided_by']}")
        return 0
    meta = decide(week_ending, "approved" if args.cmd == "approve" else "rejected", args.reviewer, args.note,
                  program=args.program)
    print(f"{meta['status']} by {meta['reviewer']}; edit ratio {meta['edit_ratio']:.1%}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
