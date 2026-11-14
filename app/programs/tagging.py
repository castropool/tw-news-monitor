"""Tag articles to research programs (an article may belong to several).

Deterministic rules from ``config/programs.yaml`` on title + summary, after
Simplified -> Traditional folding: a category match, any one ``strong``
term, or at least ``min_support`` distinct ``support`` terms.  Tags live in
their own table, ``program_tags`` (created here, not in Database.connect()),
with the matched terms as the stated reason.

Off in the regular run unless ``PROGRAMS_TAGGING_ENABLED`` is set; the CLI
tags any date range::

    python -m app.programs.tagging --days 30
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import sys
from datetime import datetime, timedelta, timezone

from ..election2026.hanzi_utils import to_traditional
from . import load

logger = logging.getLogger(__name__)


def ensure_table(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS program_tags (
            url TEXT NOT NULL,
            program TEXT NOT NULL,
            reason TEXT NOT NULL,
            tagged_at TEXT NOT NULL,
            PRIMARY KEY (url, program)
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_program_tags_program ON program_tags(program)")
    conn.commit()


def _fold(text: str) -> str:
    return to_traditional(text or "").lower()


def match(title: str, summary: str | None, category: str, cfg: dict | None = None) -> dict[str, list[str]]:
    """program -> reasons (matched category/terms) for one article."""
    cfg = cfg or load()
    text = _fold(f"{title} {summary or ''}")
    out: dict[str, list[str]] = {}
    for pid, p in cfg["programs"].items():
        r = p.get("routing") or {}
        if category in (r.get("categories") or []):
            out[pid] = [f"category:{category}"]
            continue
        strong = [t for t in r.get("strong") or [] if _fold(t) in text]
        if strong:
            out[pid] = [f"strong:{t}" for t in strong]
            continue
        support = sorted({t for t in r.get("support") or [] if _fold(t) in text})
        if len(support) < int(r.get("min_support", 2)):
            continue
        required = r.get("require_any") or []
        anchors = [t for t in required if _fold(t) in text]
        if required and not anchors:
            continue
        out[pid] = [f"support:{t}" for t in support] + [f"actor:{t}" for t in anchors]
    return out


def tag_range(conn: sqlite3.Connection, since: datetime, until: datetime | None = None,
              *, now: datetime | None = None) -> dict[str, int]:
    """Tag eligible articles fetched or published in the range; returns counts per program."""
    ensure_table(conn)
    cfg = load()
    stamp = (now or datetime.now(timezone.utc)).isoformat()
    until = until or (now or datetime.now(timezone.utc))
    rows = conn.execute(
        "SELECT url, title, summary, category FROM articles a WHERE (a.delivery_eligible = 1 OR a.filter_reason = 'research_only') "
        "AND COALESCE(published_at, fetched_at) >= ? AND COALESCE(published_at, fetched_at) <= ?",
        (since.isoformat()[:19], until.isoformat()[:19] + "~"),
    ).fetchall()
    counts: dict[str, int] = {}
    batch = []
    for url, title, summary, category in rows:
        for pid, reasons in match(title, summary, category, cfg).items():
            batch.append((url, pid, json.dumps(reasons, ensure_ascii=False), stamp))
            counts[pid] = counts.get(pid, 0) + 1
    conn.executemany(
        "INSERT INTO program_tags(url, program, reason, tagged_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT(url, program) DO UPDATE SET reason = excluded.reason, tagged_at = excluded.tagged_at",
        batch,
    )
    conn.commit()
    return counts


def urls_for(conn: sqlite3.Connection, program: str) -> set[str]:
    ensure_table(conn)
    return {r[0] for r in conn.execute("SELECT url FROM program_tags WHERE program = ?", (program,))}


def programs_for(conn: sqlite3.Connection, url: str) -> list[str]:
    ensure_table(conn)
    return [r[0] for r in conn.execute("SELECT program FROM program_tags WHERE url = ? ORDER BY program", (url,))]


def enabled() -> bool:
    return os.getenv("PROGRAMS_TAGGING_ENABLED", "").strip().lower() in ("1", "true", "yes", "on")


def run_safe(db, *, now: datetime | None = None) -> dict:
    """Regular-run hook: tag the last two days; never raises."""
    if db is None or not enabled():
        return {"status": "disabled"}
    try:
        now = now or datetime.now(timezone.utc)
        return {"status": "ok", "tagged": tag_range(db.conn, now - timedelta(days=2), now, now=now)}
    except Exception as exc:  # noqa: BLE001
        logger.warning("Program tagging failed safely: %s", exc)
        return {"status": "error", "error": str(exc)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.programs.tagging")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--db", help="news DB path (default: the configured news DB)")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    from ..database import Database
    from ..settings import get_settings

    now = datetime.now(timezone.utc)
    with Database(args.db or get_settings().news_db_path) as db:
        counts = tag_range(db.conn, now - timedelta(days=args.days), now, now=now)
    for pid in sorted(counts):
        print(f"{pid}: {counts[pid]} articles")
    if not counts:
        print("No articles matched any program.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
