from app.assessment.research_task_reconciliation import (
    filter_active_research_tasks,
    reconcile_research_tasks,
)
from app.assessment.evidence_pack_builder import build_do_not_infer, FormalData
from pathlib import Path


def _backlog():
    return [
        {"research_task_id": "RT01", "research_status": "completed", "active_status": "completed", "research_priority": "P0"},
        {"research_task_id": "RT02", "research_status": "completed", "research_priority": "P0"},
        {"research_task_id": "RT03", "research_status": "completed", "research_priority": "P0"},
        {"research_task_id": "RT04", "research_status": "completed", "research_priority": "P0"},
        {"research_task_id": "RT05", "research_priority": "P1", "coverage_status": "missing"},
        {"research_task_id": "RT06", "research_priority": "P1", "coverage_status": "partial"},
        {"research_task_id": "RT07", "research_priority": "P1", "coverage_status": "missing"},
        {"research_task_id": "RT08", "research_priority": "P2", "coverage_status": "partial"},
    ]


class TestResearchTaskReconciliation:
    def test_completed_not_in_active(self):
        r = reconcile_research_tasks(_backlog(), {"active_p0_count": 0, "active_p1_count": 3})
        assert "RT01" not in r["authoritative_active_task_ids"]
        assert "RT01" in r["completed_task_ids_from_backlog"]

    def test_active_from_authoritative_backlog(self):
        r = reconcile_research_tasks(_backlog(), {"active_p0_count": 0, "active_p1_count": 3})
        assert r["authoritative_active_task_ids"] == ["RT05", "RT06", "RT07"]
        assert r["reconciliation_ready"] is True

    def test_active_count_matches_array(self):
        r = reconcile_research_tasks(_backlog(), {"active_p0_count": 0, "active_p1_count": 3})
        tasks = filter_active_research_tasks(_backlog(), r["authoritative_active_task_ids"])
        assert r["authoritative_active_task_count"] == len(tasks) == 3

    def test_backlog_validation_conflict_fails(self):
        r = reconcile_research_tasks(_backlog(), {"active_p0_count": 0, "active_p1_count": 5})
        assert r["reconciliation_ready"] is False
        assert r["status_conflicts"]

    def test_completed_do_not_assume_kept_in_do_not_infer(self):
        formal = FormalData(
            election_id="e",
            events={},
            sources={},
            links=set(),
            polls=[],
            snapshots=[],
            fts_count=0,
            counts={},
            active_snapshot={"snapshot_id": "a", "state": {}},
            previous_snapshot=None,
            snapshot_selection_basis="",
            coverage_dir=Path("."),
            coverage_name="v",
            coverage_preflight={},
            coverage_validation={},
            gap_reconciliation=[],
            research_backlog=[
                {"research_task_id": "RT01", "research_status": "completed", "do_not_assume": ["民调不存在不等于稳定"]}
            ],
            closure_record=None,
            blocker_triage={},
            theme_matrix=[],
            blocked_ids=set(),
        )
        assert "RT01: 民调不存在不等于稳定" in build_do_not_infer(formal, {})

    def test_soft_limitation_not_auto_active_task(self):
        r = reconcile_research_tasks(
            _backlog(),
            {"active_p0_count": 0, "active_p1_count": 3},
        )
        # soft limitation 只来自 blocker_triage，不自动创建 backlog 任务
        assert r["authoritative_active_task_ids"] == ["RT05", "RT06", "RT07"]

