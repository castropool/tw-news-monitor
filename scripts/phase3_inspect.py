"""Phase 3 read-only inspection of coverage/snapshot/assessment surfaces."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
OUT = ROOT / "data" / "election_candidates" / "tainan_2026" / "phase3_inspection"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect("file:data/election_context.db?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    active = dict(conn.execute(
        "SELECT state_json FROM election_state_snapshots WHERE snapshot_status='active'"
    ).fetchone())
    state = json.loads(active["state_json"]) if isinstance(active["state_json"], str) else active["state_json"]
    coverage = state.get("coverage", {})
    conn.close()

    coverage_schema = {
        "files": ["election_context.db -> election_state_snapshots.state_json.coverage",
                  "data/election_seed/tainan_2026/initial_snapshot.json（同结构）"],
        "schema": {k: "string" for k in coverage},
        "field_semantics": {
            "coverage_status": "覆盖状态（partial/full）",
            "coverage_version": "fact_coverage_<date>_v<n>",
            "requested_period_start/end": "报告周期",
            "facts_cutoff": "事实覆盖截止日期",
            "poll_cutoff": "民调覆盖截止日期",
            "latest_event_date": "最新正式事件日期",
            "latest_poll_field_end": "最新民调 fieldwork 结束日",
            "known_gaps": "人工/研究识别的空窗与限制",
        },
        "granularity": "周期级（半月至月），非逐事件",
        "manually_maintained_fields": ["known_gaps", "coverage_status", "requested_period_start", "requested_period_end"],
        "deterministic_fields": ["coverage_version", "facts_cutoff", "poll_cutoff", "latest_event_date", "latest_poll_field_end"],
    }
    (OUT / "coverage_schema_inventory.json").write_text(
        json.dumps(coverage_schema, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    dependency_map = {
        "coverage.depends_on": ["election_events.occurred_at", "event_sources", "election_polls.fieldwork_json", "poll_source_links"],
        "final_report_allowed_source": "app/assessment/generation_eligibility.py (facts_cutoff >= period_end)",
        "evidence_pack_uses": ["coverage_version", "facts_cutoff", "poll_cutoff", "known_gaps"],
        "snapshot.depends_on": ["coverage", "election_events", "election_polls"],
    }
    (OUT / "coverage_dependency_map.json").write_text(
        json.dumps(dependency_map, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    rebuild_assessment = {
        "coverage_builder_input": ["events", "event_sources", "polls", "poll_sources", "formal state"],
        "can_rebuild_deterministically": True,
        "non_deterministic_fields": ["known_gaps", "coverage_status"],
        "non_deterministic_policy": "known_gaps 保留人工/研究标注；coverage_status 由 builder 按缺口规则确定性计算",
        "production_ready": False,
        "reason": "需先与 fact_coverage_20260801_v4 对账并输出 migration differences",
    }
    (OUT / "coverage_rebuild_assessment.json").write_text(
        json.dumps(rebuild_assessment, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    snapshot_schema = {
        "schema": {
            "snapshot_id": "tn_state_<date>_v<n>",
            "as_of": "生效日期",
            "snapshot_status": "active/superseded",
            "state_json": {
                "coverage": "coverage dict",
                "candidate_status": "analytical",
                "structural_lean": "analytical",
                "competitiveness": "analytical",
                "dpp_integration": "analytical",
                "kmt_organization": "analytical",
                "kmt_tpp_cooperation": "analytical",
                "public_poll_assessment": "analytical",
                "core_issues": "analytical",
                "key_risks": "analytical",
                "milestone_events": "deterministic",
            },
            "supporting_event_ids": "deterministic",
            "superseded_by/superseded_at": "metadata",
        },
        "deterministic_fields": ["milestone_events", "supporting_event_ids", "coverage"],
        "analytical_fields": ["candidate_status", "structural_lean", "competitiveness", "dpp_integration",
                              "kmt_organization", "kmt_tpp_cooperation", "public_poll_assessment",
                              "core_issues", "key_risks"],
        "metadata_fields": ["snapshot_id", "as_of", "snapshot_status", "superseded_by", "superseded_at", "created_at"],
    }
    (OUT / "snapshot_schema_inventory.json").write_text(
        json.dumps(snapshot_schema, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    snapshot_dependency = {
        "snapshot.depends_on": ["previous active snapshot", "current formal state", "validated coverage"],
        "must_not_read": ["news.db", "candidate DB unpublished", "LLM output"],
        "transaction_boundary": "独立于事实发布事务",
    }
    (OUT / "snapshot_dependency_map.json").write_text(
        json.dumps(snapshot_dependency, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    trigger_assessment = {
        "period_helper": "app/assessment/reporting_period.py::scheduled_period_for",
        "generation_eligibility": "app/assessment/generation_eligibility.py::build_generation_eligibility",
        "run_days": [9, 22],
        "period_rules": {"9": "previous_month_16_to_end", "22": "current_month_01_to_15"},
        "production_llm_ready": False,
        "mock_only": True,
    }
    (OUT / "assessment_trigger_assessment.json").write_text(
        json.dumps(trigger_assessment, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    lines = [
        "# Phase 3 输入核查",
        "",
        f"- coverage 现有版本：{coverage.get('coverage_version')}",
        f"- facts_cutoff：{coverage.get('facts_cutoff')}；poll_cutoff：{coverage.get('poll_cutoff')}",
        f"- 周期规则：9日=previous_month_16_to_end；22日=current_month_01_to_15（复用 reporting_period）",
        "- coverage 可确定性重建字段：facts_cutoff/poll_cutoff/latest_event_date/latest_poll_field_end",
        "- 需人工/研究标注：known_gaps、coverage_status、周期范围",
    ]
    (OUT / "phase3_inspection_summary.md").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


if __name__ == "__main__":
    main()
