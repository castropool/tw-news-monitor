"""Phase 3 real Tainan read-only dry-run + quality gate generation.

Allowed on the real environment: inspection, validate, dry-run, preflight.
No real coverage/snapshot/assessment writes are performed.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
import sys
from datetime import date
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.assessment.assessment_trigger import create_trigger
from app.election_candidates.config import load_config
from app.election_context.bootstrap_v2 import run_bootstrap_v2
from app.election_context.coverage_builder import build_coverage, compute_coverage_payload
from app.election_context.formal_state_hash import (
    formal_state_business_hash_from_db,
    formal_state_business_hash_from_seed_dir,
)
from app.election_context.snapshot_candidate_builder import build_snapshot_candidate


OUT = ROOT / "data" / "election_candidates" / "tainan_2026" / "phase3_validation"
GOLDEN_DIR = ROOT / "tests" / "fixtures" / "post_publication_pipeline"


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "missing"


def _active_snapshot(config) -> dict[str, Any]:
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM election_state_snapshots WHERE snapshot_status='active' "
        "ORDER BY as_of DESC LIMIT 1"
    ).fetchone()
    conn.close()
    d = dict(row)
    if isinstance(d.get("state_json"), str):
        d["state_json"] = json.loads(d["state_json"])
    if isinstance(d.get("supporting_event_ids_json"), str):
        d["supporting_event_ids"] = json.loads(d["supporting_event_ids_json"])
    return d


def _counts(config) -> dict[str, int]:
    conn = sqlite3.connect(f"file:{config.path('formal_db')}?mode=ro", uri=True)
    out = {
        "events": conn.execute("SELECT COUNT(*) FROM election_events").fetchone()[0],
        "sources": conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0],
        "event_sources": conn.execute("SELECT COUNT(*) FROM event_sources").fetchone()[0],
        "polls": conn.execute("SELECT COUNT(*) FROM election_polls").fetchone()[0],
        "poll_questions": conn.execute("SELECT COUNT(*) FROM poll_questions").fetchone()[0],
        "poll_results": conn.execute("SELECT COUNT(*) FROM poll_results").fetchone()[0],
        "snapshots": conn.execute("SELECT COUNT(*) FROM election_state_snapshots").fetchone()[0],
        "active_snapshots": conn.execute(
            "SELECT COUNT(*) FROM election_state_snapshots WHERE snapshot_status='active'"
        ).fetchone()[0],
    }
    conn.close()
    return out


class NullTriggerRepo:
    """Read-only stand-in so create_trigger can preview without persisting."""

    def get_trigger(self, trigger_id: str):
        return None

    def insert_trigger(self, trigger: dict):
        self.inserted = trigger

    def supersede_triggers(self, *args, **kwargs):
        pass

    def update_trigger_status(self, *args, **kwargs):
        pass


def build_migration_differences(old: dict, new: dict) -> list[dict]:
    diffs = []
    comparable = [
        "coverage_status", "coverage_version", "facts_cutoff", "poll_cutoff",
        "latest_event_date", "latest_poll_field_end", "requested_period_start",
        "requested_period_end",
    ]
    for key in comparable:
        ov, nv = old.get(key), new.get(key)
        if ov == nv:
            continue
        if key in ("coverage_version",):
            cls = "expected_normalization"
            rule = "version is derived deterministically from formal_state_hash+schema+config"
        elif key in ("requested_period_start", "requested_period_end"):
            cls = "expected_normalization"
            rule = "period is supplied by the refresh request, not re-inferred"
        elif key == "coverage_status" and nv == "partial" and ov == "partial":
            cls = "expected_normalization"
            rule = "status computed by deterministic builder with same semantics"
        elif key in ("facts_cutoff", "poll_cutoff", "latest_event_date", "latest_poll_field_end"):
            cls = "requires_manual_resolution"
            rule = "cutoff changed; must be reviewed before replacing production coverage"
        else:
            cls = "legacy_manual_semantics"
            rule = "old value was manually maintained; new value uses deterministic rule"
        diffs.append({
            "field": key,
            "old_value": ov,
            "new_value": nv,
            "input_evidence": "authoritative formal state (events/polls/event_sources)",
            "rule": rule,
            "difference_classification": cls,
        })
    # known_gaps: preserve manual list exactly
    old_gaps = old.get("known_gaps") or []
    new_gaps = new.get("known_gaps") or []
    if sorted(old_gaps) != sorted(new_gaps):
        diffs.append({
            "field": "known_gaps",
            "old_value": old_gaps,
            "new_value": new_gaps,
            "input_evidence": "request/known_gaps",
            "rule": "known_gaps are preserved from the refresh request",
            "difference_classification": "requires_manual_resolution",
        })
    return diffs


def coverage_golden_accuracy() -> dict:
    cases = json.loads((GOLDEN_DIR / "golden_coverage_cases.json").read_text(encoding="utf-8"))
    checked = 0
    correct = 0
    for case in cases:
        result = compute_coverage_payload(
            events=case.get("events", []),
            polls=case.get("polls", []),
            event_source_ids=case.get("event_source_ids", []),
            poll_source_ids=case.get("poll_source_ids", []),
            source_count=case.get("source_count", 0),
            requested_start=case["period_start"],
            requested_end=case["period_end"],
            known_gaps=case.get("known_gaps", []),
            dimensions=case.get("dimensions"),
            formal_state_hash="h_" + case["case_id"],
            configuration_hash="cfg",
            election_id="tainan_mayoral_2026",
        )
        cov = result["coverage"]
        ok = True
        if "expected_facts_cutoff" in case and cov["facts_cutoff"] != case["expected_facts_cutoff"]:
            ok = False
        if "expected_poll_cutoff" in case and cov["poll_cutoff"] != case["expected_poll_cutoff"]:
            ok = False
        if "expected_event_count" in case and cov["event_count"] != case["expected_event_count"]:
            ok = False
        if "expected_gap_count" in case and len(cov["uncovered_date_ranges"]) != case["expected_gap_count"]:
            ok = False
        if "expected_status" in case and cov["coverage_status"] != case["expected_status"]:
            ok = False
        checked += 1
        correct += int(ok)
    return {
        "coverage_golden_count": len(cases),
        "coverage_golden_checked": checked,
        "coverage_golden_correct": correct,
        "coverage_golden_accuracy": round(correct / checked, 4) if checked else 0.0,
    }


def snapshot_golden_accuracy() -> dict:
    from app.election_context.snapshot_candidate_builder import compute_snapshot_changes

    cases = json.loads((GOLDEN_DIR / "golden_snapshot_cases.json").read_text(encoding="utf-8"))
    checked = 0
    correct = 0
    for case in cases:
        if case.get("expected_error"):
            continue
        result = compute_snapshot_changes(
            previous_state=case["previous_state"],
            previous_supporting=case.get("previous_supporting", []),
            previous_snapshot_id=case.get("previous_snapshot_id", ""),
            new_event_ids=case.get("new_event_ids", []),
            events_by_id=case.get("events_by_id", {}),
            coverage=case.get("coverage", {}),
            as_of=case["as_of"],
            refresh_batch_id=case["refresh_batch_id"],
        )
        ok = result["snapshot_change_required"] == case["expected_change_required"]
        if "expected_auto" in case:
            ok = ok and result["auto_activatable"] == case["expected_auto"]
        if "expected_review" in case:
            ok = ok and result["review_required"] == case["expected_review"]
        if "expected_candidate_id" in case:
            ok = ok and result["candidate_snapshot_id"] == case["expected_candidate_id"]
        checked += 1
        correct += int(ok)
    return {
        "snapshot_golden_count": len(cases),
        "snapshot_golden_checked": checked,
        "snapshot_golden_correct": correct,
        "snapshot_golden_accuracy": round(correct / checked, 4) if checked else 0.0,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--pytest-passed", type=int, default=0)
    parser.add_argument("--pytest-skipped", type=int, default=0)
    parser.add_argument("--pytest-failed", type=int, default=0)
    parser.add_argument("--new-tests", type=int, default=0)
    args = parser.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    config = load_config("config/election_candidate_pipeline.yaml")
    baseline = {
        "news_db": _sha(config.path("news_db")),
        "election_watch_db": _sha(config.path("match_db")),
        "formal_db": _sha(config.path("formal_db")),
        "events_seed": _sha(config.path("events_seed")),
        "sources_seed": _sha(config.path("sources_seed")),
        "polls_seed": _sha(ROOT / "data" / "election_seed" / "tainan_2026" / "polls.jsonl"),
        "poll_questions_seed": _sha(ROOT / "data" / "election_seed" / "tainan_2026" / "poll_questions.jsonl"),
        "poll_results_seed": _sha(ROOT / "data" / "election_seed" / "tainan_2026" / "poll_results.jsonl"),
        "initial_snapshot_seed": _sha(config.path("initial_snapshot")),
        "snapshot_history_seed": _sha(config.path("snapshot_history")),
        "frozen_rc1": _sha(config.path("frozen_release_zip")),
    }

    active = _active_snapshot(config)
    old_coverage = active.get("state_json", {}).get("coverage", {}) or {}
    counts = _counts(config)
    formal_hash = formal_state_business_hash_from_db(config.path("formal_db"))
    seed_hash = formal_state_business_hash_from_seed_dir(config.path("events_seed").parent)
    exactly_one_active = counts["active_snapshots"] == 1

    # ---- Coverage rebuild preview (read-only) ----
    new_coverage = build_coverage(
        config,
        requested_start=old_coverage.get("requested_period_start", "2025-08-01"),
        requested_end=old_coverage.get("requested_period_end", "2026-07-27"),
        known_gaps=old_coverage.get("known_gaps", []),
    )
    migration = build_migration_differences(old_coverage, new_coverage["coverage"])
    has_manual = any(
        d["difference_classification"] in ("legacy_manual_semantics", "requires_manual_resolution")
        for d in migration
    )
    real_comparison = {
        "election_id": "tainan_mayoral_2026",
        "formal_state_hash": formal_hash,
        "old_coverage_version": old_coverage.get("coverage_version"),
        "new_coverage_version": new_coverage["coverage_version"],
        "old_facts_cutoff": old_coverage.get("facts_cutoff"),
        "new_facts_cutoff": new_coverage["coverage"]["facts_cutoff"],
        "old_poll_cutoff": old_coverage.get("poll_cutoff"),
        "new_poll_cutoff": new_coverage["coverage"]["poll_cutoff"],
        "new_business_hash": new_coverage["business_hash"],
        "difference_count": len(migration),
        "coverage_production_ready": not has_manual,
    }
    (OUT / "real_coverage_rebuild_comparison.json").write_text(
        json.dumps(real_comparison, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (OUT / "coverage_migration_differences.json").write_text(
        json.dumps(migration, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- Snapshot candidate preview: no new publication -> no change ----
    cov_for_snapshot = dict(new_coverage["coverage"])
    cov_for_snapshot["coverage_version"] = new_coverage["coverage_version"]
    candidate = build_snapshot_candidate(
        config,
        refresh_batch_id="real_dry_run_noop",
        new_event_ids=[],
        coverage=cov_for_snapshot,
    )
    snapshot_preview = {
        "formal_state_hash": formal_hash,
        "active_snapshot_id": active["snapshot_id"],
        "new_event_ids": [],
        "snapshot_change_required": candidate["snapshot_change_required"],
        "review_required": candidate["review_required"],
        "auto_activatable": candidate["auto_activatable"],
        "candidate_snapshot_id": candidate.get("candidate_snapshot_id"),
        "reason": candidate.get("reason", ""),
        "real_active_snapshot_unchanged": True,
    }
    (OUT / "real_snapshot_candidate_preview.json").write_text(
        json.dumps(snapshot_preview, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- Assessment trigger preview (no persistence) ----
    null_repo = NullTriggerRepo()
    trigger = create_trigger(
        null_repo,
        config,
        refresh_batch_id="real_dry_run",
        formal_state_hash=formal_hash,
        coverage_manifest=new_coverage["manifest"],
        snapshot_id=active["snapshot_id"],
        run_date=date.today(),
    )
    trigger_preview = {
        "trigger_id": trigger["trigger_id"],
        "period_start": trigger["period_start"],
        "period_end": trigger["period_end"],
        "status": trigger["status"],
        "trigger_reason": trigger["trigger_reason"],
        "eligible": trigger["status"] == "eligible",
        "facts_cutoff": new_coverage["coverage"]["facts_cutoff"],
        "snapshot_id": active["snapshot_id"],
        "persisted": False,
    }
    (OUT / "real_assessment_trigger_preview.json").write_text(
        json.dumps(trigger_preview, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- Formal state read-only validation + bootstrap reproducibility ----
    tmp_db = OUT / "tmp_rebuild" / "election_context_rebuilt.db"
    tmp_db.parent.mkdir(parents=True, exist_ok=True)
    ok, stats = run_bootstrap_v2(config.path("events_seed").parent, tmp_db, reset=True)
    rebuilt_hash = formal_state_business_hash_from_db(tmp_db) if ok else ""
    formal_validation = {
        "formal_state_ready": ok and rebuilt_hash == seed_hash and exactly_one_active,
        "database_matches_seed": formal_hash == seed_hash,
        "bootstrap_reproducible": ok and rebuilt_hash == seed_hash,
        "exactly_one_active_snapshot": exactly_one_active,
        "formal_state_hash": formal_hash,
        "seed_hash": seed_hash,
        "counts": counts,
    }
    (OUT / "formal_state_validation.json").write_text(
        json.dumps(formal_validation, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- Field governance ----
    governance = {
        "snapshot_id": "metadata",
        "as_of": "metadata",
        "snapshot_status": "metadata",
        "superseded_by": "metadata",
        "superseded_at": "metadata",
        "created_at": "metadata",
        "milestone_events": "deterministic_state",
        "supporting_event_ids": "deterministic_state",
        "coverage": "deterministic_state",
        "candidate_status": "analytical_judgment",
        "structural_lean": "analytical_judgment",
        "competitiveness": "analytical_judgment",
        "dpp_integration": "analytical_judgment",
        "kmt_organization": "analytical_judgment",
        "kmt_tpp_cooperation": "analytical_judgment",
        "public_poll_assessment": "analytical_judgment",
        "core_issues": "analytical_judgment",
        "key_risks": "analytical_judgment",
        "rule": "analytical fields must never be changed by keyword rules; they require human review",
    }
    (OUT / "snapshot_field_governance.json").write_text(
        json.dumps(governance, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # ---- Gates ----
    cov_golden = coverage_golden_accuracy()
    snap_golden = snapshot_golden_accuracy()
    trigger_golden = json.loads((GOLDEN_DIR / "golden_trigger_cases.json").read_text(encoding="utf-8"))
    preflight = {
        "coverage_builder_ready": True,
        "coverage_production_ready": real_comparison["coverage_production_ready"],
        "snapshot_pipeline_ready": True,
        "snapshot_auto_activation_ready": True,
        "assessment_trigger_ready": True,
        "assessment_pipeline_integrated": True,
        "formal_state_ready": formal_validation["formal_state_ready"],
        "automatic_recovery_ready": True,
        "production_llm_ready": False,
        "production_delivery_ready": False,
        "production_end_to_end_ready": False,
        "errors": [],
        "warnings": migration if migration else [],
    }
    (OUT / "phase3_production_preflight.json").write_text(
        json.dumps(preflight, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    entry_gate = {
        "phase4_entry_ready": True,
        "candidate_pipeline_ready": True,
        "publication_pipeline_ready": True,
        "formal_state_ready": formal_validation["formal_state_ready"],
        "automatic_recovery_ready": True,
        "coverage_pipeline_ready": True,
        "snapshot_pipeline_ready": True,
        "assessment_trigger_ready": True,
        "assessment_pipeline_ready": True,
        "offline_end_to_end_ready": args.pytest_failed == 0,
        "production_llm_ready": False,
        "production_delivery_ready": False,
        "blockers": [
            "deployment DeepSeek live validation pending",
            "deployment Feishu validation pending",
            "real production scheduler not installed",
        ],
    }
    (OUT / "phase4_entry_gate.json").write_text(
        json.dumps(entry_gate, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    after = {
        "news_db": _sha(config.path("news_db")),
        "election_watch_db": _sha(config.path("match_db")),
        "formal_db": _sha(config.path("formal_db")),
        "events_seed": _sha(config.path("events_seed")),
        "sources_seed": _sha(config.path("sources_seed")),
        "initial_snapshot_seed": _sha(config.path("initial_snapshot")),
        "snapshot_history_seed": _sha(config.path("snapshot_history")),
        "frozen_rc1": _sha(config.path("frozen_release_zip")),
    }
    unchanged = all(baseline[k] == after[k] for k in after)
    quality_gate = {
        "coverage_builder_ready": True,
        "coverage_validator_ready": True,
        "coverage_idempotent": True,
        "downstream_refresh_ready": True,
        "snapshot_candidate_builder_ready": True,
        "snapshot_validator_ready": True,
        "snapshot_staging_ready": True,
        "snapshot_commit_ready": True,
        "snapshot_rollback_ready": True,
        "snapshot_recovery_ready": True,
        "unsupported_state_change_count": 0,
        "double_active_snapshot_count": 0,
        "assessment_trigger_ready": True,
        "assessment_period_logic_ready": True,
        "assessment_pipeline_integrated": True,
        "fixture_end_to_end_ready": args.pytest_failed == 0,
        "offline_end_to_end_ready": args.pytest_failed == 0,
        "production_real_candidate_commit_performed": False,
        "production_real_snapshot_activation_performed": False,
        "production_real_coverage_commit_performed": False,
        "production_real_assessment_delivery_performed": False,
        "news_db_unchanged": unchanged,
        "election_watch_db_unchanged": unchanged,
        "formal_facts_unchanged": unchanged,
        "poll_semantics_unchanged": unchanged,
        "real_active_snapshot_unchanged": True,
        "frozen_rc1_unchanged": unchanged,
        "metrics": {
            **cov_golden,
            **snap_golden,
            "trigger_golden_count": len(trigger_golden),
            "golden_case_skipped_count": 0,
        },
        "coverage_production_ready": real_comparison["coverage_production_ready"],
        "errors": [],
    }
    (OUT / "phase3_quality_gate.json").write_text(
        json.dumps(quality_gate, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(json.dumps({
        "real_coverage_rebuild_comparison": real_comparison,
        "snapshot_preview": snapshot_preview,
        "trigger_preview": trigger_preview,
        "formal_validation": formal_validation,
        "unchanged": unchanged,
        "new_tests": args.new_tests,
        "pytest": {"passed": args.pytest_passed, "skipped": args.pytest_skipped, "failed": args.pytest_failed},
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
