"""Gap 稳定匹配与变化分类。"""

from __future__ import annotations

import re
from typing import Any


TRIAGE_TO_GAP = {
    "rt05_danas_typhoon": "gap_danas_typhoon",
    "rt06_sanye_budget": "gap_flood_governance",
    "rt07_feb_mar_gap": "rt07_feb_mar_gap",
}

RT_TO_GAP = {
    "RT05": "gap_danas_typhoon",
    "RT06": "gap_flood_governance",
    "RT07": "rt07_feb_mar_gap",
}

THEME_KEYWORDS = [
    ("gap_polling", ["民调", "民調", "追踪"]),
    ("gap_kmt_tpp", ["蓝白", "藍白", "在野整合", "禮讓", "礼让"]),
    ("gap_campaign_coverage", ["竞选活动", "競選活動", "竞选总部", "競選總部", "竞选", "競選", "指挥名单", "指揮名單", "组织扩张", "組織擴張"]),
    ("gap_flood_governance", ["治水", "淹水", "三爷溪", "三爺溪", "预算", "預算"]),
    ("gap_danas_typhoon", ["丹娜丝", "丹娜絲", "风灾", "風災"]),
    ("gap_dpp_joint_campaign", ["整合", "联合竞选", "聯合競選", "民进党", "民進黨"]),
    ("rt07_feb_mar_gap", ["2—3月", "2-3月", "断层", "斷層", "连续性", "連續性"]),
]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", text or "").lower()


def _match_by_rt(text: str) -> str | None:
    m = re.search(r"RT\s*(\d+)", text)
    if m:
        return RT_TO_GAP.get(f"RT{int(m.group(1)):02d}")
    return None


def _match_by_theme(text: str) -> str | None:
    for gap_id, keywords in THEME_KEYWORDS:
        if any(kw in text for kw in keywords):
            return gap_id
    return None


def match_stable_gap(text: str, aliases: dict[str, list[str]]) -> str | None:
    for gap_id, alias_list in aliases.items():
        for alias in alias_list:
            if alias and alias in text:
                return gap_id
    rt = _match_by_rt(text)
    if rt:
        return rt
    return _match_by_theme(text)


def build_gap_registry(
    gap_reconciliation: list[dict],
    backlog: list[dict],
    blocker_triage: dict,
) -> tuple[dict[str, dict], dict[str, list[str]]]:
    registry: dict[str, dict] = {}
    aliases: dict[str, list[str]] = {}
    for rec in gap_reconciliation:
        gid = rec.get("gap_id")
        if not gid:
            continue
        registry[gid] = {
            "stable_gap_id": gid,
            "previous_status": rec.get("previous_status"),
            "current_status": rec.get("current_status"),
            "formal_evidence_ids": rec.get("new_formal_evidence_ids") or [],
            "v2_gap_text": rec.get("v2_gap_text"),
            "remaining_gap": rec.get("remaining_gap"),
        }
        aliases[gid] = [
            x for x in (rec.get("v2_gap_text"), rec.get("remaining_gap")) if x
        ]
    for task in backlog:
        tid = task.get("research_task_id")
        if not tid:
            continue
        gid = RT_TO_GAP.get(tid)
        if not gid:
            continue
        registry.setdefault(
            gid,
            {
                "stable_gap_id": gid,
                "previous_status": None,
                "current_status": task.get("coverage_status"),
                "formal_evidence_ids": task.get("current_evidence_ids") or [],
                "v2_gap_text": task.get("title"),
                "remaining_gap": task.get("title"),
            },
        )
        aliases.setdefault(gid, []).append(task.get("title") or "")
    for key, entry in (blocker_triage or {}).items():
        if not isinstance(entry, dict):
            continue
        gid = TRIAGE_TO_GAP.get(key, key)
        registry.setdefault(
            gid,
            {
                "stable_gap_id": gid,
                "previous_status": None,
                "current_status": None,
                "formal_evidence_ids": [],
                "v2_gap_text": entry.get("snapshot_handling"),
                "remaining_gap": entry.get("rationale"),
            },
        )
        aliases.setdefault(gid, []).append(entry.get("snapshot_handling") or "")
        aliases.setdefault(gid, []).append(entry.get("rationale") or "")
    return registry, aliases


def reconcile_gaps(
    *,
    previous_gap_texts: list[str],
    current_gap_texts: list[str],
    gap_reconciliation: list[dict],
    backlog: list[dict],
    blocker_triage: dict,
) -> dict:
    registry, aliases = build_gap_registry(gap_reconciliation, backlog, blocker_triage)
    prev_matched: dict[str, str] = {}
    cur_matched: dict[str, str] = {}
    for text in previous_gap_texts:
        gid = match_stable_gap(text, aliases)
        if gid:
            prev_matched[gid] = text
    for text in current_gap_texts:
        gid = match_stable_gap(text, aliases)
        if gid:
            cur_matched[gid] = text

    entries: list[dict] = []
    for gid in sorted(set(prev_matched) | set(cur_matched) | set(registry)):
        info = registry.get(gid)
        if not info:
            continue
        prev_present = gid in prev_matched
        cur_present = gid in cur_matched
        if not prev_present and not cur_present:
            continue
        prev_status = info.get("previous_status") or ("active" if prev_present else None)
        cur_status = info.get("current_status") or ("active" if cur_present else None)
        formal_ids = info.get("formal_evidence_ids") or []
        change_type = "unchanged"
        material = False
        basis = f"stable_gap_id:{gid}"
        if prev_present and cur_present:
            if (
                prev_status in ("active", "missing", "partial", "unresolved")
                and cur_status in ("resolved", "completed")
                and formal_ids
            ):
                change_type = "resolved"
                material = True
            elif cur_status in ("narrowed", "reframed", "unchanged"):
                change_type = cur_status
            else:
                change_type = "reframed"
        elif prev_present and not cur_present:
            if (
                cur_status in ("resolved", "completed")
                and prev_status in ("active", "missing", "partial", "unresolved")
                and formal_ids
            ):
                change_type = "resolved"
                material = True
            else:
                change_type = "renamed"
                basis = f"{basis};previous_present_current_not_matched"
        elif cur_present and not prev_present:
            if info.get("previous_status") is None:
                change_type = "new"
                material = bool(formal_ids)
            else:
                change_type = "reframed"
                basis = f"{basis};previous_not_matched_but_registry_known"

        entries.append(
            {
                "stable_gap_id": gid,
                "previous_gap_id": gid if prev_present else None,
                "current_gap_id": gid if cur_present else None,
                "previous_status": prev_status,
                "current_status": cur_status,
                "change_type": change_type,
                "material_for_report": material,
                "matching_basis": basis,
                "formal_evidence_ids": formal_ids,
                "previous_gap_text": prev_matched.get(gid),
                "current_gap_text": cur_matched.get(gid),
            }
        )

    return {
        "previous_gap_count": len(previous_gap_texts),
        "current_gap_count": len(current_gap_texts),
        "matched_previous_gap_count": len(prev_matched),
        "matched_current_gap_count": len(cur_matched),
        "gap_changes": entries,
        "resolved_gaps": [e["stable_gap_id"] for e in entries if e["change_type"] == "resolved"],
        "new_gaps": [e["stable_gap_id"] for e in entries if e["change_type"] == "new"],
        "renamed_gaps": [e["stable_gap_id"] for e in entries if e["change_type"] == "renamed"],
        "reframed_gaps": [e["stable_gap_id"] for e in entries if e["change_type"] == "reframed"],
        "narrowed_gaps": [e["stable_gap_id"] for e in entries if e["change_type"] == "narrowed"],
        "reconciliation_ready": True,
    }
