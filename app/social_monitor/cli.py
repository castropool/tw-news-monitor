"""CLI for the social monitor (political persons and KOLs)."""

from __future__ import annotations

import argparse
import json
from datetime import datetime
from pathlib import Path

from ..database import Database
from ..settings import get_settings, load_environment
from .collector import SocialCollector
from .crosspost import build_crossposts
from .registry import (
    load_registry_with_verifications,
    load_social_config,
    load_verification_store,
    save_verification_store,
    sync_registry,
)
from .repository import SocialRepository
from .source_bridge import bridge_all
from .validation import validate_config_and_database
from .verify import verify_account


def _open_db() -> Database:
    root = load_environment(Path(__file__).resolve().parents[2])
    settings = get_settings(root, load_env=False)
    db = Database(settings.news_db_path)
    db.connect()
    return db


def _registry(args) -> dict:
    """``--config`` selects one registry file; default is political + KOL."""
    if args.config:
        return load_social_config(args.config)
    return load_registry_with_verifications(store_path=getattr(args, "store", None))


def _cmd_sync(args) -> int:
    db = _open_db()
    try:
        result = sync_registry(db, _registry(args))
        print(json.dumps({"synced": result}, ensure_ascii=False, indent=2))
        return 0
    finally:
        db.close()


def _cmd_collect(args) -> int:
    db = _open_db()
    try:
        sync_registry(db, _registry(args))
        collector = SocialCollector(db)
        result = collector.collect(
            platform=args.platform,
            person_id=args.person_id,
            account_id=args.account_id,
            since=datetime.fromisoformat(args.since.replace("Z", "+00:00")) if args.since else None,
            dry_run=args.dry_run,
            person_category=args.category,
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
        result = validate_config_and_database(_registry(args), SocialRepository(db))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["valid"] else 1
    finally:
        db.close()


def _iter_accounts(config: dict, *, category: str | None, account_id: str | None):
    for person in config.get("persons") or []:
        if category and person.get("person_category", "political") != category:
            continue
        for account in person.get("accounts") or []:
            if account_id and account.get("account_id") != account_id:
                continue
            yield person, account


def _cmd_verify(args, *, client=None) -> int:
    config = load_registry_with_verifications(store_path=args.store)
    if args.url and not args.account_id:
        print("--url requires --account-id")
        return 2
    targets = list(_iter_accounts(config, category=args.category, account_id=args.account_id))
    if args.account_id and not targets:
        print(f"account not found: {args.account_id}")
        return 2
    if not args.account_id:
        targets = [
            (p, a) for p, a in targets
            if a.get("verification_status") not in ("verified", "rejected")
        ]
    store = load_verification_store(args.store)
    results = []
    for person, account in targets:
        record = verify_account(person, account, url=args.url, client=client)
        results.append(record)
        if record["status"] == "verified" or args.account_id:
            store.setdefault("accounts", {})[str(account["account_id"])] = record
    if not args.dry_run and results:
        save_verification_store(store, args.store)
    summary = {
        "checked": len(results),
        "verified": sum(1 for r in results if r["status"] == "verified"),
        "failed": [
            {"account_id": r["account_id"], "reason": r.get("reason")}
            for r in results if r["status"] != "verified"
        ],
        "written": bool(results) and not args.dry_run,
    }
    print(json.dumps({"summary": summary, "results": results}, ensure_ascii=False, indent=2))
    return 0 if all(r["status"] == "verified" for r in results) else 1


def _cmd_list(args) -> int:
    config = load_registry_with_verifications(store_path=args.store)
    rows = []
    for person, account in _iter_accounts(config, category=args.category, account_id=None):
        rows.append({
            "person_id": person.get("person_id"),
            "name": person.get("display_name") or person.get("canonical_name"),
            "category": person.get("person_category", "political"),
            "account_id": account.get("account_id"),
            "platform": account.get("platform"),
            "verification_status": account.get("verification_status", "unverified"),
            "identifier": account.get("canonical_url") or account.get("feed_url") or account.get("handle"),
        })
    print(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Social Media Monitor (political persons and KOLs)")
    parser.add_argument("--config", default=None, help="sync/collect/validate one registry file only")
    parser.add_argument("--store", default=None, help="verification store path (default data/social_monitor/account_verifications.json)")
    sub = parser.add_subparsers(dest="command", required=True)

    sync_parser = sub.add_parser("sync-registry")
    sync_parser.set_defaults(func=_cmd_sync)

    collect_parser = sub.add_parser("collect")
    collect_parser.add_argument("--platform")
    collect_parser.add_argument("--person-id")
    collect_parser.add_argument("--account-id")
    collect_parser.add_argument("--category", choices=["political", "kol"])
    collect_parser.add_argument("--since")
    collect_parser.add_argument("--dry-run", action="store_true")
    collect_parser.set_defaults(func=_cmd_collect)

    validate_parser = sub.add_parser("validate")
    validate_parser.set_defaults(func=_cmd_validate)

    verify_parser = sub.add_parser(
        "verify", help="prove candidate account identifiers on a networked machine"
    )
    verify_parser.add_argument("--account-id", help="verify one account (default: all unverified)")
    verify_parser.add_argument("--url", help="channel/profile or feed URL to verify for --account-id")
    verify_parser.add_argument("--category", choices=["political", "kol"])
    verify_parser.add_argument("--dry-run", action="store_true", help="do not write the verification store")
    verify_parser.set_defaults(func=_cmd_verify)

    list_parser = sub.add_parser("list", help="list registry accounts with verification status")
    list_parser.add_argument("--category", choices=["political", "kol"])
    list_parser.set_defaults(func=_cmd_list)
    return parser


def main(argv=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
