"""研究任务状态对账（backlog / coverage_validation / blocker_triage）。"""

from __future__ import annotations

from typing import Any


ACTIVE_PRIORITIES = ("P0", "P1")


def _is_completed(task: dict) -> bool:
    return (
        task.get("research_status") == "completed"
        or task.get("active_status") == "completed"
    )


def _validation_active_ids(coverage_validation: dict) -> list[str]:
    out: list[str] = []
    for key, value in coverage_validation.items():
        if key.startswith("active_") and key.endswith("_task_ids"):
            if isinstance(value, list):
                out.extend(str(x) for x in value if x)
    return sorted(set(out))


def reconcile_research_tasks(
    backlog: list[dict],
    coverage_validation: dict,
    evidence_pack_before: dict | None = None,
) -> dict:
    """Reconcile research task status across authoritative files.

    Returns a reconciliation dict; ``reconciliation_ready`` is False when
    authoritative sources conflict or the backlog itself is inconsistent.
    """
    conflicts: list[str] = []
    task_ids = [t.get("research_task_id") for t in backlog if t.get("research_task_id")]
    if len(task_ids) != len(set(task_ids)):
        conflicts.append("backlog 中存在重复 research_task_id")

    completed_ids: list[str] = []
    open_tasks: list[dict] = []
    for task in backlog:
        tid = task.get("research_task_id")
        if not tid:
            conflicts.append("backlog 中存在缺少 research_task_id 的任务")
            continue
        status = task.get("research_status")
        active_status = task.get("active_status")
        if status == "completed" and active_status not in (None, "", "completed"):
            conflicts.append(
                f"task {tid}: research_status=completed 但 active_status={active_status!r}"
            )
        if _is_completed(task):
            completed_ids.append(tid)
        else:
            open_tasks.append(task)

    active_p0 = [
        t.get("research_task_id")
        for t in open_tasks
        if t.get("research_priority") == "P0"
    ]
    active_p1 = [
        t.get("research_task_id")
        for t in open_tasks
        if t.get("research_priority") == "P1"
    ]
    authoritative = [
        t.get("research_task_id")
        for t in open_tasks
        if t.get("research_priority") in ACTIVE_PRIORITIES
    ]
    authoritative.sort(key=lambda x: (0 if x in active_p0 else 1, x))

    validation_p0 = coverage_validation.get("active_p0_count")
    validation_p1 = coverage_validation.get("active_p1_count")
    if isinstance(validation_p0, int) and len(active_p0) != validation_p0:
        conflicts.append(
            f"backlog active P0 数量 {len(active_p0)} 与 coverage_validation "
            f"active_p0_count={validation_p0} 不一致"
        )
    if isinstance(validation_p1, int) and len(active_p1) != validation_p1:
        conflicts.append(
            f"backlog active P1 数量 {len(active_p1)} 与 coverage_validation "
            f"active_p1_count={validation_p1} 不一致"
        )

    pack_before_ids = []
    if evidence_pack_before:
        pack_before_ids = [
            t.get("research_task_id")
            for t in (evidence_pack_before.get("active_research_tasks") or [])
            if t.get("research_task_id")
        ]

    return {
        "coverage_version": coverage_validation.get("coverage_version")
        or coverage_validation.get("coverage_version"),
        "backlog_task_count": len(backlog),
        "active_task_ids_from_backlog": sorted(t.get("research_task_id") for t in open_tasks),
        "completed_task_ids_from_backlog": sorted(completed_ids),
        "active_p0_task_ids": sorted(active_p0),
        "active_p1_task_ids": sorted(active_p1),
        "active_task_ids_from_validation": _validation_active_ids(coverage_validation),
        "active_task_ids_from_evidence_pack_before": sorted(pack_before_ids),
        "status_conflicts": conflicts,
        "authoritative_active_task_ids": authoritative,
        "authoritative_active_task_count": len(authoritative),
        "lower_priority_open_task_ids": sorted(
            t.get("research_task_id")
            for t in open_tasks
            if t.get("research_priority") not in ACTIVE_PRIORITIES
        ),
        "reconciliation_ready": not conflicts,
    }


def filter_active_research_tasks(
    backlog: list[dict], authoritative_ids: list[str]
) -> list[dict]:
    """Return only authoritative active tasks, preserving backlog details."""
    by_id = {t.get("research_task_id"): t for t in backlog}
    out = []
    for tid in authoritative_ids:
        task = by_id.get(tid)
        if not task:
            continue
        out.append(
            {
                "research_task_id": task.get("research_task_id"),
                "title": task.get("title"),
                "research_priority": task.get("research_priority"),
                "coverage_status": task.get("coverage_status"),
                "time_range": task.get("time_range"),
                "missing_facts": task.get("missing_facts") or [],
                "research_questions": task.get("research_questions") or [],
                "do_not_assume": task.get("do_not_assume") or [],
                "affected_snapshot_fields": task.get("affected_snapshot_fields") or [],
            }
        )
    priority = {"P0": 0, "P1": 1}
    out.sort(key=lambda x: (priority.get(x.get("research_priority"), 9), x.get("research_task_id") or ""))
    return out

