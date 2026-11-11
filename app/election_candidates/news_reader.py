"""Read-only reader for the news database."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .candidate_models import NormalizedArticle


def open_news_connection(db_path: str | Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info('{table}')").fetchall()]


def read_articles(
    conn: sqlite3.Connection,
    *,
    table: str = "articles",
    id_column: str = "id",
    date_column: str = "published_at",
    date_from: str = "",
    date_to: str = "",
    id_after: int = 0,
) -> list[dict[str, Any]]:
    cols = table_columns(conn, table)
    if id_column not in cols:
        raise ValueError(f"id column {id_column!r} not present in {table}: {cols}")
    if date_column not in cols:
        raise ValueError(f"date column {date_column!r} not present in {table}: {cols}")
    clauses: list[str] = [f"{id_column} > ?"]
    params: list[Any] = [id_after]
    if date_from:
        clauses.append(f"{date_column} >= ?")
        params.append(date_from)
    if date_to:
        if len(date_to) == 10:
            date_to = f"{date_to}T23:59:59.999999"
        clauses.append(f"{date_column} <= ?")
        params.append(date_to)
    sql = f"SELECT * FROM {table} WHERE {' AND '.join(clauses)} ORDER BY {id_column} ASC"
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]


def article_rows_to_models(
    rows: list[dict[str, Any]],
    config,
    match_by_id: dict[str, Any] | None = None,
) -> list[NormalizedArticle]:
    from .article_normalizer import normalize_article

    match_by_id = match_by_id or {}
    result: list[NormalizedArticle] = []
    for row in rows:
        article_id = str(row.get(config.get("news_reader.id_column", "id"), ""))
        if not article_id:
            continue
        art = normalize_article(row, config)
        art.news_article_id = article_id
        if article_id in match_by_id:
            art.match = match_by_id[article_id]
        result.append(art)
    return result


def business_signature(conn: sqlite3.Connection, table: str = "articles") -> str:
    cols = table_columns(conn, table)
    base = [
        c for c in (
            "id", "source_id", "source_name", "category", "title", "url",
            "published_at", "fetched_at", "position",
        )
        if c in cols
    ]
    optional = [
        c for c in ("summary", "summary_source", "collected_at")
        if c in cols
    ]
    if not base:
        raise ValueError(f"no known columns in {table}: {cols}")
    select = ",".join(base + optional)
    rows = conn.execute(f"SELECT {select} FROM {table} ORDER BY id").fetchall()
    payload = json.dumps([list(r) for r in rows], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
