"""Backfill durable news topics without recollecting or delivering news."""

from __future__ import annotations

import argparse
import json
import sqlite3
import tempfile
import shutil
from datetime import datetime
from pathlib import Path

import yaml

from .database import Database
from .election2026.classifier import classify_articles as classify_election_articles
from .election2026.config import load_election_config, load_entities
from .settings import get_settings, load_environment


def _load_sources(path: Path) -> dict[str, dict]:
    with path.open(encoding="utf-8-sig") as stream:
        payload = yaml.safe_load(stream) or {}
    rows = payload.get("sources") if isinstance(payload, dict) else []
    return {
        row["id"]: row
        for row in rows
        if isinstance(row, dict) and row.get("id")
    }


def _parse_datetime(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value)


def _existing_topic_keys(db: Database, rows: list[dict]) -> set[tuple[str, str]]:
    """Return persisted (url, topic) keys for the given candidate rows."""
    urls = list(dict.fromkeys(str(row.get("url", "")) for row in rows))
    if not urls:
        return set()
    placeholders = ",".join("?" for _ in urls)
    found = db.conn.execute(
        f"SELECT url, topic FROM news_topics WHERE url IN ({placeholders})",
        urls,
    ).fetchall()
    return {(str(row[0]), str(row[1])) for row in found}


def backfill_topics(
    db: Database,
    *,
    sources: dict[str, dict],
    election_config: dict | None,
    election_entities: dict | None,
    batch_size: int = 500,
    since: datetime | None = None,
    until: datetime | None = None,
    dry_run: bool = False,
    refresh_existing: bool = False,
) -> dict[str, int]:
    """Backfill missing durable topics.

    Default semantics are insert-only: existing (url, topic) annotations are
    never overwritten.  Passing ``refresh_existing=True`` enables an explicit
    upsert mode for operators who intentionally want to reclassify history.
    """
    scanned = 0
    election_rows = 0
    military_rows = 0
    matched = 0
    inserted = 0
    skipped_existing = 0
    updated = 0
    written = 0
    batch: list = []

    def flush(rows: list[dict]) -> None:
        nonlocal matched, inserted, skipped_existing, updated, written
        if not rows:
            return
        matched += len(rows)
        existing = _existing_topic_keys(db, rows)
        missing: list[dict] = []
        for row in rows:
            key = (str(row.get("url", "")), str(row.get("topic", "election_2026_local")))
            if key in existing:
                skipped_existing += 1
            else:
                missing.append(row)
        inserted += len(missing)
        if dry_run:
            return
        if refresh_existing:
            # Explicit operator request: allow updating existing annotations.
            written += db.save_topics(rows)
            updated += len(rows)
        else:
            written += db.insert_missing_topics(missing)

    def add_rows_for_batch(articles_batch: list) -> None:
        nonlocal election_rows, military_rows
        annotations = classify_election_articles(
            articles_batch,
            election_config or {"enabled": False},
            election_entities or {},
        )
        rows = [
            {
                "url": url,
                "topic": "election_2026_local",
                "scope": annotation.scope,
                "region": annotation.region,
                "regions": annotation.regions,
                "event_type": annotation.event_type,
                "confidence": annotation.confidence,
                "metadata": {
                    "classifier_version": "election2026-v1",
                    "event_tags": list(getattr(annotation, "event_tags", ()) or ()),
                    "backfill": True,
                },
            }
            for url, annotation in annotations.items()
        ]
        election_rows += len(rows)
        for article_item in articles_batch:
            source = sources.get(article_item.source_id, {})
            if source.get("topic") == "military" and source.get("military_source_type"):
                rows.append({
                    "url": article_item.url,
                    "topic": "military",
                    "source_type": source["military_source_type"],
                    "metadata": {"backfill": True},
                })
                military_rows += 1
        flush(rows)

    for article in db.iter_articles(
        batch_size=max(1, min(batch_size, 5000)), since=since, until=until
    ):
        scanned += 1
        batch.append(article)
        if len(batch) >= batch_size:
            add_rows_for_batch(batch)
            batch.clear()

    if batch:
        add_rows_for_batch(batch)

    return {
        "scanned": scanned,
        "matched": matched,
        "inserted": inserted,
        "skipped_existing": skipped_existing,
        "updated": updated,
        "written": written,
        "dry_run": int(dry_run),
        "election_topics": election_rows,
        "military_topics": military_rows,
    }


def _safe_copy_for_dry_run(db_path: Path) -> Path:
    """Copy a SQLite DB into a temporary path without mutating the original."""
    tmp_dir = tempfile.mkdtemp(prefix="tw-topic-backfill-dry-")
    tmp_path = Path(tmp_dir) / "news.db"
    try:
        src = sqlite3.connect(f"file:{db_path.resolve()}?mode=ro", uri=True, timeout=5)
        try:
            dst = sqlite3.connect(str(tmp_path))
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
    except Exception:
        shutil.rmtree(tmp_dir, ignore_errors=True)
        raise
    return tmp_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Backfill news topics only")
    parser.add_argument("--db", default=None, help="database path override")
    parser.add_argument("--sources-config", default=None)
    parser.add_argument("--election-config", default=None)
    parser.add_argument("--entities-config", default=None)
    parser.add_argument("--batch-size", type=int, default=500)
    parser.add_argument("--since", default=None, help="fetched_at ISO lower bound")
    parser.add_argument("--until", default=None, help="fetched_at ISO upper bound")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--refresh-existing",
        action="store_true",
        help="explicitly update existing topic annotations (default: insert-only)",
    )
    args = parser.parse_args()

    root = load_environment(Path(__file__).resolve().parent.parent)
    settings = get_settings(root, load_env=False)
    db_path = Path(args.db) if args.db else settings.news_db_path
    if not db_path.is_absolute():
        db_path = root / db_path
    source_path = Path(args.sources_config) if args.sources_config else settings.sources_config_path
    if not source_path.is_absolute():
        source_path = root / source_path
    election_path = Path(args.election_config) if args.election_config else root / "config/election_2026.yaml"
    entity_path = Path(args.entities_config) if args.entities_config else root / "config/election_2026_entities.yaml"
    if not election_path.is_absolute():
        election_path = root / election_path
    if not entity_path.is_absolute():
        entity_path = root / entity_path

    # dry-run must never migrate or write the original DB.  Copy it safely,
    # run migration + full backfill on the disposable copy, then remove it.
    if args.dry_run:
        tmp_path = _safe_copy_for_dry_run(db_path)
        db = Database(tmp_path)
        try:
            db.connect()
            result = backfill_topics(
                db,
                sources=_load_sources(source_path),
                election_config=load_election_config(election_path),
                election_entities=load_entities(entity_path),
                batch_size=args.batch_size,
                since=_parse_datetime(args.since),
                until=_parse_datetime(args.until),
                refresh_existing=args.refresh_existing,
                dry_run=False,
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
        finally:
            db.close()
            shutil.rmtree(tmp_path.parent, ignore_errors=True)
        return

    db = Database(db_path)
    db.connect()
    try:
        result = backfill_topics(
            db,
            sources=_load_sources(source_path),
            election_config=load_election_config(election_path),
            election_entities=load_entities(entity_path),
            batch_size=args.batch_size,
            since=_parse_datetime(args.since),
            until=_parse_datetime(args.until),
            refresh_existing=args.refresh_existing,
            dry_run=False,
        )
        print(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        db.close()


if __name__ == "__main__":
    main()
