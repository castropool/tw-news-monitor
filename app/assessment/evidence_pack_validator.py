"""证据包验证器（确定性检查，不调用大模型）。"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from typing import Any


@dataclass
class ValidationContext:
    formal_event_ids: set[str]
    formal_source_ids: set[str]
    formal_link_pairs: set[tuple[str, str]]
    formal_poll_ids: set[str]
    blocked_ids: set[str]
    active_snapshot_id: str
    previous_snapshot_id: str | None
    coverage_name: str
    facts_cutoff: str | None
    poll_cutoff: str | None
    expected_counts: dict[str, int]
    before_hashes: dict[str, str]
    after_hashes: dict[str, str]
    period_start: date
    period_end: date
    max_background_total: int = 15
    authoritative_active_task_ids: list[str] | None = None
    llm_contract_validation: dict | None = None


def _check(result: dict, name: str, ok: bool, message: str = "") -> None:
    result[name] = bool(ok)
    if not ok and message:
        result["errors"].append(f"{name}: {message}")


def _warn(result: dict, message: str) -> None:
    if message not in result["warnings"]:
        result["warnings"].append(message)


def validate_evidence_pack(pack: dict, ctx: ValidationContext) -> dict:
    result: dict[str, Any] = {
        "evidence_pack_ready": False,
        "errors": [],
        "warnings": [],
    }
    rp = pack.get("report_period") or {}
    ds = pack.get("data_status") or {}
    period_events = pack.get("period_events") or []
    background_events = pack.get("background_events") or []
    sources = pack.get("sources") or []
    polls = pack.get("polls") or []
    state_diff = pack.get("state_diff") or {}
    stats = pack.get("evidence_statistics") or {}

    # ---- 周期 ----
    period_ok = (
        bool(rp.get("period_start"))
        and bool(rp.get("period_end"))
        and ctx.period_start.isoformat() == rp.get("period_start")
        and ctx.period_end.isoformat() == rp.get("period_end")
        and bool(rp.get("period_complete")) is True
    )
    _check(result, "period_ready", period_ok, "报告周期与请求不一致或未完成")

    # ---- 正式数据一致性 ----
    counts_ok = all(
        ds.get(key) == value
        for key, value in ctx.expected_counts.items()
        if key in ds
    )
    _check(result, "formal_data_consistent", counts_ok, "data_status 计数与正式数据不符")

    # ---- 快照 ----
    active_ok = (
        (pack.get("current_snapshot") or {}).get("snapshot_id") == ctx.active_snapshot_id
    )
    _check(result, "active_snapshot_unique", active_ok, "current_snapshot 不是唯一 active")
    prev = pack.get("previous_snapshot")
    if ctx.previous_snapshot_id:
        prev_ok = prev is not None and prev.get("snapshot_id") == ctx.previous_snapshot_id
        _check(result, "previous_snapshot_valid", prev_ok, "previous_snapshot 选择错误")
    else:
        _check(result, "previous_snapshot_valid", prev is None, "无前序快照但输出了 previous_snapshot")

    # ---- 覆盖 ----
    _check(
        result,
        "coverage_version_valid",
        ds.get("coverage_version") == ctx.coverage_name,
        f"覆盖版本应为 {ctx.coverage_name}",
    )

    # ---- 事件 ----
    period_ids = [e.get("event_id") for e in period_events]
    bg_ids = [e.get("event_id") for e in background_events]
    all_event_ids = period_ids + bg_ids
    _check(
        result,
        "period_events_valid",
        len(period_ids) == len(set(period_ids))
        and all(e.get("evidence_role") == "period_event" for e in period_events)
        and all(
            set(e.get("inclusion_reasons") or []) <= {
                "event_date_in_period", "subevent_date_in_period", "active_snapshot_evidence"
            }
            for e in period_events
        ),
        "period_events 存在重复、错误角色或非法纳入原因",
    )
    _check(
        result,
        "background_events_valid",
        len(bg_ids) == len(set(bg_ids))
        and all(e.get("evidence_role") == "background" for e in background_events)
        and len(bg_ids) <= ctx.max_background_total,
        "background_events 存在重复、错误角色或超出数量上限",
    )
    _check(
        result,
        "no_duplicate_events",
        len(all_event_ids) == len(set(all_event_ids)),
        "事件重复",
    )
    _check(
        result,
        "all_event_ids_exist",
        set(all_event_ids) <= ctx.formal_event_ids,
        "存在非正式事件 id",
    )
    for e in period_events + background_events:
        if e.get("event_id") in ctx.blocked_ids:
            result["errors"].append(f"no_hold_records: 事件 {e.get('event_id')} 出现在 hold/negative 记录中")

    # ---- 来源 ----
    source_ids = [s.get("source_id") for s in sources]
    _check(
        result,
        "sources_valid",
        len(source_ids) == len(set(source_ids))
        and all(s.get("is_formal_source") is True for s in sources),
        "sources 存在重复或非正式来源标记",
    )
    _check(
        result,
        "all_source_ids_exist",
        set(source_ids) <= ctx.formal_source_ids,
        "存在非正式 source_id",
    )
    _check(
        result,
        "no_duplicate_sources",
        len(source_ids) == len(set(source_ids)),
        "来源重复",
    )
    _check(
        result,
        "no_orphan_sources",
        all((s.get("linked_event_ids") or []) for s in sources),
        "存在未关联任何事件的来源",
    )
    for s in sources:
        if s.get("source_id") in ctx.blocked_ids:
            result["errors"].append(f"no_hold_records: 来源 {s.get('source_id')} 出现在 hold/negative 记录中")

    # ---- 事件-来源关系 ----
    links_ok = True
    for e in period_events + background_events:
        for sid in e.get("source_ids") or []:
            if (e["event_id"], sid) not in ctx.formal_link_pairs:
                links_ok = False
                result["errors"].append(
                    f"all_event_source_links_exist: 缺少关系 {e['event_id']} -> {sid}"
                )
    _check(result, "all_event_source_links_exist", links_ok, "存在非法事件-来源关系")
    _check(result, "no_orphan_links", links_ok, "存在孤立关系")

    # ---- 民调 ----
    poll_ids = [p.get("poll_id") for p in polls]
    _check(
        result,
        "polls_valid",
        len(poll_ids) == len(set(poll_ids))
        and all(
            (not p.get("trend_eligible")) or p.get("methodology_complete") is True
            for p in polls
        )
        and all(p.get("recommended_disposition") not in ("hold", "preview", "negative") for p in polls),
        "民调重复、趋势资格与方法学不一致或含 hold 记录",
    )
    _check(
        result,
        "all_poll_ids_exist",
        set(poll_ids) <= ctx.formal_poll_ids,
        "存在非正式 poll_id",
    )
    for p in polls:
        if p.get("poll_id") in ctx.blocked_ids:
            result["errors"].append(f"no_hold_records: 民调 {p.get('poll_id')} 出现在 hold/negative 记录中")

    # ---- 状态差异 ----
    state_diff_ok = (
        state_diff.get("state_diff_mode") in ("initial_baseline", "structured_comparison")
        and all(k in state_diff for k in (
            "changed_dimensions", "unchanged_dimensions", "dimensions",
            "snapshot_evidence_reference_additions", "snapshot_evidence_reference_removals",
            "new_risks", "risk_changes", "confidence_changes",
        ))
    )
    forbidden_terms = ("win_probability", "胜选概率", "support_rate", "支持率", "prob_win")
    sd_text = json.dumps(state_diff, ensure_ascii=False)
    if any(term in sd_text for term in forbidden_terms):
        state_diff_ok = False
        result["errors"].append("state_diff_valid: 状态差异包含禁止的推断字段")
    _check(result, "state_diff_valid", state_diff_ok, "state_diff 结构无效或包含推断")

    # ---- 语义分层检查 ----
    dimensions = state_diff.get("dimensions") or []
    dim_keys = {
        "dimension", "previous_status", "current_status", "change_status",
        "change_scope", "changed_paths", "material_for_report",
        "material_change_summary", "evidence_only_change_summary",
        "limitations_change_summary",
    }
    sem_ok = (
        isinstance(dimensions, list)
        and len(dimensions) == 7
        and all(isinstance(d, dict) and dim_keys <= set(d) for d in dimensions)
        and all(
            d.get("material_for_report") is True
            and "business_state" in (d.get("change_scope") or [])
            or d.get("material_for_report") is False
            for d in dimensions
        )
        and not any(term in sd_text for term in forbidden_terms)
    )
    _check(result, "state_diff_semantically_valid", sem_ok, "state_diff 语义分层无效")

    # ---- 快照引用增减 vs 正式删除 ----
    sec = pack.get("snapshot_evidence_changes") or {}
    dist_ok = (
        "event_reference_additions" in sec
        and "event_reference_removals" in sec
        and "poll_reference_additions" in sec
        and "poll_reference_removals" in sec
        and "evidence_removals" not in state_diff
    )
    _check(
        result,
        "snapshot_reference_changes_distinguished",
        dist_ok,
        "快照引用变化未与正式记录删除区分",
    )
    del_ok = (
        sec.get("formal_events_deleted") == []
        and sec.get("formal_polls_deleted") == []
        and sec.get("reconciliation_ready") is True
    )
    _check(
        result,
        "formal_record_deletion_check_passed",
        del_ok,
        "正式事件/民调被错误标记为删除",
    )

    # ---- gap / risk 对账 ----
    gaps = pack.get("gap_changes") or []
    gap_ok = (
        all(
            g.get("change_type") != "resolved"
            or (
                g.get("formal_evidence_ids")
                and g.get("previous_status") in ("active", "missing", "partial", "unresolved")
                and g.get("current_status") in ("resolved", "completed")
            )
            for g in gaps
        )
        and all(
            g.get("change_type") != "new" or g.get("previous_status") is None
            for g in gaps
        )
        and all(
            g.get("change_type") not in ("renamed", "reframed", "narrowed", "widened")
            or g.get("material_for_report") is False
            for g in gaps
        )
    )
    _check(result, "gap_changes_reconciled", gap_ok, "gap 变化分类不一致")

    risks = pack.get("risk_changes") or []
    risk_ok = (
        all(
            r.get("change_type") != "newly_emerged_risk"
            or (r.get("supporting_event_ids") and not r.get("previously_present"))
            for r in risks
        )
        and all(
            r.get("change_type") != "existing_limitation_carried_forward"
            or r.get("previously_present") is False
            for r in risks
        )
        and len(risks) == (stats.get("risk_change_count") or 0)
    )
    _check(result, "risk_changes_reconciled", risk_ok, "risk 分类不一致")

    # ---- 研究任务 ----
    recon = pack.get("research_task_status_reconciliation") or {}
    task_ids = [t.get("research_task_id") for t in (pack.get("active_research_tasks") or [])]
    authoritative = ctx.authoritative_active_task_ids or []
    task_ok = (
        recon.get("reconciliation_ready") is True
        and len(task_ids) == len(set(task_ids))
        and sorted(task_ids) == sorted(authoritative)
    )
    _check(
        result,
        "research_task_status_consistent",
        task_ok,
        "active_research_tasks 与权威任务状态不一致",
    )

    # ---- 生成资格 ----
    elig = pack.get("generation_eligibility") or {}
    fully_covered = ds.get("report_period_fully_covered_by_facts") is True
    elig_ok = (
        elig.get("allowed_generation_mode") in ("final", "draft_with_data_gap")
        and elig.get("final_report_allowed") is fully_covered
        and (
            elig.get("allowed_generation_mode") != "draft_with_data_gap"
            or len(elig.get("required_disclosures") or []) >= 3
        )
    )
    _check(result, "generation_eligibility_valid", elig_ok, "generation_eligibility 无效")

    # ---- LLM 输入合同 ----
    contract_validation = ctx.llm_contract_validation or {}
    _check(
        result,
        "llm_input_contract_ready",
        contract_validation.get("llm_input_contract_ready") is True,
        "; ".join(contract_validation.get("errors") or []) or "LLM 输入合同验证失败",
    )

    # ---- 限制与禁止推断 ----
    _check(
        result,
        "limitations_present",
        bool(pack.get("known_limitations")),
        "known_limitations 为空",
    )
    _check(
        result,
        "do_not_infer_present",
        bool(pack.get("do_not_infer")),
        "do_not_infer 为空",
    )
    _check(
        result,
        "facts_cutoff_disclosed",
        bool(ds.get("facts_cutoff")),
        "facts_cutoff 未披露",
    )
    _check(
        result,
        "poll_cutoff_disclosed",
        bool(ds.get("poll_cutoff")),
        "poll_cutoff 未披露",
    )

    # ---- hold / preview / negative ----
    blocked_hit = (
        any(e.get("event_id") in ctx.blocked_ids for e in period_events + background_events)
        or any(s.get("source_id") in ctx.blocked_ids for s in sources)
        or any(p.get("poll_id") in ctx.blocked_ids for p in polls)
    )
    _check(result, "no_hold_records", not blocked_hit, "证据包包含 hold 记录")
    _check(result, "no_preview_records", not blocked_hit, "证据包包含 preview 记录")
    _check(result, "no_negative_findings_as_facts", not blocked_hit, "negative finding 被当作正式事实")

    # ---- 截止日期与覆盖 ----
    if ds.get("report_period_fully_covered_by_facts") is not True:
        _warn(result, f"报告周期晚于 facts_cutoff={ctx.facts_cutoff}，正式事实未完整覆盖本期")
    if stats.get("poll_gap") is True:
        _warn(result, f"本期无正式民调（poll_cutoff={ctx.poll_cutoff}）")

    # ---- 正式数据保护 ----
    _check(
        result,
        "formal_data_unchanged",
        ctx.before_hashes == ctx.after_hashes,
        "正式输入哈希在运行前后发生变化",
    )
    _check(
        result,
        "snapshot_data_unchanged",
        all(
            ctx.before_hashes.get(k) == ctx.after_hashes.get(k)
            for k in ctx.before_hashes
            if "snapshot" in k or "initial_snapshot" in k
        ),
        "快照哈希发生变化",
    )
    _check(
        result,
        "coverage_data_unchanged",
        all(
            ctx.before_hashes.get(k) == ctx.after_hashes.get(k)
            for k in ctx.before_hashes
            if "coverage" in k
        ),
        "覆盖目录哈希发生变化",
    )
    _check(
        result,
        "poll_data_unchanged",
        all(
            ctx.before_hashes.get(k) == ctx.after_hashes.get(k)
            for k in ctx.before_hashes
            if "poll" in k
        ),
        "民调种子哈希发生变化",
    )

    # ---- 汇总 ----
    result["period_ready"] = result.get("period_ready", False)
    result["evidence_pack_ready"] = not result["errors"]
    return result
