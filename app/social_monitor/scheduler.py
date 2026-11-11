"""Tier-based scheduling policy (config driven; no sleep in tests)."""

from __future__ import annotations

from typing import Any

DEFAULT_INTERVALS_MINUTES = {1: 15, 2: 30, 3: 60, 4: 60}


def interval_minutes(tier: int | None, config: dict[str, Any] | None = None) -> int:
    cfg = (config or {}).get("scheduling", {}) if isinstance(config, dict) else {}
    per_tier = cfg.get("tier_intervals_minutes", {}) if isinstance(cfg, dict) else {}
    try:
        return int(per_tier.get(str(tier), per_tier.get(tier, DEFAULT_INTERVALS_MINUTES.get(int(tier or 3), 60))))
    except Exception:
        return DEFAULT_INTERVALS_MINUTES.get(3, 60)


def due_accounts(accounts: list[dict], now, config: dict[str, Any] | None = None) -> list[dict]:
    """Return accounts due for collection without sleeping."""
    from datetime import datetime, timezone

    due = []
    for account in accounts:
        last = account.get("last_checked_at")
        if not last:
            due.append(account)
            continue
        try:
            parsed = datetime.fromisoformat(str(last).replace("Z", "+00:00"))
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            tier = int(account.get("monitoring_priority") or 3)
            if (now - parsed).total_seconds() >= interval_minutes(tier, config) * 60:
                due.append(account)
        except Exception:
            due.append(account)
    return due
