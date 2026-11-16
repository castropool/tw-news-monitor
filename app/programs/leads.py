"""Research gaps and approved search proposals (design capability C9).

1. **Gaps**: for each program question, the number of valid facts linked to
   it over the last ``days`` (default 14).  A question with none is a gap.
2. **Proposals**: each gap gets a proposed search built from the question's
   first indicator, stored as ``proposed``.  Nothing is searched yet.
3. **Approval**: the analyst approves (optionally rewriting the query) or
   rejects each proposal.  Only approved proposals run.
4. **Run**: an approved proposal searches the approved source only (Google
   News RSS, ``LEADS_ALLOWED_HOSTS``), saves new articles through the normal
   article table (deduplicated by URL), tags them to the program and records
   how many were found.

The design's open question on proactive search is answered conservatively:
every search needs a human approval first.

CLI::

    python -m app.programs.leads gaps --program P1
    python -m app.programs.leads propose --program P1
    python -m app.programs.leads list
    python -m app.programs.leads approve <id> --reviewer NAME [--query "..."]
    python -m app.programs.leads reject <id> --reviewer NAME
    python -m app.programs.leads run
"""

from __future__ import annotations

import argparse
import os
import re
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from urllib.parse import quote, urlsplit

from .. import programs as prog
from ..pilot import claims as cl

PROGRAM_CATEGORY = {"P1": "international", "P2": "politics", "P3": "international", "P4": "military",
                    "P5": "economy", "P6": "politics"}
DEFAULT_ALLOWED_HOSTS = ("news.google.com",)


