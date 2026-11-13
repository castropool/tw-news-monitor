"""Social collection and delivery helpers used by the regular news run.

``python -m app.main`` calls :func:`collect_due_for_news_run` before building
the digest so verified KOL accounts are refreshed on their tier schedule, and
:func:`kol_delivery_items` to select recent, relevant KOL posts for Word and
the Feishu text digest.  Every function here fails safe: social problems are
logged and never block news delivery.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import yaml

from ..database import Database
from .collector import SocialCollector
from .crosspost import build_crossposts, canonical_posts
from .registry import collectable_accounts, load_registry_with_verifications, sync_registry
from .relevance import select_word_posts
from .repository import SocialRepository
from .scheduler import due_accounts

logger = logging.getLogger(__name__)

DEFAULT_MONITOR_CONFIG = Path(__file__).resolve().parents[2] / "config" / "social_monitor.yaml"
DEFAULTS: dict[str, Any] = {
    "enabled": True,
    "news_run": {"enabled": True, "categories": ["kol"], "max_accounts_per_run": 30},
    "kol_delivery": {
        "word_enabled": True,
        "feishu_text_enabled": True,
        "window_hours": 24,
        "max_items": 30,
        "feishu_max_items": 10,
    },
    "raw": {"root": "data/social_monitor/raw"},
}


def load_monitor_config(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else DEFAULT_MONITOR_CONFIG
    try:
        payload = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("social_monitor config unavailable (%s); using defaults", exc)
        payload = {}
    if not isinstance(payload, dict):
        payload = {}
    merged = {key: (dict(value) if isinstance(value, dict) else value) for key, value in DEFAULTS.items()}
    for key, value in payload.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key].update(value)
        else:
            merged[key] = value
    return merged


def collect_due_for_news_run(
    db: Database,
    *,
    config: dict[str, Any] | None = None,
    registry: dict[str, Any] | None = None,
    now: datetime | None = None,
    project_root: Path | None = None,
    collector: SocialCollector | None = None,
) -> dict[str, Any]:
    """Collect due, verified accounts of the configured person categories."""
    cfg = config or load_monitor_config()
    run_cfg = cfg.get("news_run") or {}
    if not cfg.get("enabled", True) or not run_cfg.get("enabled", False):
        return {"status": "disabled"}
    try:
        sync_registry(db, registry if registry is not None else load_registry_with_verifications())
        repo = SocialRepository(db)
        now = now or datetime.now(timezone.utc)
        selected: list[dict[str, Any]] = []
        for category in run_cfg.get("categories") or []:
            accounts = collectable_accounts(repo, person_category=str(category))
            selected.extend(due_accounts(accounts, now, cfg))
        limit = max(0, int(run_cfg.get("max_accounts_per_run") or 0))
        if limit:
            selected.sort(key=lambda a: (a.get("last_checked_at") or "", a["account_id"]))
            selected = selected[:limit]
        if not selected:
            return {"status": "ok", "accounts_attempted": 0, "posts_new": 0}
        raw_root = Path(str((cfg.get("raw") or {}).get("root") or "data/social_monitor/raw"))
        if not raw_root.is_absolute() and project_root is not None:
            raw_root = project_root / raw_root
        active = collector or SocialCollector(db, raw_root=raw_root)
        summary = active.collect(account_ids=[a["account_id"] for a in selected])
        build_crossposts(repo)
        logger.info(
            "Social news-run collection: attempted=%d ok=%d failed=%d new=%d",
            summary.get("accounts_attempted", 0), summary.get("accounts_successful", 0),
            summary.get("accounts_failed", 0), summary.get("posts_new", 0),
        )
        summary["status"] = "ok"
        return summary
    except Exception as exc:  # noqa: BLE001 - social collection must never block news
        logger.warning("Social news-run collection failed safely: %s", exc)
        return {"status": "error", "error": str(exc)}


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


def social_items(db: Database) -> list[dict[str, Any]]:
    """All canonical posts enriched with person display data and category."""
    repo = SocialRepository(db)
    persons = {p["person_id"]: p for p in repo.list_persons()}
    items = []
    for post in canonical_posts(repo):
        person = persons.get(post.get("person_id")) or {}
        item = dict(post)
        item["person_display_name"] = person.get("display_name") or person.get("canonical_name")
        item["person_canonical_name"] = person.get("canonical_name")
        item["person_category"] = person.get("person_category") or "political"
        items.append(item)
    return items


def kol_delivery_items(
    items: list[dict[str, Any]],
    *,
    config: dict[str, Any] | None = None,
    now: datetime | None = None,
    for_feishu: bool = False,
) -> list[dict[str, Any]]:
    """Recent, relevance-gated KOL posts, newest first."""
    cfg = (config or load_monitor_config()).get("kol_delivery") or {}
    enabled_key = "feishu_text_enabled" if for_feishu else "word_enabled"
    if not cfg.get(enabled_key, True):
        return []
    now = now or datetime.now(timezone.utc)
    window = timedelta(hours=float(cfg.get("window_hours") or 24))
    recent = []
    for item in items:
        if item.get("person_category") != "kol":
            continue
        published = _parse_time(item.get("published_at"))
        if published is None or published > now + timedelta(minutes=10) or now - published > window:
            continue
        recent.append(item)
    selected = select_word_posts(recent)
    selected.sort(key=lambda i: (str(i.get("published_at") or ""), i.get("post_id") or ""), reverse=True)
    limit = int(cfg.get("feishu_max_items" if for_feishu else "max_items") or 0)
    return selected[:limit] if limit else selected
