"""Deterministic Claim–Evidence semantic checks.

The module rejects unsupported output.  It never edits Claims or Evidence.
"""

from __future__ import annotations

import re
from typing import Any


ATTRIBUTION_TERMS = ("表示", "称", "指出", "指称", "宣称", "声称", "指控", "公开指控", "重申")
BOUNDING_TERMS = ("研判", "显示", "可能", "预计", "值得观察", "不足以证明", "不代表", "尚不能", "仍需")
NEGATION_TERMS = ("不足以证明", "不代表", "不得", "尚未", "未完成", "无法", "不能", "并非", "不等于", "禁止", "不能代表")
STRONG_TERMS = (
    "全面完成", "已经完成", "确定已经", "已经逆转", "成熟竞选机器",
    "显著扩大领先", "全面动员", "实质领先", "必然", "确定获胜",
)


def _norm(text: str) -> str:
    return re.sub(r"[^0-9A-Za-z\u4e00-\u9fff]+", "", text or "")


def _bigrams(text: str) -> set[str]:
    value = _norm(text)
    return {value[i : i + 2] for i in range(max(0, len(value) - 1))}


def _overlap(left: str, right: str) -> float:
    a, b = _bigrams(left), _bigrams(right)
    if not a or not b:
        return 0.0
    return len(a & b) / min(len(a), len(b))


def _negated(text: str) -> bool:
    return any(term in text for term in NEGATION_TERMS)


def _atomic(text: str) -> bool:
    body = (text or "").strip().rstrip("。！？")
    if "；" in body or ";" in body:
        return False
    independent_links = len(re.findall(r"，(?:并且|并|同时|因此|进而)", body))
    return independent_links < 2


def _assertions(events: list[dict]) -> list[dict]:
    projected = [
        assertion
        for event in events
        for assertion in (event.get("evidence_assertions") or [])
        if isinstance(assertion, dict) and assertion.get("text")
    ]
    for event in events:
        for field in ("title", "fact_summary"):
            if event.get(field):
                projected.append(
                    {
                        "assertion_id": f"{event.get('event_id')}:{field}",
                        "assertion_type": "observed_fact",
                        "text": str(event[field]),
                        "speaker": None,
                        "source_ids": list(event.get("source_ids") or []),
                    }
                )
    return projected


def _attribution_valid(text: str, assertions: list[dict]) -> tuple[bool, list[str]]:
    failures: list[str] = []
    for assertion in assertions:
        assertion_type = assertion.get("assertion_type")
        if assertion_type not in {"actor_statement", "allegation"}:
            continue
        if _overlap(text, str(assertion.get("text") or "")) < 0.55:
            continue
        speaker = str(assertion.get("speaker") or "").strip()
        has_speaker = bool(speaker) and speaker in text
        has_marker = any(term in text for term in ATTRIBUTION_TERMS)
        if not (has_speaker and has_marker):
            failures.append(
                "allegation_as_fact"
                if assertion_type == "allegation"
                else "statement_as_fact"
            )
    return not failures, failures


def _observed_fact_actor_valid(text: str, assertions: list[dict]) -> bool:
    observed = [a for a in assertions if a.get("assertion_type") == "observed_fact"]
    if not observed:
        return True
    matching = [a for a in observed if _overlap(text, str(a.get("text") or "")) >= 0.55]
    if not matching:
        return False
    for assertion in matching:
        evidence_names = re.findall(r"[\u4e00-\u9fff]{3}", str(assertion.get("text") or ""))
        named_people = [name for name in evidence_names if name[0] in "陈谢林王黄蔡赖柯李苏卢蒋侯韩卓郑何邱郭张吴许罗叶廖沈曾魏江周徐杨朱胡萧游潘马赵孙"]
        if named_people and not any(name in text for name in named_people):
            return False
    return True


