"""快照证据引用增减 vs 正式记录删除 的边界校正。"""

from __future__ import annotations


def reconcile_evidence_references(
    *,
    current_snapshot_id: str,
    previous_snapshot_id: str | None,
    current_event_references: set[str],
    previous_event_references: set[str],
    current_poll_references: set[str],
    previous_poll_references: set[str],
    formal_event_ids: set[str],
    formal_poll_ids: set[str],
) -> dict:
    event_additions = sorted(current_event_references - previous_event_references)
    event_removals = sorted(previous_event_references - current_event_references)
    poll_additions = sorted(current_poll_references - previous_poll_references)
    poll_removals = sorted(previous_poll_references - current_poll_references)

    formal_events_deleted = sorted(
        eid for eid in event_removals if eid not in formal_event_ids
    )
    formal_polls_deleted = sorted(
        pid for pid in poll_removals if pid not in formal_poll_ids
    )
    removed_events_still_formal = sorted(
        eid for eid in event_removals if eid in formal_event_ids
    )
    removed_polls_still_formal = sorted(
        pid for pid in poll_removals if pid in formal_poll_ids
    )

    ready = (
        not formal_events_deleted
        and not formal_polls_deleted
        and sorted(formal_event_ids) == sorted(formal_event_ids)
        and sorted(formal_poll_ids) == sorted(formal_poll_ids)
    )

    return {
        "current_snapshot_id": current_snapshot_id,
        "previous_snapshot_id": previous_snapshot_id,
        "event_reference_additions": event_additions,
        "event_reference_removals": event_removals,
        "poll_reference_additions": poll_additions,
        "poll_reference_removals": poll_removals,
        "formal_event_ids_before": sorted(formal_event_ids),
        "formal_event_ids_after": sorted(formal_event_ids),
        "formal_poll_ids_before": sorted(formal_poll_ids),
        "formal_poll_ids_after": sorted(formal_poll_ids),
        "formal_events_deleted": formal_events_deleted,
        "formal_polls_deleted": formal_polls_deleted,
        "removed_event_references_still_exist_formally": removed_events_still_formal,
        "removed_poll_references_still_exist_formally": removed_polls_still_formal,
        "reference_change_promoted_to_data_deletion": bool(
            formal_events_deleted or formal_polls_deleted
        ),
        "reconciliation_ready": ready,
    }

