"""Formal event/source ID allocation that follows existing seed conventions."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any


def _slugify(text: str, max_len: int = 24) -> str:
    cleaned = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "_", text or "").strip("_").lower()
    return cleaned[:max_len].rstrip("_") or "event"


def _date_part(event_date: str) -> str:
    digits = re.sub(r"\D", "", event_date or "")[:8]
    return digits or "00000000"


def allocate_event_id(
    existing_ids: set[str],
    date: str,
    title: str,
    election_prefix: str = "tnn",
) -> str:
    base = f"evt_{election_prefix}_{_date_part(date)}_{_slugify(title)}"
    candidate = base
    n = 2
    while candidate in existing_ids:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def _source_prefix(domain: str, publisher: str) -> str:
    if domain:
        return re.sub(r"[^0-9a-z]", "", domain.split(".")[0])[:12] or "src"
    return re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]", "", publisher or "")[:12] or "src"


def allocate_source_id(
    existing_ids: set[str],
    domain: str,
    publisher: str,
    date: str = "",
) -> str:
    base = f"src_{_source_prefix(domain, publisher)}_{_date_part(date) or 'seed'}"
    candidate = base
    n = 2
    while candidate in existing_ids:
        candidate = f"{base}_{n}"
        n += 1
    return candidate


def collect_existing_ids(seed_events: list[dict], seed_sources: list[dict], db_ids: dict[str, set[str]]) -> dict[str, set[str]]:
    event_ids = {e.get("event_id") for e in seed_events if e.get("event_id")} | db_ids.get("events", set())
    source_ids = {s.get("source_id") for s in seed_sources if s.get("source_id")} | db_ids.get("sources", set())
    return {"events": event_ids, "sources": source_ids}


def write_id_allocation_manifest(
    manifest: dict[str, Any],
    path: str | Path,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def payload_hash(payload: Any) -> str:
    raw = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()