def allowed_hosts() -> tuple[str, ...]:
    raw = os.getenv("LEADS_ALLOWED_HOSTS", "")
    hosts = tuple(h.strip() for h in raw.split(",") if h.strip())
    return hosts or DEFAULT_ALLOWED_HOSTS


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS research_leads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            program TEXT NOT NULL,
            question_id TEXT NOT NULL,
            query TEXT NOT NULL,
            reason TEXT NOT NULL,
            status TEXT NOT NULL DEFAULT 'proposed',
            created_at TEXT NOT NULL,
            decided_by TEXT,
            decided_at TEXT,
            executed_at TEXT,
            items_found INTEGER
        )
        """
    )
    conn.commit()


def gaps(conn: sqlite3.Connection, program: str, *, days: int = 14, today: date | None = None) -> list[dict]:
    today = today or datetime.now(timezone.utc).date()
    facts = cl.claims_between(conn, today - timedelta(days=days), today, program=program)
    if program == "P4":
        counts = {q["id"]: len(facts) for q in prog.program(program)["questions"]}  # P4 claims are not question-linked
    else:
        ids = [f["id"] for f in facts]
        counts = {q["id"]: 0 for q in prog.program(program)["questions"]}
        if ids:
            marks = ",".join("?" * len(ids))
            for qid, n in conn.execute(
                f"SELECT question_id, COUNT(*) FROM claim_questions WHERE claim_id IN ({marks}) GROUP BY question_id", ids):
                if qid in counts:
                    counts[qid] = n
    return [{"question_id": q["id"], "text": q["text"], "facts": counts.get(q["id"], 0),
             "indicators": q["indicators"], "gap": counts.get(q["id"], 0) == 0}
            for q in prog.program(program)["questions"]]


def _query_from_indicator(indicator: str) -> str:
    words = [w for w in re.split(r"[\s、，,與和及（）()/]+", indicator) if len(w) >= 2]
    return " ".join(words[:3]) + " 台灣"


def propose(conn: sqlite3.Connection, program: str, *, days: int = 14, now: datetime | None = None) -> int:
    """One proposal per gap question that has no open proposal yet."""
    ensure_table(conn)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    open_q = {r[0] for r in conn.execute(
        "SELECT question_id FROM research_leads WHERE program = ? AND status IN ('proposed', 'approved')", (program,))}
    created = 0
    for g in gaps(conn, program, days=days, today=(now or datetime.now(timezone.utc)).date()):
        if not g["gap"] or g["question_id"] in open_q:
            continue
        conn.execute(
            "INSERT INTO research_leads(program, question_id, query, reason, created_at) VALUES (?, ?, ?, ?, ?)",
            (program, g["question_id"], _query_from_indicator(g["indicators"][0]),
             f"近 {days} 天沒有與 {g['question_id']}（{g['text']}）相關的事實；依指標「{g['indicators'][0]}」補充搜尋。",
             stamp),
        )
        created += 1
    conn.commit()
    return created


def decide(conn: sqlite3.Connection, lead_id: int, decision: str, reviewer: str, *, query: str | None = None,
           now: datetime | None = None) -> bool:
    if decision not in ("approved", "rejected"):
        raise ValueError("decision must be approved or rejected")
    ensure_table(conn)
    params = [decision, reviewer, (now or datetime.now(timezone.utc)).isoformat()]
    sql = "UPDATE research_leads SET status = ?, decided_by = ?, decided_at = ?"
    if query:
        sql += ", query = ?"
        params.append(query.strip())
    cur = conn.execute(sql + " WHERE id = ? AND status = 'proposed'", (*params, lead_id))
    conn.commit()
    return cur.rowcount == 1


def feed_url(query: str) -> str:
    return (f"https://news.google.com/rss/search?q={quote(query + ' when:30d')}"
            f"&hl=zh-TW&gl=TW&ceid=TW:zh-Hant")


def run_approved(db, *, collector_factory=None, now: datetime | None = None) -> list[dict]:
    """Execute approved proposals only (``db`` is an open Database); returns per-lead results."""
    from . import tagging

    conn = db.conn
    ensure_table(conn)
    if collector_factory is None:
        from ..collectors.google_news import GoogleNewsRSSCollector as collector_factory
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    results = []
    for lead_id, program, qid, query in conn.execute(
            "SELECT id, program, question_id, query FROM research_leads WHERE status = 'approved' ORDER BY id").fetchall():
        url = feed_url(query)
        if urlsplit(url).hostname not in allowed_hosts():
            results.append({"id": lead_id, "status": "host_not_allowed"})
            continue
        source = {"id": f"lead_{lead_id}", "name": f"研究線索 {qid}", "category": PROGRAM_CATEGORY[program],
                  "type": "google_news_rss", "url": url, "enabled": True}
        try:
            articles = collector_factory(source).collect()
        except Exception as exc:  # noqa: BLE001
            results.append({"id": lead_id, "status": "error", "error": str(exc)})
            continue
        inserted = db.save_articles(articles)
        tagging.ensure_table(conn)
        conn.executemany(
            "INSERT INTO program_tags(url, program, reason, tagged_at) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(url, program) DO NOTHING",
            [(a.url, program, f'["lead:{lead_id}"]', stamp) for a in articles],
        )
        conn.execute("UPDATE research_leads SET status = 'executed', executed_at = ?, items_found = ? WHERE id = ?",
                     (stamp, len(articles), lead_id))
        conn.commit()
        results.append({"id": lead_id, "status": "executed", "found": len(articles), "new": len(inserted)})
    return results


def list_leads(conn: sqlite3.Connection) -> list[dict]:
    ensure_table(conn)
    cur = conn.execute("SELECT id, program, question_id, query, status, reason, decided_by, items_found "
                       "FROM research_leads ORDER BY id DESC")
    keys = [d[0] for d in cur.description]
    return [dict(zip(keys, r)) for r in cur.fetchall()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.programs.leads")
    sub = parser.add_subparsers(dest="cmd", required=True)
    g = sub.add_parser("gaps")
    g.add_argument("--program", required=True, choices=prog.PROGRAM_IDS)
    g.add_argument("--days", type=int, default=14)
    p = sub.add_parser("propose")
    p.add_argument("--program", required=True, choices=prog.PROGRAM_IDS)
    p.add_argument("--days", type=int, default=14)
    ls = sub.add_parser("list")
    ap = sub.add_parser("approve")
    ap.add_argument("lead_id", type=int)
    ap.add_argument("--reviewer", required=True)
    ap.add_argument("--query")
    rj = sub.add_parser("reject")
    rj.add_argument("lead_id", type=int)
    rj.add_argument("--reviewer", required=True)
    rn = sub.add_parser("run")
    for x in (g, p, ls, ap, rj, rn):
        x.add_argument("--db")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    from ..database import Database
    from ..settings import get_settings

    with Database(args.db or get_settings().news_db_path) as db:
        conn = db.conn
        if args.cmd == "gaps":
            for row in gaps(conn, args.program, days=args.days):
                print(f"{row['question_id']} facts={row['facts']}{'  ← 缺口' if row['gap'] else ''}  {row['text']}")
        elif args.cmd == "propose":
            print(f"proposals created: {propose(conn, args.program, days=args.days)}")
        elif args.cmd == "list":
            for r in list_leads(conn):
                print(f"#{r['id']} [{r['status']}] {r['program']} {r['question_id']} 「{r['query']}」 {r['reason']}")
        elif args.cmd in ("approve", "reject"):
            ok = decide(conn, args.lead_id, "approved" if args.cmd == "approve" else "rejected", args.reviewer,
                        query=getattr(args, "query", None))
            print("updated" if ok else "no such open proposal")
            return 0 if ok else 1
        else:
            for r in run_approved(db):
                print(r)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
