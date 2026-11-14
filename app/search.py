"""Chinese-aware full-text search over stored articles.

Indexes each article's title, summary and stored full text (``article_texts``)
in an SQLite FTS5 table.  Chinese has no spaces between words, so every Han
character is indexed as its own token and a query term becomes a phrase of
adjacent characters: ``國防部`` matches only the three characters in order.
Simplified input is folded to Traditional (``app.election2026.hanzi_utils``)
on both sides, so ``国防部`` and ``國防部`` find the same articles.

The index lives in the news DB but is created lazily here, never in
``Database.connect()``: an SQLite build without FTS5 only disables search and
cannot break news collection.  The index is brought up to date incrementally
before each search (new articles, changed summaries, added or purged text).

CLI::

    python -m app.search 國防部 共機 --since 2026-11-01 --limit 20
    python -m app.search --rebuild
"""

from __future__ import annotations

import argparse
import logging
import re
import sqlite3
import sys
from dataclasses import dataclass
from datetime import date, datetime

from .election2026.hanzi_utils import to_traditional

logger = logging.getLogger(__name__)

_HAN = "㐀-䶿一-鿿豈-﫿"
_HAN_RE = re.compile(f"([{_HAN}])")
_TOKEN_RE = re.compile(r"[^\W_]+")
SYNC_BATCH = 500
# bm25 weights per column: url (unindexed), title, summary, body.
_BM25 = "bm25(article_search, 0.0, 6.0, 2.0, 1.0)"


class SearchUnavailable(RuntimeError):
    """SQLite was built without FTS5."""


def _tokens(text: str | None) -> list[str]:
    if not text:
        return []
    spaced = _HAN_RE.sub(r" \1 ", to_traditional(text).lower())
    return _TOKEN_RE.findall(spaced)


def index_text(text: str | None) -> str:
    """Normalized, one-token-per-Han-character form stored in the index."""
    return " ".join(_tokens(text))


def build_match(query: str) -> str | None:
    """Space-separated terms, all required; each term is an exact phrase."""
    phrases = []
    for term in query.split():
        tokens = _tokens(term)
        if tokens:
            phrases.append('"' + " ".join(tokens) + '"')
    return " AND ".join(phrases) if phrases else None


