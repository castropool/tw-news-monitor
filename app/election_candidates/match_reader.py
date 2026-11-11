"""Read election_watch match results (read-only) or reproduce them in memory.

The persisted mode reads article_matches from election_watch.db.  The
inline_classifier mode reuses app.election_classifier.ElectionClassifier so the
candidate pipeline does not reimplement a conflicting election filter; it never
writes to article_matches.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from .candidate_models import MatchInfo
from .news_reader import open_news_connection


def open_match_connection(db_path: str | Path) -> sqlite3.Connection:
    return sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)


def read_persisted_matches(
    match_conn: sqlite3.Connection,
    news_conn: sqlite3.Connection,
    config,
) -> dict[str, MatchInfo]:
    table = config.get("match_reader.table", "article_matches")
    city_values = config.get("match_reader.city_values", ["tainan"])
    placeholders = ",".join("?" for _ in city_values)
    rows = match_conn.execute(
        f"SELECT article_url, city, relevance, matched_people, matched_parties, "
        f"matched_issues, matched_basis, processed_at "
        f"FROM {table} WHERE city IN ({placeholders}) ORDER BY article_url",
        city_values,
    ).fetchall()

    article_url_to_id: dict[str, str] = {}
    for r in news_conn.execute(
        "SELECT id, url FROM articles WHERE url IS NOT NULL ORDER BY id"
    ).fetchall():
        article_url_to_id[r[1]] = str(r[0])

    result: dict[str, MatchInfo] = {}
    for row in rows:
        url = row[0]
        article_id = article_url_to_id.get(url, f"url:{url}")
        people = [p for p in (row[3] or "").split(",") if p]
        parties = [p for p in (row[4] or "").split(",") if p]
        issues = [p for p in (row[5] or "").split(",") if p]
        basis = [p for p in (row[6] or "").split(",") if p]
        relevance = row[2] or "low"
        result[article_id] = MatchInfo(
            city=row[1],
            relevance=relevance,
            matched_people=people,
            matched_parties=parties,
            matched_issues=issues,
            matched_terms=list(dict.fromkeys(people + parties + issues)),
            matched_basis=basis,
            match_rule_id="article_matches",
            region_match="region_match" in basis,
            election_context_match="election_context" in basis,
            match_score=_relevance_to_score(relevance),
        )
    return result


def _relevance_to_score(relevance: str) -> float:
    return {"high": 1.0, "medium": 0.65, "low": 0.35}.get(relevance, 0.2)


def inline_classify(articles, config, city: str | None = None) -> dict[str, MatchInfo]:
    """Reproduce election_watch matching in memory using the same classifier.

    city 缺省时取 config ``match_reader.city_values`` 的第一个值（台南默认）。
    """
    from app.election_classifier import ElectionClassifier

    cities = config.get("match_reader.city_values") or ["tainan"]
    if city is None:
        city = str(cities[0])
    elif city not in cities:
        # 显式指定了非配置城市时仍尊重显式值（调用方负责一致性）
        pass
    classifier = ElectionClassifier(config.path("election_watch_config"))
    result: dict[str, MatchInfo] = {}
    for art in articles:
        matches = classifier.classify_article(
            art.raw_title, art.category, art.source_name
        )
        for m in matches:
            if m["city"] != city:
                continue
            people = list(m.get("matched_people", []))
            parties = list(m.get("matched_parties", []))
            issues = list(m.get("matched_issues", []))
            basis = list(m.get("matched_basis", []))
            result[art.news_article_id] = MatchInfo(
                city=city,
                relevance=m.get("relevance", "low"),
                matched_people=people,
                matched_parties=parties,
                matched_issues=issues,
                matched_terms=list(m.get("matched_terms", [])),
                matched_basis=basis,
                match_rule_id="inline_election_classifier",
                region_match="region_match" in basis,
                election_context_match="election_context" in basis,
                match_score=_relevance_to_score(m.get("relevance", "low")),
            )
            break
    return result


def read_matches(
    news_articles,
    config,
    match_db: str | Path | None = None,
    news_db: str | Path | None = None,
    mode: str | None = None,
) -> dict[str, MatchInfo]:
    mode = mode or config.get("match_reader.mode", "persisted")
    if mode == "inline_classifier":
        return inline_classify(news_articles, config)
    if mode != "persisted":
        raise ValueError(f"unknown match_reader.mode: {mode}")
    match_path = Path(match_db) if match_db else config.path("match_db")
    news_path = Path(news_db) if news_db else config.path("news_db")
    match_conn = open_match_connection(match_path)
    news_conn = open_news_connection(news_path)
    try:
        return read_persisted_matches(match_conn, news_conn, config)
    finally:
        match_conn.close()
        news_conn.close()


def matches_signature(match_conn: sqlite3.Connection, table: str = "article_matches") -> str:
    try:
        rows = match_conn.execute(
            f"SELECT article_url, city, relevance, matched_people, matched_parties, "
            f"matched_issues, matched_basis, processed_at, in_fact_base "
            f"FROM {table} ORDER BY article_url"
        ).fetchall()
    except sqlite3.OperationalError:
        rows = []
    payload = json.dumps([list(r) for r in rows], ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()
