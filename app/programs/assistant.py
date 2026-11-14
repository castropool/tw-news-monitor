"""Research assistant over the stored record (design capability C10).

Answers a question only from retrieved records, each with a stable id:
``A<article id>`` (articles, via full-text search), ``C<claim id>`` (valid,
non-rejected facts; unconfirmed allegations excluded) and ``R<relation id>``
(registry relations, sensitive ones only when confirmed).

- Retrieval terms: the ``--terms`` given, else every configured program term,
  registry entity name or alias that appears in the question.
- Too little evidence (fewer than ``min_records``) → answers 證據不足 and lists
  what was found, **without** calling the model.
- Otherwise the strong-tier model answers (``prompts/assistant.md``) and the
  answer is checked: every sentence must cite retrieved ids, and every number
  it states must appear in the records it cites.  Failing sentences are kept
  and marked ⚠ for the reader.

CLI::

    python -m app.programs.assistant ask "共機逾越中線最近有什麼變化？" [--program P4] [--terms 共機,中線]
    python -m app.programs.assistant eval [--llm]
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import yaml

from .. import programs as prog
from .. import search
from ..election2026.hanzi_utils import to_traditional
from ..pilot import claims as cl
from . import registry

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "assistant.md"
EVAL_PATH = Path(__file__).resolve().parents[2] / "config" / "assistant_eval.yaml"
NUMBER_RE = re.compile(r"\d+(?:\.\d+)?")
MIN_RECORDS = 3


@dataclass(slots=True)
class Record:
    id: str
    when: str
    text: str
    source: str


@dataclass(slots=True)
class Sentence:
    text: str
    citations: list[str]
    notes: list[str] = field(default_factory=list)


@dataclass(slots=True)
class Answer:
    question: str
    terms: list[str]
    records: list[Record]
    sentences: list[Sentence]
    insufficient: bool
    model: str | None
    error: str | None = None


def _fold(text: str) -> str:
    return to_traditional(text or "").lower()


def question_terms(conn: sqlite3.Connection, question: str) -> list[str]:
    """Known terms (program routing terms, entity names/aliases) found in the question."""
    folded = _fold(question)
    candidates: set[str] = set()
    for p in prog.load()["programs"].values():
        r = p.get("routing") or {}
        candidates |= set(r.get("strong") or []) | set(r.get("support") or [])
    registry.ensure_tables(conn)
    for name, aliases in conn.execute("SELECT name, aliases_json FROM entities"):
        candidates.add(name)
        candidates |= set(json.loads(aliases))
    found = sorted({t for t in candidates if len(t) >= 2 and _fold(t) in folded}, key=len, reverse=True)
    # Drop terms contained in a longer matched term (台積電赴美 covers 台積電).
    return [t for t in found if not any(t != o and _fold(t) in _fold(o) for o in found)]


def retrieve(conn: sqlite3.Connection, terms: list[str], *, program: str | None = None, days: int | None = None,
             limit: int = 12) -> list[Record]:
    if not terms:
        return []
    since = (datetime.now(timezone.utc).date() - timedelta(days=days)) if days else None
    records: list[Record] = []
    # Facts first: they are the checked layer.
    end = datetime.now(timezone.utc).date() + timedelta(days=1)
    start = since or date(2000, 1, 1)
    facts = cl.claims_between(conn, start, end, program=program)
    scored = []
    for f in facts:
        hay = _fold(" ".join(str(f.get(k) or "") for k in ("actor", "action", "target", "location", "quote")))
        hits = sum(1 for t in terms if _fold(t) in hay)
        if hits:
            scored.append((hits, f["event_date"] or "", f))
    scored.sort(key=lambda x: x[1], reverse=True)  # newest first ...
    scored.sort(key=lambda x: -x[0])                  # ... within the same number of matched terms
    for _, _, f in scored[:limit]:
        qty = f" {f['quantity']}{f['unit'] or ''}" if f["quantity"] is not None else ""
        records.append(Record(f"C{f['id']}", f["event_date"] or "", f"{f['actor'] or ''} {f['action'] or ''}{qty}："
                              f"「{f['quote']}」", f"{f['source_name']} {f['url']}"))
    # Articles via full-text search, one term at a time (OR), ranked by terms matched.
    try:
        search.sync_index(conn)
        hits: dict[str, tuple[int, search.SearchHit]] = {}
        for t in terms:
            for h in search.search(conn, t, since=since, limit=limit, sync=False):
                n, _ = hits.get(h.url, (0, h))
                hits[h.url] = (n + 1, h)
        ids = dict(conn.execute("SELECT url, id FROM articles WHERE url IN (%s)" % ",".join("?" * len(hits)),
                                list(hits)).fetchall()) if hits else {}
        for n, h in sorted(hits.values(), key=lambda x: (-x[0], x[1].published_at or ""))[:limit]:
            records.append(Record(f"A{ids[h.url]}", (h.published_at or "")[:10], f"{h.title}　{h.snippet}",
                                  f"{h.source_name} {h.url}"))
    except search.SearchUnavailable:
        pass
    # Registry relations of entities named in the terms.
    for t in terms:
        for ent in registry.find_entity(conn, t):
            for r in registry.relations_for(conn, ent["id"]):
                mark = "" if r["status"] == "confirmed" else "（未確認）"
                records.append(Record(f"R{r['id']}", r["valid_from"] or "",
                                      f"{r['subject']} {registry.PREDICATES[r['predicate']]} {r['object']}"
                                      f"（{r['valid_from'] or '?'} 至 {r['valid_to'] or '今'}）{mark}",
                                      r["source_url"] or f"claim#{r['source_claim_id']}"))
    seen, unique = set(), []
    for rec in records:
        if rec.id not in seen:
            seen.add(rec.id)
            unique.append(rec)
    return unique


def check(sentences: list[Sentence], records: list[Record]) -> None:
    by_id = {r.id: r for r in records}
    for s in sentences:
        missing = [c for c in s.citations if c not in by_id]
        if missing:
            s.notes.append(f"引用不存在的記錄 {missing}")
        if not s.citations:
            s.notes.append("無引用")
        allowed = set()
        for c in s.citations:
            if c in by_id:
                allowed |= set(NUMBER_RE.findall(by_id[c].text + " " + by_id[c].when))
                allowed |= {str(int(x)) for x in NUMBER_RE.findall(by_id[c].when) if x.isdigit()}
        unverified = sorted({x for x in NUMBER_RE.findall(s.text) if x not in allowed})
        if unverified:
            s.notes.append(f"未核實數字 {unverified}")


def ask(conn: sqlite3.Connection, question: str, *, terms: list[str] | None = None, program: str | None = None,
        days: int | None = None, client=None, use_llm: bool = True, min_records: int = MIN_RECORDS) -> Answer:
    terms = terms or question_terms(conn, question)
    records = retrieve(conn, terms, program=program, days=days)
    if len(records) < min_records:
        return Answer(question, terms, records, [Sentence(
            f"證據不足：只找到 {len(records)} 筆相關記錄（至少需要 {min_records} 筆）。"
            + ("請改用更具體的詞語，或先擴充資料來源。" if records else "未找到任何相關記錄。"), [])], True, None)
    if not use_llm:
        return Answer(question, terms, records, [], False, None, "未使用模型")
    if client is None:
        from ..ai_client import make_client

        client = make_client("assistant", "strong")
    if client is None:
        return Answer(question, terms, records, [], False, None, "未配置模型")
    payload = {"question": question,
               "records": [{"id": r.id, "date": r.when, "text": r.text, "source": r.source} for r in records]}
    result = client.analyze(PROMPT_PATH.read_text(encoding="utf-8"), json.dumps(payload, ensure_ascii=False))
    if result.get("status") != "success":
        return Answer(question, terms, records, [], False, client.model, str(result.get("error")))
    sentences = [Sentence(str(s.get("text") or "").strip(), [str(c) for c in s.get("citations") or []])
                 for s in result.get("answer") or [] if isinstance(s, dict) and str(s.get("text") or "").strip()]
    check(sentences, records)
    return Answer(question, terms, records, sentences, bool(result.get("insufficient")), client.model)


def render(a: Answer) -> str:
    lines = [f"問題：{a.question}", f"檢索詞：{'、'.join(a.terms) or '（無）'}", ""]
    if a.error:
        lines.append(f"（回答未生成：{a.error}）")
    for s in a.sentences:
        cites = "".join(f"[{c}]" for c in s.citations)
        lines.append(f"- {s.text} {cites}".rstrip())
        if s.notes:
            lines.append(f"  ⚠ {'；'.join(s.notes)}")
    if a.insufficient and a.sentences and not a.sentences[0].text.startswith("證據不足"):
        lines.append("（模型判斷：證據不足）")
    lines += ["", f"記錄（{len(a.records)}）："]
    for r in a.records:
        lines.append(f"[{r.id}] {r.when} {r.text[:120]} — {r.source}")
    return "\n".join(lines)


# ── fixed evaluation set ──────────────────────────────────────────────


def load_eval(path: Path = EVAL_PATH) -> list[dict]:
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return data.get("questions") or []


def evaluate(conn: sqlite3.Connection, questions: list[dict], *, client=None, use_llm: bool = False) -> dict:
    """Retrieval recall (expected terms found) and, with a model, citation validity."""
    rows = []
    for q in questions:
        a = ask(conn, q["question"], terms=q.get("terms"), program=q.get("program"), client=client, use_llm=use_llm)
        text = " ".join(r.text for r in a.records)
        expected = q.get("expect_in_records") or []
        found = [t for t in expected if _fold(t) in _fold(text)]
        valid_sentences = sum(1 for s in a.sentences if not s.notes)
        rows.append({
            "id": q["id"], "records": len(a.records), "insufficient": a.insufficient,
            "expected_insufficient": bool(q.get("expect_insufficient")),
            "recall": (len(found) / len(expected)) if expected else None,
            "sentences": len(a.sentences), "valid_sentences": valid_sentences,
        })
    recalls = [r["recall"] for r in rows if r["recall"] is not None]
    correct_refusals = sum(1 for r in rows if r["expected_insufficient"] and r["insufficient"])
    expected_refusals = sum(1 for r in rows if r["expected_insufficient"])
    total_s = sum(r["sentences"] for r in rows)
    return {
        "questions": rows,
        "mean_recall": round(sum(recalls) / len(recalls), 3) if recalls else None,
        "refusal_rate_when_expected": (correct_refusals / expected_refusals) if expected_refusals else None,
        "citation_validity": (sum(r["valid_sentences"] for r in rows) / total_s) if total_s else None,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.programs.assistant")
    sub = parser.add_subparsers(dest="cmd", required=True)
    a = sub.add_parser("ask")
    a.add_argument("question")
    a.add_argument("--program", choices=prog.PROGRAM_IDS)
    a.add_argument("--terms", help="comma-separated search terms (default: detected from the question)")
    a.add_argument("--days", type=int)
    a.add_argument("--no-llm", action="store_true", help="retrieval only")
    e = sub.add_parser("eval")
    e.add_argument("--llm", action="store_true", help="also generate answers (uses the AI budget)")
    for p in (a, e):
        p.add_argument("--db")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    from ..database import Database
    from ..settings import get_settings

    with Database(args.db or get_settings().news_db_path) as db:
        if args.cmd == "ask":
            terms = [t.strip() for t in args.terms.split(",")] if args.terms else None
            print(render(ask(db.conn, args.question, terms=terms, program=args.program, days=args.days,
                             use_llm=not args.no_llm)))
        else:
            report = evaluate(db.conn, load_eval(), use_llm=args.llm)
            for row in report["questions"]:
                print(row)
            print({k: v for k, v in report.items() if k != "questions"})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