def validate_claim_semantics(claim: dict, ctx: Any) -> dict:
    """Return an auditable, non-mutating decision for one Claim."""

    failures: list[str] = []
    text = str(claim.get("claim_text") or "")
    claim_type = claim.get("claim_type")
    event_ids = list(claim.get("supporting_event_ids") or [])
    poll_ids = list(claim.get("supporting_poll_ids") or [])
    source_ids = list(claim.get("supporting_source_ids") or [])
    gap_ids = list(claim.get("supporting_gap_ids") or [])
    dimensions = list(claim.get("supporting_snapshot_dimensions") or [])

    events_exist = set(event_ids) <= set(ctx.events)
    polls_exist = set(poll_ids) <= set(ctx.polls)
    sources_exist = set(source_ids) <= set(ctx.sources)
    gaps_exist = set(gap_ids) <= set(ctx.gaps)
    dimensions_exist = set(dimensions) <= set(ctx.dimension_names)
    if not events_exist:
        failures.append("invalid_event_reference")
    if not polls_exist:
        failures.append("invalid_poll_reference")
    if not sources_exist:
        failures.append("invalid_source_reference")
    if not gaps_exist:
        failures.append("invalid_gap_reference")
    if not dimensions_exist:
        failures.append("invalid_snapshot_dimension")

    evidence_ids = event_ids + poll_ids
    evidence_required = claim_type not in {"data_disclosure", "limitation"}
    if evidence_required and not (evidence_ids or dimensions):
        failures.append("missing_evidence")

    cited_events = [ctx.events[eid] for eid in event_ids if eid in ctx.events]
    cited_polls = [ctx.polls[pid] for pid in poll_ids if pid in ctx.polls]
    if evidence_ids:
        if not source_ids:
            failures.append("missing_source_reference")
        else:
            all_evidence = [("event", item) for item in cited_events] + [
                ("poll", item) for item in cited_polls
            ]
            allowed_union = {
                sid
                for _kind, item in all_evidence
                for sid in (item.get("source_ids") or [])
            }
            if not set(source_ids) <= allowed_union:
                failures.append("source_not_linked_to_evidence")
            if any(
                not (set(source_ids) & set(item.get("source_ids") or []))
                for _kind, item in all_evidence
            ):
                failures.append("evidence_without_linked_source")

    if not _atomic(text) and claim_type not in {"data_disclosure", "limitation"}:
        failures.append("claim_not_atomic")

    if any(term in text for term in STRONG_TERMS) and not _negated(text):
        independent_sources = {
            sid
            for item in cited_events + cited_polls
            for sid in (item.get("source_ids") or [])
        }
        if len(cited_events) + len(cited_polls) < 2 or len(independent_sources) < 2:
            failures.append("claim_strength_exceeds_evidence")

    if claim_type == "forward_outlook" and len(event_ids) + len(poll_ids) < 2:
        failures.append("forward_outlook_insufficient_evidence")
    if claim_type in {"current_assessment", "comparative_assessment", "forward_outlook"}:
        if not claim.get("inference_basis"):
            failures.append("missing_inference_basis")

    assertions = _assertions(cited_events)
    attribution_ok, attribution_failures = _attribution_valid(text, assertions)
    if not attribution_ok:
        failures.extend(attribution_failures)

    if claim_type == "factual_synthesis" and assertions:
        observed = [a for a in assertions if a.get("assertion_type") == "observed_fact"]
        attributed = [
            a
            for a in assertions
            if a.get("assertion_type") in {"actor_statement", "allegation"}
        ]
        observed_supported = _observed_fact_actor_valid(text, assertions)
        attributed_supported = attribution_ok and any(
            _overlap(text, str(item.get("text") or "")) >= 0.55 for item in attributed
        )
        if observed and not observed_supported and not attributed_supported:
            failures.append("evidence_does_not_support_claim")

    allowed_dates = {
        str(item.get("event_date") or "")[:10]
        for item in cited_events
        if item.get("event_date")
    }
    allowed_dates.update(
        str(item.get(key) or "")[:10]
        for item in cited_polls
        for key in ("release_date", "fieldwork_start", "fieldwork_end")
        if item.get(key)
    )
    for date_text in re.findall(r"\d{4}-\d{2}-\d{2}", text):
        data_status = ctx.contract.get("data_status") or {}
        report_period = ctx.contract.get("report_period") or {}
        data_dates = {
            str(data_status.get("facts_cutoff") or "")[:10],
            str(data_status.get("poll_cutoff") or "")[:10],
            str(report_period.get("period_start") or "")[:10],
            str(report_period.get("period_end") or "")[:10],
        }
        data_dates.update(str(item)[:10] for item in data_status.get("uncovered_date_range") or [])
        if date_text not in allowed_dates | data_dates:
            failures.append("temporal_scope_mismatch")

    period_start = str((ctx.contract.get("report_period") or {}).get("period_start") or "")[:10]
    if poll_ids and any(term in text for term in ("实时", "当前支持率", "最新实时")) and not _negated(text):
        if any(
            str((ctx.polls.get(pid) or {}).get("fieldwork_end") or "")[:10] < period_start
            for pid in poll_ids
        ):
            failures.append("old_poll_as_current")

    failures = list(dict.fromkeys(failures))
    return {
        "claim_id": claim.get("claim_id"),
        "accepted": not failures,
        "failures": failures,
        "attribution_valid": attribution_ok,
        "atomic_claim_valid": "claim_not_atomic" not in failures,
        "references_valid": not any("reference" in item for item in failures),
    }
