"""Program 4 fact extraction: typed claims with verbatim evidence.

For each in-scope article (default category ``military``) the fast-tier model
is asked for typed claims (``prompts/claims_p4.md``).  Every claim must carry
a ``quote`` copied from the article; the quote is located in the source text
and its character offset stored.  Claims are rejected (kept, with a reason,
for auditing) when:

- the quote is not found verbatim (after whitespace/width normalisation);
- ``claim_type`` is not one of the allowed types;
- ``event_date`` is malformed or later than the article's date + 1 day;
- ``quantity`` is not a non-negative integer.

Source text is the stored full text when present, else title + summary
(recorded as ``source_field``).  An article is extracted once per text
version and prompt version; re-runs skip it.  Analysts set a claim's review
status to ``confirmed`` or ``rejected``; nothing is published anywhere.

Off by default in the regular run (``PILOT_EXTRACTION_ENABLED``); the CLI
runs it on demand::

    python -m app.pilot.claims extract --days 7 --limit 20
    python -m app.pilot.claims list --days 7
    python -m app.pilot.claims review <claim_id> confirmed|rejected --reviewer NAME
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PROMPT_PATH = Path(__file__).resolve().parents[2] / "prompts" / "claims_p4.md"
CLAIM_TYPES = frozenset(
    {"pla_activity", "exercise", "incident", "statement", "arms", "policy", "other_security"}
)
REVIEW_STATUSES = ("unreviewed", "confirmed", "rejected")
MAX_SOURCE_CHARS = 6000
MIN_QUOTE_CHARS = 6


def prompt_version() -> str:
    try:
        text = PROMPT_PATH.read_text(encoding="utf-8")
    except OSError:
        return "missing"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS claims (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            article_url TEXT NOT NULL,
            claim_type TEXT NOT NULL,
            actor TEXT,
            action TEXT,
            target TEXT,
            location TEXT,
            event_date TEXT,
            quantity INTEGER,
            unit TEXT,
            attribution TEXT,
            quote TEXT NOT NULL,
            quote_start INTEGER,
            source_field TEXT NOT NULL,
            valid INTEGER NOT NULL,
            reject_reason TEXT,
            model TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            extracted_at TEXT NOT NULL,
            review_status TEXT NOT NULL DEFAULT 'unreviewed',
            reviewer TEXT,
            reviewed_at TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_claims_article ON claims(article_url);
        CREATE INDEX IF NOT EXISTS idx_claims_event_date ON claims(event_date);
        CREATE TABLE IF NOT EXISTS claim_extraction_runs (
            article_url TEXT NOT NULL,
            text_sha TEXT NOT NULL,
            prompt_version TEXT NOT NULL,
            model TEXT NOT NULL,
            status TEXT NOT NULL,
            n_valid INTEGER NOT NULL,
            n_rejected INTEGER NOT NULL,
            ran_at TEXT NOT NULL,
            PRIMARY KEY (article_url, text_sha, prompt_version)
        );
        """
    )


# ── validation ────────────────────────────────────────────────────────


def _norm(text: str) -> str:
    return re.sub(r"\s+", "", unicodedata.normalize("NFKC", text or ""))


def locate_quote(source: str, quote: str) -> int | None:
    """Character offset of ``quote`` in ``source`` ignoring whitespace/width; else None."""
    if not quote or not source:
        return None
    pos = source.find(quote)
    if pos >= 0:
        return pos
    target = _norm(quote)
    if len(target) < MIN_QUOTE_CHARS:
        return None
    # Map positions of the normalised source back to the original.
    chars, index = [], []
    for i, ch in enumerate(source):
        for n in unicodedata.normalize("NFKC", ch):
            if not n.isspace():
                chars.append(n)
                index.append(i)
    found = "".join(chars).find(target)
    return index[found] if found >= 0 else None


