"""Incremental collection orchestration for political social accounts."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..database import Database
from .adapters import ADAPTERS
from .models import SocialPost, utc_now_iso
from .normalize import normalize_social_text
from .registry import collectable_accounts
from .repository import SocialRepository

logger = logging.getLogger(__name__)


def _raw_snapshot(root: Path, platform: str, account_id: str, raw: dict[str, Any],
                  *, persist: bool = True) -> tuple[str, str]:
    now = datetime.now(timezone.utc)
    payload = json.dumps(raw, ensure_ascii=False, sort_keys=True, default=str)
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
    target_dir = root / now.strftime("%Y/%m/%d") / platform / account_id
    path = target_dir / f"{digest[:16]}.json"
    if persist:
        target_dir.mkdir(parents=True, exist_ok=True)
        path.write_text(payload, encoding="utf-8")
    return digest, path.as_posix()


class SocialCollector:
    def __init__(
        self,
        db: Database,
        *,
        raw_root: str | Path = "data/social_monitor/raw",
        adapters: dict[str, Any] | None = None,
        client_factory: Any = None,
    ):
        self.db = db
        self.repo = SocialRepository(db)
        self.raw_root = Path(raw_root)
        self.adapters = adapters or ADAPTERS
        self.client_factory = client_factory

    def collect(
        self,
        *,
        platform: str | None = None,
        person_id: str | None = None,
        account_id: str | None = None,
        since: datetime | None = None,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        run_id = f"social_run_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S%f')}"
        if not dry_run:
            self.repo.start_run(run_id)
        summary = {
            "run_id": run_id, "accounts_attempted": 0, "accounts_successful": 0,
            "accounts_failed": 0, "posts_seen": 0, "posts_new": 0,
            "posts_updated": 0, "posts_deleted": 0, "crossposts_created": 0,
            "errors": [], "warnings": [],
        }
        accounts = collectable_accounts(
            self.repo, platform=platform, person_id=person_id, account_id=account_id
        )
        for account in accounts:
            summary["accounts_attempted"] += 1
            adapter_cls = self.adapters.get(account["platform"])
            if adapter_cls is None:
                summary["accounts_failed"] += 1
                summary["warnings"].append(f"{account['account_id']}: platform adapter not available")
                continue
            try:
                client = self.client_factory(account) if self.client_factory else None
                adapter = adapter_cls(account, client=client) if client is not None else adapter_cls(account)
                posts = adapter.fetch_recent_posts(since=since)
                latest_id = None
                latest_published = None
                for raw in posts:
                    summary["posts_seen"] += 1
                    normalized = adapter.normalize_post(raw)
                    if not normalized.get("platform_post_id"):
                        continue
                    if raw.get("deleted") is True:
                        existing = self.repo.get_post(normalized["platform"], normalized["platform_post_id"])
                        if existing and not dry_run:
                            self.repo.mark_deleted(existing["post_id"], deleted=True)
                            summary["posts_deleted"] += 1
                        continue
                    raw_hash, raw_path = _raw_snapshot(
                        self.raw_root, normalized["platform"], account["account_id"], raw,
                        persist=not dry_run,
                    )
                    post = SocialPost(
                        post_id="",
                        platform=normalized["platform"],
                        platform_post_id=str(normalized["platform_post_id"]),
                        account_id=account["account_id"],
                        person_id=account["person_id"],
                        published_at=normalized.get("published_at"),
                        text=normalized.get("text"),
                        normalized_text=normalized.get("normalized_text") or normalize_social_text(normalized.get("text")),
                        title=normalized.get("title"),
                        canonical_url=normalized.get("canonical_url"),
                        thumbnail_url=normalized.get("thumbnail_url"),
                        raw_payload_hash=raw_hash,
                        raw_snapshot_path=raw_path,
                    )
                    if dry_run:
                        existing = self.repo.get_post(post.platform, post.platform_post_id)
                        summary["posts_updated" if existing else "posts_new"] += 1
                    else:
                        _post_id, status = self.repo.upsert_post(post)
                        if status == "new":
                            summary["posts_new"] += 1
                        elif status == "updated":
                            summary["posts_updated"] += 1
                    if latest_id is None:
                        latest_id = post.platform_post_id
                    if latest_published is None:
                        latest_published = post.published_at
                if not dry_run:
                    self.repo.update_account_cursor(
                        account["account_id"],
                        last_post_id=latest_id,
                        last_published_at=latest_published,
                        success=True,
                    )
                summary["accounts_successful"] += 1
            except Exception as exc:  # noqa: BLE001 - one account must not stop others
                summary["accounts_failed"] += 1
                code = getattr(exc, "code", type(exc).__name__)
                summary["errors"].append(f"{account['account_id']}: {code}")
                logger.warning("Social account failed %s: %s", account["account_id"], code)
                if not dry_run:
                    self.repo.update_account_cursor(
                        account["account_id"], last_post_id=None,
                        last_published_at=None, success=False, error_code=str(code),
                    )
        if not dry_run:
            self.repo.finish_run(
                run_id,
                accounts_attempted=summary["accounts_attempted"],
                accounts_successful=summary["accounts_successful"],
                accounts_failed=summary["accounts_failed"],
                posts_seen=summary["posts_seen"],
                posts_new=summary["posts_new"],
                posts_updated=summary["posts_updated"],
                posts_deleted=summary["posts_deleted"],
                errors_json=json.dumps(summary["errors"], ensure_ascii=False),
                warnings_json=json.dumps(summary["warnings"], ensure_ascii=False),
            )
        return summary
