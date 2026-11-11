"""Formal data diff before/after publication."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _index(rows: list[dict], key: str) -> dict[str, dict]:
    return {r[key]: r for r in rows if r.get(key)}


def diff_rows(before: list[dict], after: list[dict], key: str) -> dict[str, list]:
    b = _index(before, key)
    a = _index(after, key)
    added = [k for k in a if k not in b]
    removed = [k for k in b if k not in a]
    modified = [
        k for k in a
        if k in b and json.dumps(b[k], ensure_ascii=False, sort_keys=True)
        != json.dumps(a[k], ensure_ascii=False, sort_keys=True)
    ]
    return {"added": sorted(added), "removed": sorted(removed), "modified": sorted(modified)}


def diff_links(before: list[dict], after: list[dict]) -> dict[str, list]:
    def pairs(rows):
        return {f"{r['event_id']}|{r['source_id']}" for r in rows}

    b = pairs(before)
    a = pairs(after)
    return {
        "added": sorted(a - b),
        "removed": sorted(b - a),
    }


def write_formal_diff(before: dict[str, Any], after: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "events_added": before["events_diff"]["added"],
        "events_removed": before["events_diff"]["removed"],
        "events_modified": before["events_diff"]["modified"],
        "sources_added": before["sources_diff"]["added"],
        "sources_removed": before["sources_diff"]["removed"],
        "sources_modified": before["sources_diff"]["modified"],
        "links_added": before["links_diff"]["added"],
        "links_removed": before["links_diff"]["removed"],
        "snapshot_changed": before["snapshot_changed"],
        "coverage_changed": before["coverage_changed"],
        "poll_changed": before["poll_changed"],
    }
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