@dataclass(slots=True)
class Claim:
    claim_type: str
    actor: str | None
    action: str | None
    target: str | None
    location: str | None
    event_date: str | None
    quantity: int | None
    unit: str | None
    attribution: str | None
    quote: str
    quote_start: int | None
    valid: bool
    reject_reason: str | None


def _text(value) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    return value or None


def validate(raw: dict, source: str, published: date | None) -> Claim:
    reasons: list[str] = []
    claim_type = str(raw.get("claim_type") or "").strip()
    if claim_type not in CLAIM_TYPES:
        reasons.append(f"claim_type:{claim_type or 'missing'}")
    quote = str(raw.get("quote") or "").strip()
    start = locate_quote(source, quote)
    if len(_norm(quote)) < MIN_QUOTE_CHARS:
        reasons.append("quote_too_short")
    elif start is None:
        reasons.append("quote_not_in_source")
    event_date = _text(raw.get("event_date"))
    if event_date is not None:
        try:
            parsed = date.fromisoformat(event_date)
            if published is not None and parsed > published + timedelta(days=1):
                reasons.append("event_date_after_article")
        except ValueError:
            reasons.append("event_date_format")
    quantity = raw.get("quantity")
    if quantity is not None:
        if isinstance(quantity, bool) or not isinstance(quantity, (int, float, str)):
            reasons.append("quantity")
            quantity = None
        else:
            try:
                q = float(quantity)
                if q < 0 or q != int(q):
                    raise ValueError
                quantity = int(q)
            except (TypeError, ValueError):
                reasons.append("quantity")
                quantity = None
    return Claim(
        claim_type=claim_type or "missing",
        actor=_text(raw.get("actor")),
        action=_text(raw.get("action")),
        target=_text(raw.get("target")),
        location=_text(raw.get("location")),
        event_date=event_date,
        quantity=quantity,
        unit=_text(raw.get("unit")),
        attribution=_text(raw.get("attribution")),
        quote=quote,
        quote_start=start,
        valid=not reasons,
        reject_reason=",".join(reasons) or None,
    )


def parse_response(result: dict) -> list[dict]:
    claims = result.get("claims") if isinstance(result, dict) else None
    return [c for c in claims if isinstance(c, dict)] if isinstance(claims, list) else []


# ── extraction ────────────────────────────────────────────────────────


