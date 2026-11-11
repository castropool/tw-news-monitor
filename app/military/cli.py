"""Read-only event CLI: python -m app.military.cli --hours 24."""
import argparse
from datetime import datetime, timedelta
import json
import logging
import math
from pathlib import Path
import sqlite3
import sys

from ..models import Article
from ..time_utils import TAIPEI, normalize_published_at
from .clustering import article_time
from .config import DEFAULT_RULES_PATH, load_rules
from .events import build_events
from .output import markdown

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]


def read_articles(path, hours, now=None):
    if not math.isfinite(hours) or hours <= 0:
        raise ValueError("hours must be finite and positive")
    now = normalize_published_at(now or datetime.now(TAIPEI), assumed_timezone=TAIPEI)
    since = now - timedelta(hours=hours)
    conn = sqlite3.connect(Path(path).resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    try:
        conn.execute("PRAGMA query_only=ON")
        conn.row_factory = sqlite3.Row
        columns = {row[1] for row in conn.execute("PRAGMA table_info(articles)")}
        required = {"id", "source_id", "source_name", "category", "title", "url", "published_at", "fetched_at", "position"}
        if not required <= columns:
            raise ValueError("news.db articles schema is missing required columns")
        fields = sorted(required | ({"summary"} & columns))
        articles, ids = [], {}
        eligible_only = "delivery_eligible" in columns
        sql = "SELECT " + ",".join(fields) + " FROM articles"
        if eligible_only:
            sql += " WHERE delivery_eligible = 1"
        sql += " ORDER BY id"
        # Parse timestamps in Python: production contains both naive and offset ISO
        # strings, for which SQLite string comparison gives incorrect window edges.
        for row in conn.execute(sql):
            try:
                published = datetime.fromisoformat(row["published_at"]) if row["published_at"] else None
                fetched = datetime.fromisoformat(row["fetched_at"])
                article = Article(row["source_id"], row["source_name"], row["category"], row["title"], row["url"],
                                  published, fetched, row["position"], row["summary"] if "summary" in columns else None)
                timestamp = article_time(article)
            except (ValueError, TypeError, OverflowError):
                logger.warning("Skipping article id=%s: invalid timestamp", row["id"])
                continue
            if since <= timestamp <= now:
                articles.append(article)
                ids[article.url] = row["id"]
        return articles, ids
    finally:
        conn.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description="只读 news.db，生成涉台军武事件简报（不调用网络或 LLM）")
    parser.add_argument("--hours", type=float, default=24)
    parser.add_argument("--db", type=Path, default=None)
    parser.add_argument("--config", type=Path, default=DEFAULT_RULES_PATH)
    parser.add_argument("--format", choices=("json", "markdown"), default="json")
    parser.add_argument("--output", type=Path, help="不指定则输出到终端")
    parser.add_argument("--now", help="ISO 时间，供离线回放；无时区按台北时间")
    parser.add_argument("--word-dir", type=Path, help="同时生成包含事件栏目的现有 Word 简版")
    args = parser.parse_args(argv)
    try:
        if args.db is None:
            from ..settings import get_settings, load_environment
            load_environment(ROOT)
            args.db = get_settings(ROOT, load_env=False).news_db_path
        if args.output:
            target = args.output.resolve()
            protected = {args.db.resolve(), args.config.resolve(), DEFAULT_RULES_PATH.resolve()}
            if target in protected or target.suffix.lower() in {".db", ".sqlite", ".sqlite3", ".yaml", ".yml", ".docx"}:
                raise ValueError("output must not overwrite a database, rule file or Word file")
        now = normalize_published_at(datetime.fromisoformat(args.now) if args.now else datetime.now(TAIPEI), assumed_timezone=TAIPEI)
        rules = load_rules(args.config)
        articles, ids = read_articles(args.db, args.hours, now)
        events = build_events(articles, rules, ids)
        payload = {"schema_version": 1, "generated_at": now.isoformat(), "hours": args.hours,
                   "input_articles": len(articles), "military_articles": sum(e["article_count"] for e in events),
                   "events": events}
        if args.word_dir and articles:
            from ..word_digest import build_word_digest
            word_rules = dict(rules, word_enabled=True)
            payload["word_path"] = str(build_word_digest(articles, args.word_dir, generated_at=now,
                                                         military_event_config=word_rules))
        text = json.dumps(payload, ensure_ascii=False, indent=2) if args.format == "json" else markdown(events)
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(text + "\n", encoding="utf-8")
        else:
            if hasattr(sys.stdout, "reconfigure"):
                sys.stdout.reconfigure(encoding="utf-8")
            print(text)
        return 0
    except (OSError, ValueError, sqlite3.Error, OverflowError) as exc:
        print(f"military: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
