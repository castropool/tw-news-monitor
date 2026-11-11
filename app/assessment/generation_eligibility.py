"""报告生成资格分级。"""

from __future__ import annotations

from datetime import date


ALLOWED_MODES = ("final", "draft_with_data_gap")


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value).strip()[:10])
    except ValueError:
        return None


def build_generation_eligibility(
    *,
    evidence_pack_ready: bool,
    facts_cutoff: str | None,
    period_end: date,
    uncovered_date_range: list[str],
    errors: list[str] | None = None,
) -> dict:
    errors = errors or []
    fully_covered = (
        _parse_date(facts_cutoff) is not None
        and period_end <= _parse_date(facts_cutoff)
    )
    if evidence_pack_ready and not errors and fully_covered:
        final_allowed = True
        mode = "final"
        warnings: list[str] = []
        disclosures: list[str] = []
        blocking: list[str] = []
    else:
        final_allowed = False
        mode = "draft_with_data_gap"
        warnings = ["报告周期未被正式事实完全覆盖"]
        blocking = ["facts_cutoff 早于 period_end"] if not fully_covered else []
        disclosures = [
            f"正式事实底表仅覆盖至 {facts_cutoff or '未知日期'}",
            "尚未覆盖的具体日期范围：" + ("、".join(uncovered_date_range) if uncovered_date_range else "无"),
            "不得将未覆盖期间表述为“没有重要事件”",
            "报告只能作为数据不完整草稿",
        ]
    return {
        "evidence_pack_ready": evidence_pack_ready,
        "report_period_fully_covered_by_facts": fully_covered,
        "final_report_allowed": final_allowed,
        "allowed_generation_mode": mode,
        "blocking_reasons": blocking,
        "warnings": warnings,
        "required_disclosures": disclosures,
    }