def enabled() -> bool:
    return os.getenv("PILOT_EXTRACTION_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def inscope_categories() -> set[str]:
    raw = os.getenv("PILOT_CATEGORIES", "military")
    return {c.strip() for c in raw.split(",") if c.strip()}


@dataclass(slots=True)
class SourceDoc:
    url: str
    title: str
    published: date | None
    text: str
    field: str

    @property
    def sha(self) -> str:
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def pending_sources(conn: sqlite3.Connection, *, days: int, limit: int, now: datetime | None = None) -> list[SourceDoc]:
    ensure_tables(conn)
    cats = sorted(inscope_categories())
    since = ((now or datetime.now(timezone.utc)) - timedelta(days=days)).date().isoformat()
    marks = ",".join("?" * len(cats))
    rows = conn.execute(
        f"""
        SELECT a.url, a.title, a.summary, COALESCE(a.published_at, a.fetched_at), t.text
        FROM articles a LEFT JOIN article_texts t ON t.url = a.url
        WHERE a.category IN ({marks}) AND a.delivery_eligible = 1
          AND COALESCE(a.published_at, a.fetched_at) >= ?
        ORDER BY COALESCE(a.published_at, a.fetched_at) DESC
        """,
        (*cats, since),
    ).fetchall()
    version = prompt_version()
    done = {(r[0], r[1]) for r in conn.execute(
        "SELECT article_url, text_sha FROM claim_extraction_runs WHERE prompt_version = ? AND status = 'ok'",
        (version,),
    )}
    out: list[SourceDoc] = []
    for url, title, summary, when, text in rows:
        if text:
            body, field = text[:MAX_SOURCE_CHARS], "text"
        else:
            body, field = "\n".join(x for x in (title, summary) if x), "title_summary"
        try:
            published = date.fromisoformat(str(when)[:10])
        except ValueError:
            published = None
        doc = SourceDoc(url, title, published, body, field)
        if (url, doc.sha) in done:
            continue
        out.append(doc)
        if len(out) >= limit:
            break
    return out


def _system_prompt() -> str:
    return PROMPT_PATH.read_text(encoding="utf-8")


def extract_one(doc: SourceDoc, client) -> tuple[str, list[Claim]]:
    user = json.dumps(
        {"url": doc.url, "published_date": doc.published.isoformat() if doc.published else None,
         "title": doc.title, "text": doc.text},
        ensure_ascii=False,
    )
    result = client.analyze(_system_prompt(), user)
    if result.get("status") != "success":
        return str(result.get("error") or "error"), []
    return "ok", [validate(raw, doc.text, doc.published) for raw in parse_response(result)]


def save_claims(conn: sqlite3.Connection, doc: SourceDoc, status: str, claims: list[Claim], *,
                model: str, now: datetime | None = None) -> None:
    ensure_tables(conn)
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    version = prompt_version()
    if status == "ok":
        # A new text/prompt version replaces this article's unreviewed claims.
        conn.execute(
            "DELETE FROM claims WHERE article_url = ? AND review_status = 'unreviewed'", (doc.url,)
        )
        conn.executemany(
            "INSERT INTO claims(article_url, claim_type, actor, action, target, location, event_date, "
            "quantity, unit, attribution, quote, quote_start, source_field, valid, reject_reason, model, "
            "prompt_version, extracted_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (doc.url, c.claim_type, c.actor, c.action, c.target, c.location, c.event_date, c.quantity,
                 c.unit, c.attribution, c.quote, c.quote_start, doc.field, int(c.valid), c.reject_reason,
                 model, version, stamp)
                for c in claims
            ],
        )
    conn.execute(
        "INSERT INTO claim_extraction_runs(article_url, text_sha, prompt_version, model, status, n_valid, "
        "n_rejected, ran_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(article_url, text_sha, prompt_version) "
        "DO UPDATE SET model=excluded.model, status=excluded.status, n_valid=excluded.n_valid, "
        "n_rejected=excluded.n_rejected, ran_at=excluded.ran_at",
        (doc.url, doc.sha, version, model, status, sum(c.valid for c in claims),
         sum(not c.valid for c in claims), stamp),
    )
    conn.commit()


def run_extraction(conn: sqlite3.Connection, *, days: int = 7, limit: int = 20, client=None,
                   now: datetime | None = None) -> dict:
    if client is None:
        from ..ai_client import make_client

        client = make_client("extraction", "fast")
    if client is None:
        return {"status": "no_model_configured"}
    docs = pending_sources(conn, days=days, limit=limit, now=now)
    totals = {"status": "ok", "articles": 0, "valid": 0, "rejected": 0, "failed": 0}
    for doc in docs:
        status, claims = extract_one(doc, client)
        save_claims(conn, doc, status, claims, model=client.model, now=now)
        if status != "ok":
            totals["failed"] += 1
            if status == "budget_exceeded":
                totals["status"] = "budget_exceeded"
                break
            continue
        totals["articles"] += 1
        totals["valid"] += sum(c.valid for c in claims)
        totals["rejected"] += sum(not c.valid for c in claims)
    return totals


def run_safe(db, **kw) -> dict:
    """Regular-run hook (PILOT_EXTRACTION_ENABLED); never raises."""
    if db is None or not enabled():
        return {"status": "disabled"}
    try:
        limit = int(os.getenv("PILOT_EXTRACTION_MAX_PER_RUN", "10"))
        return run_extraction(db.conn, days=2, limit=limit, **kw)
    except Exception as exc:  # noqa: BLE001
        logger.warning("Claim extraction failed safely: %s", exc)
        return {"status": "error", "error": str(exc)}


