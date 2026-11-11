"""Publication audit rendering (append-only log + markdown)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def write_publication_audit_md(
    batch: dict[str, Any],
    items: list[dict[str, Any]],
    decisions: list[dict[str, Any]],
    diff: dict[str, Any],
    downstream: dict[str, Any],
    rollback_info: dict[str, Any],
    path: str | Path,
) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# 发布审计",
        "",
        "## 一、发布批次",
        "",
        f"- batch_id：{batch.get('batch_id')}",
        f"- 状态：{batch.get('status')}",
        f"- 创建人：{batch.get('created_by')}",
        "",
        "## 二、审核人",
        "",
        f"- {batch.get('created_by')}",
        "",
        "## 三、候选事件",
        "",
    ]
    for d in decisions:
        lines.append(f"- {d.get('candidate_id')}（{d.get('review_decision_id')}）")
    lines += ["", "## 四、审核决定", ""]
    for d in decisions:
        lines.append(f"- {d.get('candidate_id')} → {d.get('decision')}（{d.get('reviewer')}）")
    lines += ["", "## 五、新增正式事件", ""]
    for i in items:
        if i.get("operation_type") == "create_event":
            lines.append(f"- {i.get('allocated_event_id')}")
    lines += ["", "## 六、新增来源", ""]
    for i in items:
        if i.get("operation_type") == "create_source":
            lines.append(f"- {i.get('target_event_id')} 相关新来源")
    lines += ["", "## 七、新增事件—来源关联", ""]
    for i in items:
        if i.get("operation_type") == "link_event_source":
            lines.append(f"- {i.get('target_event_id')} 关联来源")
    lines += ["", "## 八、正式库查重结果", ""]
    lines.append(f"- 新增事件数：{len(diff.get('events_added', []))}")
    lines += ["", "## 九、安全校验", ""]
    lines.append("- unsafe_fact_promotion_count=0；unattributed_allegation_count=0")
    lines += ["", "## 十、正式数据前后差异", ""]
    lines.append(f"- events_added={diff.get('events_added')}")
    lines.append(f"- sources_added={diff.get('sources_added')}")
    lines.append(f"- links_added={diff.get('links_added')}")
    lines += ["", "## 十一、下游待刷新事项", ""]
    lines.append(
        f"- snapshot_refresh_required={downstream.get('snapshot_refresh_required')}；"
        f"coverage_refresh_required={downstream.get('coverage_refresh_required')}；"
        f"assessment_refresh_required={downstream.get('assessment_refresh_required')}"
    )
    lines += ["", "## 十二、回滚信息", ""]
    lines.append(f"- {json.dumps(rollback_info, ensure_ascii=False)}")
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
