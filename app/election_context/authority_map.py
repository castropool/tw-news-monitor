"""Formal state authority map (seed governance)."""

AUTHORITY_MAP = {
    "elections": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "actors": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "sources": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "events": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "event_sources": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "fts": {"authority": "derived_rebuildable", "rebuildable": True, "in_business_hash": False, "in_backup": False, "in_publication_diff": False},
    "polls": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "poll_questions": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "poll_results": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "poll_sources": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "poll_source_links": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "snapshots": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "snapshot_history": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "analysis_json": {"authority": "seed_authoritative", "rebuildable": True, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "coverage_state": {"authority": "derived_rebuildable", "rebuildable": False, "in_business_hash": True, "in_backup": True, "in_publication_diff": True},
    "system_metadata": {"authority": "runtime_state", "rebuildable": True, "in_business_hash": False, "in_backup": False, "in_publication_diff": False},
}


def unknown_authority_count() -> int:
    return sum(1 for v in AUTHORITY_MAP.values() if v.get("authority") == "unknown")