# ── reading and review ────────────────────────────────────────────────


def claims_between(conn: sqlite3.Connection, start: date, end: date, *, include_invalid: bool = False,
                   include_rejected: bool = False) -> list[dict]:
    """Claims whose event date (or article date when none) falls in [start, end]."""
    ensure_tables(conn)
    where = ["COALESCE(c.event_date, substr(COALESCE(a.published_at, a.fetched_at), 1, 10)) BETWEEN ? AND ?"]
    if not include_invalid:
        where.append("c.valid = 1")
    if not include_rejected:
        where.append("c.review_status != 'rejected'")
    cur = conn.execute(
        f"""
        SELECT c.id, c.claim_type, c.actor, c.action, c.target, c.location, c.event_date, c.quantity, c.unit,
               c.attribution, c.quote, c.source_field, c.valid, c.reject_reason, c.review_status,
               a.url, a.title, a.source_name, COALESCE(a.published_at, a.fetched_at)
        FROM claims c JOIN articles a ON a.url = c.article_url
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(c.event_date, a.published_at), c.id
        """,
        (start.isoformat(), end.isoformat()),
    )
    keys = [d[0] for d in cur.description]
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def set_review(conn: sqlite3.Connection, claim_id: int, status: str, reviewer: str,
               now: datetime | None = None) -> bool:
    if status not in REVIEW_STATUSES:
        raise ValueError(f"status must be one of {REVIEW_STATUSES}")
    ensure_tables(conn)
    cur = conn.execute(
        "UPDATE claims SET review_status = ?, reviewer = ?, reviewed_at = ? WHERE id = ?",
        (status, reviewer, (now or datetime.now(timezone.utc)).isoformat(), claim_id),
    )
    conn.commit()
    return cur.rowcount == 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.pilot.claims")
    sub = parser.add_subparsers(dest="cmd", required=True)
    ex = sub.add_parser("extract", help="extract claims from recent in-scope articles (uses the AI budget)")
    ex.add_argument("--days", type=int, default=7)
    ex.add_argument("--limit", type=int, default=20)
    ls = sub.add_parser("list", help="list claims")
    ls.add_argument("--days", type=int, default=7)
    ls.add_argument("--all", action="store_true", help="include invalid and rejected claims")
    rv = sub.add_parser("review", help="mark a claim confirmed or rejected")
    rv.add_argument("claim_id", type=int)
    rv.add_argument("status", choices=REVIEW_STATUSES)
    rv.add_argument("--reviewer", required=True)
    for p in (ex, ls, rv):
        p.add_argument("--db", help="news DB path (default: the configured news DB)")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    from ..database import Database
    from ..settings import get_settings

    with Database(args.db or get_settings().news_db_path) as db:
        if args.cmd == "extract":
            print(run_extraction(db.conn, days=args.days, limit=args.limit))
        elif args.cmd == "list":
            end = datetime.now(timezone.utc).date()
            rows = claims_between(db.conn, end - timedelta(days=args.days), end,
                                  include_invalid=args.all, include_rejected=args.all)
            for r in rows:
                state = r["review_status"] if r["valid"] else f"INVALID({r['reject_reason']})"
                qty = f" {r['quantity']}{r['unit'] or ''}" if r["quantity"] is not None else ""
                print(f"#{r['id']} [{r['claim_type']}] {r['event_date'] or '????-??-??'} "
                      f"{r['actor'] or ''} {r['action'] or ''}{qty}  <{state}>")
                print(f"     「{r['quote']}」 — {r['source_name']} {r['url']}")
            print(f"{len(rows)} claims")
        else:
            ok = set_review(db.conn, args.claim_id, args.status, args.reviewer)
            print("updated" if ok else "no such claim")
            return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
