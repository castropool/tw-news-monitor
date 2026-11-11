"""证据包语义迁移核对：报告 Schema 1.0 -> 1.1，业务证据语义必须不变。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .evidence_pack_builder import canonical_hash


SEMANTIC_KEYS = (
    "period_events",
    "background_events",
    "sources",
    "polls",
    "coverage_gaps",
    "known_limitations",
    "do_not_infer",
    "state_diff",
)


def build_migration_report(pack: dict, baseline: dict) -> dict:
    current = {key: canonical_hash(pack.get(key)) for key in SEMANTIC_KEYS}
    previous = baseline.get("semantic_key_hashes") or {}
    diffs = [key for key in SEMANTIC_KEYS if current.get(key) != previous.get(key)]
    business_unchanged = not diffs
    claim_semantics_unchanged = (
        canonical_hash((pack.get("generation_eligibility") or {}).get("required_disclosures") or [])
        == canonical_hash(
            (json.loads(baseline.get("data_status_json") or "{}") or {}).get("required_disclosures") or []
        )
        if "data_status_json" in baseline
        else True
    )
    return {
        "previous_schema_version": "1.0",
        "current_schema_version": "1.1",
        "data_context_added": True,
        "business_evidence_unchanged": business_unchanged,
        "claim_semantics_unchanged": claim_semantics_unchanged,
        "migration_ready": business_unchanged and claim_semantics_unchanged,
        "semantic_key_hashes_current": current,
        "changed_semantic_keys": diffs,
        "checked_at": __import__("datetime").datetime.now().isoformat(),
    }


def write_migration_report(root: Path, pack: dict, baseline: dict) -> Path:
    out = (
        root
        / "data"
        / "reports"
        / "tainan_2026"
        / "deployment_validation"
        / "evidence_package_semantic_migration.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(build_migration_report(pack, baseline), ensure_ascii=False, indent=2)
        + "\n",
        encoding="utf-8",
    )
    return out
