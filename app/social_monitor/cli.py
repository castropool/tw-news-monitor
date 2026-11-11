"""CLI for the political social monitor."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ..database import Database
from ..settings import get_settings, load_environment
from .collector import SocialCollector
from .crosspost import build_crossposts
from .registry import DEFAULT_CONFIG_PATH, load_social_config, sync_registry
from .repository import SocialRepository
from .source_bridge import bridge_all
from .validation import validate_config_and_database


def _open_db() -> Database:
    root = load_environment(Path(__file__).resolve().parents[2])
    settings = get_settings(root, load_env=False)
    db = Database(settings.news_db_path)
    db.connect()
    return db


def _cmd_sync(args) -> int:
    db = _open_db()
    try:
        config = load_social_config(args.config or DEFAULT_CONFIG_PATH)
        result = sync_registry(db, config)
        print(json.dumps({"synced": result}, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_collect(args) -> int:
    db = _open_db()
    try:
        if args.config:
            sync_registry(db, load_social_config(args.config))
        collector = SocialCollector(db)
        result = collector.collect(
            platform=args.platform,
            person_id=args.person_id,
            account_id=args.account_id,
            since=datetime.fromisoformat(args.since.replace("Z", "+00:00")) if args.since else None,
            dry_run=args.dry_run,
        )
        if not args.dry_run:
            result["crossposts"] = build_crossposts(SocialRepository(db))
            result["source_bridge"] = bridge_all(SocialRepository(db))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_validate(args) -> int:
    db = _open_db()
    try:
        config = load_social_config(args.config or DEFAULT_CONFIG_PATH)
        result = validate_config_and_database(config, SocialRepository(db))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["valid"] else 1
    finally:
        db.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Political Social Media Monitor")
    parser.add_argument("--config", default=None)
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser("sync-registry")
    sync_parser.set_defaults(func=_cmd_sync)

    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--platform")
    collect_parser.add_argument("--person-id")
    collect_parser.add_argument("--account-id")
    collect_parser.add_argument("--since")
    collect_parser.add_argument("--dry-run", action="store_true")
    collect_parser.set_defaults(func=_cmd_collect)

    validate_parser = sub.add_parser("validate")
    validate_parser.set_defaults(func=_cmd_validate)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