def ensure_index(conn: sqlite3.Connection) -> None:
    try:
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS article_search "
            "USING fts5(url UNINDEXED, title, summary, body, tokenize='unicode61')"
        )
    except sqlite3.OperationalError as exc:
        raise SearchUnavailable(f"SQLite FTS5 unavailable: {exc}") from exc
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS article_search_state (
            article_id INTEGER PRIMARY KEY,
            title TEXT,
            summary TEXT,
            text_sha TEXT
        )
        """
    )
    conn.commit()


def sync_index(conn: sqlite3.Connection, *, rebuild: bool = False) -> int:
    """Index new or changed articles; returns the number (re)indexed."""
    ensure_index(conn)
    if rebuild:
        conn.execute("DELETE FROM article_search")
        conn.execute("DELETE FROM article_search_state")
        conn.commit()
    stale_sql = """
        SELECT a.id, a.url, a.title, a.summary, t.text, t.content_sha256
        FROM articles a
        LEFT JOIN article_texts t ON t.url = a.url
        LEFT JOIN article_search_state s ON s.article_id = a.id
        WHERE a.id > ?
          AND (s.article_id IS NULL
               OR s.title IS NOT a.title
               OR s.summary IS NOT a.summary
               OR s.text_sha IS NOT t.content_sha256)
        ORDER BY a.id
        LIMIT ?
    """
    total = 0
    last_id = 0
    while True:
        rows = conn.execute(stale_sql, (last_id, SYNC_BATCH)).fetchall()
        if not rows:
            break
        last_id = rows[-1][0]
        for article_id, url, title, summary, text, sha in rows:
            conn.execute("DELETE FROM article_search WHERE rowid = ?", (article_id,))
            conn.execute(
                "INSERT INTO article_search(rowid, url, title, summary, body) VALUES (?, ?, ?, ?, ?)",
                (article_id, url, index_text(title), index_text(summary), index_text(text)),
            )
            conn.execute(
                "INSERT INTO article_search_state(article_id, title, summary, text_sha) "
                "VALUES (?, ?, ?, ?) ON CONFLICT(article_id) DO UPDATE SET "
                "title = excluded.title, summary = excluded.summary, text_sha = excluded.text_sha",
                (article_id, title, summary, sha),
            )
        conn.commit()
        total += len(rows)
    if total:
        logger.info("Search index updated: %d articles", total)
    return total


@dataclass(slots=True)
class SearchHit:
    url: str
    title: str
    source_name: str
    category: str
    published_at: str | None
    snippet: str
    has_text: bool


def _make_snippet(texts: list[str | None], terms: list[str], width: int = 70) -> str:
    """Window of original text around the first term found, term in 【】."""
    wanted = [to_traditional(t).lower() for t in terms if t.strip()]
    for text in texts:
        if not text:
            continue
        folded = to_traditional(text)
        shown = text if len(folded) == len(text) else folded
        lowered = folded.lower()
        for term in wanted:
            pos = lowered.find(term)
            if pos < 0:
                continue
            start = max(0, pos - 20)
            end = min(len(shown), max(start + width, pos + len(term)))
            return (
                ("…" if start else "")
                + shown[start:pos] + "【" + shown[pos:pos + len(term)] + "】"
                + shown[pos + len(term):end]
                + ("…" if end < len(shown) else "")
            )
    for text in texts[1:]:
        if text:
            return text[:width] + ("…" if len(text) > width else "")
    return ""


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    since: date | None = None,
    until: date | None = None,
    category: str | None = None,
    limit: int = 20,
    sync: bool = True,
) -> list[SearchHit]:
    """Best matches first (title hits weigh most); newest first on ties."""
    match = build_match(query)
    if match is None:
        return []
    if sync:
        sync_index(conn)
    where = ["article_search MATCH ?"]
    params: list = [match]
    when = "COALESCE(a.published_at, a.fetched_at)"
    if since:
        where.append(f"{when} >= ?")
        params.append(since.isoformat())
    if until:
        where.append(f"{when} < date(?, '+1 day')")
        params.append(until.isoformat())
    if category:
        where.append("a.category = ?")
        params.append(category)
    params.append(max(1, int(limit)))
    sql = f"""
        SELECT a.url, a.title, a.source_name, a.category, a.published_at,
               a.summary, t.text
        FROM article_search
        JOIN articles a ON a.id = article_search.rowid
        LEFT JOIN article_texts t ON t.url = a.url
        WHERE {' AND '.join(where)}
        ORDER BY {_BM25}, {when} DESC
        LIMIT ?
    """
    terms = query.split()
    return [
        SearchHit(
            url=row[0],
            title=row[1],
            source_name=row[2],
            category=row[3],
            published_at=row[4],
            snippet=_make_snippet([row[1], row[5], row[6]], terms),
            has_text=row[6] is not None,
        )
        for row in conn.execute(sql, params)
    ]


def _parse_date(value: str) -> date:
    return datetime.strptime(value, "%Y-%m-%d").date()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.search", description=__doc__.split("\n\n")[0])
    parser.add_argument("terms", nargs="*", help="search terms; all must match (Traditional or Simplified)")
    parser.add_argument("--since", type=_parse_date, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--until", type=_parse_date, help="YYYY-MM-DD, inclusive")
    parser.add_argument("--category", help="e.g. politics, military, poll, betting")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--rebuild", action="store_true", help="drop and rebuild the whole index")
    parser.add_argument("--db", help="news DB path (default: the configured news DB)")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    logging.basicConfig(level=logging.INFO, format="%(message)s")

    from .database import Database
    from .settings import get_settings

    with Database(args.db or get_settings().news_db_path) as db:
        try:
            indexed = sync_index(db.conn, rebuild=args.rebuild)
        except SearchUnavailable as exc:
            print(f"Search unavailable: {exc}")
            return 2
        if args.rebuild or not args.terms:
            print(f"Index up to date ({indexed} articles indexed this run).")
            if not args.terms:
                return 0
        hits = search(
            db.conn, " ".join(args.terms), since=args.since, until=args.until,
            category=args.category, limit=args.limit, sync=False,
        )
    if not hits:
        print("No matches.")
        return 0
    for n, hit in enumerate(hits, 1):
        day = (hit.published_at or "")[:10] or "????-??-??"
        marker = "" if hit.has_text else "  [no full text]"
        print(f"{n:>3}. {day}  {hit.source_name}  {hit.title}{marker}")
        print(f"     {hit.url}")
        if hit.snippet:
            print(f"     {hit.snippet}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
