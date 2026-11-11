"""Assessment Research Pack 构建器（research-driven 生产路径）。

只读正式事实底座（election_context.db + 正式 seed），不调用大模型，
不修改正式数据。产出的研究包是最终研判模型的唯一事实基础之一，
同时生成可直接上传 ChatGPT 的 Markdown 版本（人工 Fallback）。

复用 evidence_pack_builder 的正式数据读取与周期提取逻辑；
本模块负责研究包语义：周期、本期事件、历史背景、上一期状态、
人物与阵营、民调、治理议题、来源、证据限制。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from app.assessment.evidence_pack_builder import (
    FormalData,
    collect_sources_for_events,
    extract_period_events,
    include_polls,
    load_formal_data,
    parse_date,
    select_background_events,
    snapshot_supporting_ids,
)
from app.assessment.state_diff import diff_snapshots

PACK_SCHEMA_VERSION = "1.0"

# 阵营关键词映射（deterministic camp assignment）——台南默认；多选举时由
# config ``research_pack.camps`` 覆盖（见 build_research_pack）。
CAMP_KEYWORDS: dict[str, list[str]] = {
    "chen_ting_fei": ["陈亭妃", "亭妃"],
    "hsieh_lung_chieh": ["谢龙介", "龙介"],
    "lai_faction": ["赖清德", "赖系", "赖总统", "党中央", "中央党部", "总统府"],
    "kmt": ["国民党", "蓝营", "朱立伦", "国民党团"],
    "tpp": ["民众党", "黄国昌", "柯文哲", "白营"],
    "blue_white": ["蓝白", "蓝白合作", "白蓝"],
}

# 台南默认的研究包阵营章节（向后兼容；新北由 config research_pack.camps 覆盖）。
DEFAULT_CAMP_SECTIONS: list[dict[str, str]] = [
    {"key": "chen_ting_fei", "label": "陈亭妃阵营"},
    {"key": "hsieh_lung_chieh", "label": "谢龙介阵营"},
    {"key": "lai_faction", "label": "民进党派系与中央关系（赖系）"},
    {"key": "blue_white", "label": "蓝白合作"},
]


def _camp_defs_from_config(config: dict) -> tuple[dict[str, list[str]], list[dict[str, str]]]:
    """从 config 读阵营定义；缺省回落台南默认。"""
    raw = (config.get("research_pack") or {}).get("camps") or []
    if not raw:
        return dict(CAMP_KEYWORDS), list(DEFAULT_CAMP_SECTIONS)
    keywords: dict[str, list[str]] = {}
    sections: list[dict[str, str]] = []
    for item in raw:
        key = str(item.get("key") or "").strip()
        label = str(item.get("label") or key).strip()
        kws = [str(x) for x in (item.get("keywords") or []) if str(x).strip()]
        if not key:
            continue
        keywords[key] = kws or [label]
        sections.append({"key": key, "label": label})
    return keywords or dict(CAMP_KEYWORDS), sections or list(DEFAULT_CAMP_SECTIONS)


@dataclass
class ResearchPackContext:
    """研究包构建所需的运行上下文。"""

    period_start: date
    period_end: date
    previous_period_start: date | None
    previous_period_end: date | None
    previous_period_report: dict | None  # 上一期正式报告（production store）
    previous_period_article: str | None  # 上一期最终文章正文


def _seed_actor_issues_index(formal: FormalData) -> dict[str, dict]:
    """从正式事件 seed 读取每条的 actors/issues 原始列表（normalize_event 不携带）。"""
    index: dict[str, dict] = {}
    events_path = formal.coverage_dir.parent / "events.jsonl"
    if not events_path.exists():
        return index
    try:
        with open(events_path, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                rec = json.loads(line)
                actors = rec.get("actors") or []
                issues = rec.get("issues") or []
                if isinstance(actors, str):
                    actors = json.loads(actors) if actors.strip().startswith("[") else [actors]
                if isinstance(issues, str):
                    issues = json.loads(issues) if issues.strip().startswith("[") else [issues]
                index[rec["event_id"]] = {
                    "actors": [str(x) for x in actors if x],
                    "issues": [str(x) for x in issues if x],
                }
    except Exception:  # noqa: BLE001
        return {}
    return index


def _resolve_actor_names(formal: FormalData, event: dict, actors_raw: list[str] | None = None) -> list[str]:
    """把事件 actors（ID 或原始名）解析为规范中文名。"""
    if actors_raw is None:
        actors_raw = event.get("actors") or []
    if isinstance(actors_raw, str):
        try:
            actors_raw = json.loads(actors_raw)
        except Exception:  # noqa: BLE001
            actors_raw = [actors_raw]
    resolved: list[str] = []
    id_to_name: dict[str, str] = {}
    alias_to_name: dict[str, str] = {}
    for row in _read_actors(formal):
        id_to_name[row["actor_id"]] = row["canonical_name"]
        for alias in row["aliases"]:
            alias_to_name[alias] = row["canonical_name"]
    for item in actors_raw or []:
        if not isinstance(item, str) or not item.strip():
            continue
        if item in id_to_name:
            resolved.append(id_to_name[item])
        elif item in alias_to_name:
            resolved.append(alias_to_name[item])
        else:
            resolved.append(item)
    return list(dict.fromkeys(resolved))


def _read_actors(formal: FormalData) -> list[dict]:
    """从正式数据读取 actors（通过 seed actors.yaml 或 DB）。"""
    actors_path = formal.coverage_dir.parent / "actors.yaml"
    if not actors_path.exists():
        return []
    try:
        import yaml

        data = yaml.safe_load(actors_path.read_text(encoding="utf-8")) or {}
        if isinstance(data, dict):
            items = data.get("actors") or []
        elif isinstance(data, list):
            items = data
        else:
            items = []
        out = []
        for item in items:
            if not isinstance(item, dict):
                continue
            out.append(
                {
                    "actor_id": item.get("actor_id") or item.get("id") or "",
                    "canonical_name": item.get("canonical_name") or item.get("name") or "",
                    "aliases": list(item.get("aliases") or item.get("alias") or []),
                }
            )
        return out
    except Exception:  # noqa: BLE001
        return []


def _event_sources(event: dict, formal: FormalData) -> list[dict]:
    """事件关联的正式来源（publisher/title/date）。"""
    out = []
    for sid in event.get("source_ids") or []:
        src = formal.sources.get(sid)
        if src:
            out.append(
                {
                    "source_id": sid,
                    "publisher": src.get("publisher"),
                    "title": src.get("title"),
                    "published_at": str(src.get("published_at") or "")[:10],
                    "source_type": src.get("source_type") or "news",
                }
            )
    return out


def _compact_event(event: dict, formal: FormalData, seed_index: dict[str, dict]) -> dict:
    """事件在研究包中的紧凑表达：事实完整、可追溯、无内部噪音。"""
    seed = seed_index.get(event.get("event_id") or "", {})
    actors_raw = seed.get("actors") or []
    if not actors_raw:
        actors_raw = _resolve_actor_names(formal, event)
    return {
        "event_id": event.get("event_id"),
        "event_date": event.get("event_date"),
        "event_type": event.get("event_type"),
        "actors": _resolve_actor_names(formal, event, actors_raw),
        "title": event.get("title"),
        "fact_summary": event.get("fact_summary"),
        "fact_status": event.get("fact_status"),
        "significance_score": event.get("significance_score"),
        "verified_facts": event.get("verified_facts") or [],
        "actor_statements": event.get("actor_statements") or [],
        "analytical_significance": event.get("analytical_significance") or "",
        "limitations": event.get("limitations") or [],
        "issues": seed.get("issues") or [],
        "in_period_subevents": event.get("in_period_subevents") or [],
        "inclusion_reasons": event.get("inclusion_reasons") or [],
        "sources": _event_sources(event, formal),
        "evidence_role": event.get("evidence_role"),
    }


def _assign_camps(
    events: list[dict],
    formal: FormalData,
    seed_index: dict[str, dict],
    camp_keywords: dict[str, list[str]] | None = None,
) -> dict[str, list[dict]]:
    """按人物/阵营把事件分组（一个事件可属多个阵营）。"""
    camp_keywords = camp_keywords or CAMP_KEYWORDS
    camps: dict[str, list[dict]] = {key: [] for key in camp_keywords}
    for ev in events:
        text = " ".join(
            str(x)
            for x in [
                ev.get("title"),
                ev.get("fact_summary"),
                ",".join(ev.get("actors") or []),
                ",".join(ev.get("actor_statements") or []),
            ]
        )
        for camp, keywords in camp_keywords.items():
            if any(kw in text for kw in keywords):
                camps[camp].append(_compact_event(ev, formal, seed_index))
    for key in list(camps):
        if not camps[key]:
            del camps[key]
    return camps


def _governance_issues(events: list[dict], formal: FormalData) -> list[dict]:
    """归纳治理相关议题（灾害/治水/光电/交通/产业等）。"""
    gov_keywords = {
        "灾害与治水": ["治水", "淹水", "积水", "台风", "豪雨", "河川", "溪", "排水", "防灾", "防汛"],
        "光电与土地": ["光电", "太阳能", "土地", "农地", "用地"],
        "交通": ["交通", "捷运", "铁路", "道路", "公车"],
        "治安": ["治安", "警", "犯罪"],
        "产业与经济": ["产业", "招商", "就业", "观光", "夜市", "商圈"],
        "财政与建设": ["预算", "财政", "建设", "工程"],
    }
    issues: dict[str, list[str]] = {}
    for ev in events:
        text = " ".join(
            str(x)
            for x in [ev.get("title"), ev.get("fact_summary"), ",".join(ev.get("issues") or [])]
        )
        for issue, keywords in gov_keywords.items():
            if any(kw in text for kw in keywords):
                issues.setdefault(issue, []).append(ev["event_id"])
    return [
        {"issue": issue, "event_ids": sorted(set(ids))}
        for issue, ids in sorted(issues.items())
    ]


def _poll_unit(unit: Any) -> str:
    text = str(unit or "").strip().lower()
    if text in ("percent", "pct", "%", "percentage"):
        return "%"
    return str(unit or "")


def _poll_numbers(poll: dict) -> list[dict]:
    """民调关键数字（候选人支持度）扁平化。"""
    out = []
    for q in poll.get("questions") or []:
        for r in poll.get("results") or []:
            if r.get("question_id") != q.get("question_id"):
                continue
            if r.get("option_type") not in ("candidate", "party", None):
                continue
            out.append(
                {
                    "question_id": q.get("question_id"),
                    "option": r.get("option_name"),
                    "value": r.get("value"),
                    "reported_value": r.get("reported_value"),
                    "unit": _poll_unit(r.get("unit")),
                }
            )
    return out


def _polls_section(
    formal: FormalData,
    period_start: date,
    period_end: date,
) -> dict:
    """民调部分：本期新增 + 最新正式民调 + 与前次变化 + 空窗声明。"""
    polls, poll_gap, period_poll_count, context_poll_count = include_polls(
        formal.polls, period_start, period_end, formal.active_snapshot
    )
    active_state = formal.active_snapshot.get("state") or {}
    poll_assessment = active_state.get("public_poll_assessment") or {}
    referenced_ids = list(poll_assessment.get("supporting_poll_ids") or [])
    by_id = {p["poll_id"]: p for p in polls}
    latest = max(polls, key=lambda p: str(p.get("fieldwork_end") or "")) if polls else None

    latest_polls = []
    for pid in referenced_ids:
        p = by_id.get(pid)
        if p:
            latest_polls.append(
                {
                    "poll_id": p["poll_id"],
                    "pollster": p.get("pollster"),
                    "sponsor": p.get("sponsor"),
                    "release_date": str(p.get("release_date") or "")[:10],
                    "fieldwork_start": str(p.get("fieldwork_start") or "")[:10],
                    "fieldwork_end": str(p.get("fieldwork_end") or "")[:10],
                    "sample_size": p.get("sample_size"),
                    "numbers": _poll_numbers(p),
                }
            )
    latest_polls.sort(key=lambda p: p.get("fieldwork_end") or "")

    changes: list[dict] = []
    comparable = [p for p in latest_polls if p.get("fieldwork_end")]
    comparable.sort(key=lambda p: p["fieldwork_end"])
    for prev, cur in zip(comparable, comparable[1:]):
        prev_map = {(n["question_id"], n["option"]): n for n in prev["numbers"]}
        for n in cur["numbers"]:
            key = (n["question_id"], n["option"])
            if key in prev_map and prev_map[key].get("value") is not None and n.get("value") is not None:
                changes.append(
                    {
                        "option": n["option"],
                        "from_date": prev["fieldwork_end"],
                        "to_date": cur["fieldwork_end"],
                        "from_value": prev_map[key]["value"],
                        "to_value": n["value"],
                        "delta": round(float(n["value"]) - float(prev_map[key]["value"]), 1),
                        "pollster": cur.get("pollster"),
                    }
                )

    return {
        "poll_gap": poll_gap,
        "period_poll_count": period_poll_count,
        "context_poll_count": context_poll_count,
        "poll_cutoff": str((active_state.get("coverage") or {}).get("poll_cutoff") or ""),
        "latest_polls": latest_polls,
        "latest_field_end": str(poll_assessment.get("latest_field_end") or ""),
        "changes_vs_previous": changes,
        "stale_note": (
            f"正式民调最新调查截止 {poll_assessment.get('latest_field_end') or '未知'}，"
            "之后没有新的可比较正式民调。"
        ),
        "no_new_poll_note": "本期无新增正式民调。" if poll_gap else "",
    }


def _previous_state_baseline(formal: FormalData) -> dict:
    """上一期状态基线：active/previous 快照的结构化状态与差异。"""
    active_state = formal.active_snapshot.get("state") or {}
    previous = formal.previous_snapshot
    previous_state = previous.get("state") if previous else None
    diff = diff_snapshots(active_state, previous_state) if previous_state is not None else {}
    summary_keys = (
        "candidate_status",
        "structural_lean",
        "competitiveness",
        "dpp_integration",
        "kmt_organization",
        "kmt_tpp_cooperation",
        "public_poll_assessment",
        "core_issues",
        "key_risks",
    )

    def summarize(state: dict | None) -> dict:
        if not state:
            return {}
        return {k: state.get(k) for k in summary_keys if state.get(k)}

    return {
        "baseline_mode": "previous_snapshot" if previous else "initial_baseline",
        "previous_snapshot": (
            {
                "snapshot_id": previous["snapshot_id"],
                "as_of": previous["as_of"],
                "state_summary": summarize(previous_state),
            }
            if previous
            else None
        ),
        "active_snapshot": {
            "snapshot_id": formal.active_snapshot["snapshot_id"],
            "as_of": formal.active_snapshot["as_of"],
            "state_summary": summarize(active_state),
        },
        "state_diff": {
            "status": diff.get("status"),
            "changed_dimensions": diff.get("changed_dimensions") or [],
            "unchanged_dimensions": diff.get("unchanged_dimensions") or [],
            "dimensions": [
                d for d in (diff.get("dimensions") or []) if d.get("dimension") in summary_keys
            ],
        },
    }


def build_research_pack(
    formal: FormalData,
    ctx: ResearchPackContext,
    config: dict,
) -> dict:
    """构建完整研究包（JSON）。"""
    start, end = ctx.period_start, ctx.period_end
    seed_index = _seed_actor_issues_index(formal)
    period_events = extract_period_events(formal.events, start, end, formal.active_snapshot)
    period_event_ids = {e["event_id"] for e in period_events}
    background_events = select_background_events(
        formal.events,
        period_event_ids,
        formal.active_snapshot,
        formal.previous_snapshot,
        config,
    )
    sources, included_source_ids = collect_sources_for_events(
        period_events + background_events, formal.sources, formal.links
    )
    # 来源按事件分组（正文可追溯，后台保留 source_id）
    source_index: dict[str, list[str]] = {}
    for ev in period_events + background_events:
        for sid in ev.get("source_ids") or []:
            if sid in included_source_ids:
                source_index.setdefault(sid, []).append(ev["event_id"])

    facts_cutoff = (
        formal.coverage_preflight.get("facts_cutoff")
        or ((formal.active_snapshot.get("state") or {}).get("coverage") or {}).get("facts_cutoff")
    )
    poll_cutoff = (
        formal.coverage_preflight.get("poll_cutoff")
        or ((formal.active_snapshot.get("state") or {}).get("coverage") or {}).get("poll_cutoff")
    )

    period_compact = [_compact_event(e, formal, seed_index) for e in period_events]
    background_compact = [_compact_event(e, formal, seed_index) for e in background_events]
    camp_keywords, camp_sections = _camp_defs_from_config(config)
    camps = _assign_camps(
        period_events + background_events, formal, seed_index, camp_keywords=camp_keywords
    )
    governance = _governance_issues(period_compact + background_compact, formal)

    active_state = formal.active_snapshot.get("state") or {}
    known_limitations: list[str] = []
    for section in (
        "structural_lean",
        "competitiveness",
        "dpp_integration",
        "kmt_organization",
        "kmt_tpp_cooperation",
        "public_poll_assessment",
    ):
        known_limitations.extend(
            str(x) for x in ((active_state.get(section) or {}).get("limitations") or [])
        )
    known_limitations = list(dict.fromkeys(x for x in known_limitations if x))

    do_not_infer: list[str] = []
    for section in ("dpp_integration", "kmt_tpp_cooperation"):
        do_not_infer.extend(
            str(x) for x in ((active_state.get(section) or {}).get("prohibited_conclusions") or [])
        )
    do_not_infer = list(dict.fromkeys(x for x in do_not_infer if x))

    polls_section = _polls_section(formal, start, end)

    pack: dict[str, Any] = {
        "pack_schema_version": PACK_SCHEMA_VERSION,
        "pack_mode": "research_driven",
        "election": {
            "election_id": config["election"]["election_id"],
            "display_name": config["election"]["display_name"],
            "report_label": (
                (config["election"].get("report_label"))
                or str(config["election"]["display_name"])
            ),
        },
        "period": {
            "period_start": start.isoformat(),
            "period_end": end.isoformat(),
            "previous_period_start": (
                ctx.previous_period_start.isoformat() if ctx.previous_period_start else None
            ),
            "previous_period_end": (
                ctx.previous_period_end.isoformat() if ctx.previous_period_end else None
            ),
            "facts_cutoff": str(facts_cutoff or ""),
            "poll_cutoff": str(poll_cutoff or ""),
        },
        "data_status": {
            "facts_cutoff": str(facts_cutoff or ""),
            "poll_cutoff": str(poll_cutoff or ""),
            "coverage_version": formal.coverage_name,
            "active_snapshot_id": formal.active_snapshot["snapshot_id"],
            "formal_event_count": formal.counts["formal_event_count"],
            "formal_source_count": formal.counts["formal_source_count"],
            "formal_poll_count": formal.counts["formal_poll_count"],
            "period_event_count": len(period_events),
            "background_event_count": len(background_events),
        },
        "period_events": period_compact,
        "background_events": background_compact,
        "camps": camps,
        "camp_sections": camp_sections,
        "polls": polls_section,
        "governance_issues": governance,
        "previous_state_baseline": _previous_state_baseline(formal),
        "previous_period_report": (
            {
                "period": {
                    "period_start": ctx.previous_period_report.get("period_start"),
                    "period_end": ctx.previous_period_report.get("period_end"),
                },
                "primary_thesis": (ctx.previous_period_report.get("analysis_plan") or {}).get(
                    "primary_thesis"
                ),
                "trend_outlook": (ctx.previous_period_report.get("analysis_plan") or {}).get(
                    "trend_outlook"
                ),
                "watch_indicators": (
                    ((ctx.previous_period_report.get("analysis_plan") or {}).get("trend_outlook") or {}).get(
                        "key_turning_conditions"
                    )
                    or []
                ),
                "camp_status": (ctx.previous_period_report.get("analysis_plan") or {}).get(
                    "camp_status"
                ),
            }
            if ctx.previous_period_report
            else None
        ),
        "sources": [
            {
                "source_id": s["source_id"],
                "publisher": s.get("publisher"),
                "title": s.get("title"),
                "published_at": str(s.get("published_at") or "")[:10],
                "linked_event_ids": sorted(set(source_index.get(s["source_id"], []))),
            }
            for s in sources
        ],
        "known_limitations": known_limitations,
        "do_not_infer": do_not_infer,
        "evidence_statistics": {
            "period_event_count": len(period_events),
            "background_event_count": len(background_events),
            "included_source_count": len(sources),
            "poll_gap": polls_section["poll_gap"],
        },
    }
    return pack


def render_pack_markdown(pack: dict) -> str:
    """研究包 Markdown（可直接上传 ChatGPT 的人工 Fallback 版本）。"""
    lines: list[str] = []
    add = lines.append
    p = pack["period"]
    ds = pack["data_status"]
    display_name = (pack.get("election") or {}).get("display_name") or "台南市长选举"
    report_label = (pack.get("election") or {}).get("report_label") or "台南市长选情"
    add(f"# {report_label}研判研究包（Assessment Research Pack）")
    add("")
    add("本文件全部内容来自已人工审核的正式事实底座，可独立使用：")
    add(f"上传本文件即可撰写{report_label}研判，无需访问数据库。")
    add("")
    add("## 报告周期与事实审核截止")
    add(f"- 报告周期：{p['period_start']} 至 {p['period_end']}")
    if p.get("previous_period_start"):
        add(f"- 上一周期：{p['previous_period_start']} 至 {p['previous_period_end']}")
    add(f"- 事实审核截止（facts_cutoff）：{p['facts_cutoff']}")
    add(f"- 民调截止（poll_cutoff）：{p['poll_cutoff']}")
    add(f"- 正式事件总数：{ds['formal_event_count']}；本期事件：{ds['period_event_count']}；背景事件：{ds['background_event_count']}")
    add("")

    add("## 一、本期核心事实")
    for ev in pack["period_events"]:
        actors = "、".join(ev.get("actors") or []) or "未标注"
        add(f"### [{ev['event_date']}] {ev['title']}")
        add(f"- 类型：{ev['event_type']}；人物：{actors}；事实状态：{ev['fact_status']}")
        if ev.get("in_period_subevents"):
            subs = "；".join(
                f"{s.get('subevent_date')} {s.get('description') or s.get('fact') or ''}"
                for s in ev["in_period_subevents"]
            )
            add(f"- 期内子事件：{subs}")
        if ev.get("fact_summary"):
            add(f"- 事实概要：{ev['fact_summary']}")
        for vf in ev.get("verified_facts") or []:
            add(f"- 已核实事实：{vf}")
        for st in ev.get("actor_statements") or []:
            add(f"- 相关表态：{st}")
        if ev.get("analytical_significance"):
            add(f"- 分析意义：{ev['analytical_significance']}")
        if ev.get("sources"):
            srcs = "；".join(
                f"{s['publisher']}《{s['title']}》（{s['published_at']}）"
                for s in ev["sources"]
            )
            add(f"- 来源：{srcs}")
        add("")
    if not pack["period_events"]:
        add("本期无正式事件。")
        add("")

    add("## 二、与上一期相比的新变化（状态基线差异）")
    diff = pack["previous_state_baseline"]["state_diff"]
    if diff.get("changed_dimensions"):
        add(f"- 变化维度：{'、'.join(diff['changed_dimensions'])}")
    if diff.get("unchanged_dimensions"):
        add(f"- 不变维度：{'、'.join(diff['unchanged_dimensions'])}")
    for d in diff.get("dimensions") or []:
        prev = (d.get("previous") or {}).get("value")
        cur = (d.get("current") or {}).get("value")
        if prev or cur:
            add(f"- {d.get('dimension')}：{prev} → {cur}")
    add("")

    # 阵营章节：按 pack 内 camp_sections 顺序（config 驱动，缺省台南 4 节）。
    # 与历史行为一致：每个定义阵营都输出标题（空阵营无列表项）。
    section_no = 3
    for section in pack.get("camp_sections") or DEFAULT_CAMP_SECTIONS:
        key = section.get("key") or ""
        label = section.get("label") or key
        add(f"## {section_no_cn(section_no)}、{label}")
        for ev in (pack.get("camps") or {}).get(key, []):
            add(f"- [{ev['event_date']}] {ev['title']}")
        add("")
        section_no += 1

    add(f"## {section_no_cn(section_no)}、治理议题")
    section_no += 1
    for issue in pack["governance_issues"]:
        add(f"- {issue['issue']}：涉及事件 {len(issue['event_ids'])} 件")
    if not pack["governance_issues"]:
        add("- 本期无明显治理议题进入选举讨论")
    add("")

    add(f"## {section_no_cn(section_no)}、民调")
    section_no += 1
    polls = pack["polls"]
    if polls.get("poll_gap"):
        add(f"- {polls['no_new_poll_note']}")
    add(f"- {polls['stale_note']}")
    for poll in polls["latest_polls"]:
        add(f"- {poll['pollster']}（执行 {poll['fieldwork_start']} 至 {poll['fieldwork_end']}，样本 {poll['sample_size']}）：")
        for n in poll["numbers"]:
            add(f"  - {n['option']}：{n['value']}{n['unit'] if n['unit'] else ''}")
    if polls["changes_vs_previous"]:
        add("- 与前次民调的变化：")
        for ch in polls["changes_vs_previous"]:
            add(f"  - {ch['option']}（{ch['pollster']}）：{ch['from_value']} → {ch['to_value']}（{ch['delta']:+}）")
    add("")

    add(f"## {section_no_cn(section_no)}、历史背景事件")
    section_no += 1
    for ev in pack["background_events"]:
        add(f"- [{ev['event_date']}] {ev['title']}（{ev.get('evidence_role')}）")
    add("")

    prev_report = pack.get("previous_period_report")
    add(f"## {section_no_cn(section_no)}、上一期正式报告")
    section_no += 1
    if prev_report:
        thesis = prev_report.get("primary_thesis") or {}
        if thesis.get("judgment"):
            add(f"- 上一期核心判断：{thesis['judgment']}")
        outl = prev_report.get("trend_outlook") or {}
        if outl.get("short_term"):
            add(f"- 上一期短期判断：{outl['short_term']}")
        if prev_report.get("watch_indicators"):
            add(f"- 上一期观察指标：{'、'.join(prev_report['watch_indicators'])}")
    else:
        add("- 上一期尚无正式报告，本期以上一状态基线（快照）作为比较起点。")
    add("")

    add(f"## {section_no_cn(section_no)}、证据限制")
    section_no += 1
    for lim in pack["known_limitations"]:
        add(f"- {lim}")
    if not pack["known_limitations"]:
        add("- 无额外已知限制。")
    add("")
    if pack["do_not_infer"]:
        add(f"## {section_no_cn(section_no)}、禁止推断事项")
        section_no += 1
        for dni in pack["do_not_infer"]:
            add(f"- {dni}")
        add("")

    add(f"## {section_no_cn(section_no)}、来源清单")
    for s in pack["sources"]:
        add(f"- {s['publisher']}《{s['title']}》（{s['published_at']}）")
    add("")
    add("（正文撰写时不得出现 event_id / source_id 等内部标识；本清单仅供追溯。）")
    return "\n".join(lines) + "\n"


def section_no_cn(n: int) -> str:
    """数字序号转中文：1→一 ... 11→十一（13 以内够用）。"""
    digits = "一二三四五六七八九十"
    if n <= 10:
        return digits[n - 1]
    if n < 20:
        return "十" + (digits[n - 11] if n > 10 else "")
    return str(n)


def build_pack_with_context(
    config: dict,
    root: Path,
    election_id: str,
    ctx: ResearchPackContext,
) -> dict:
    """读正式数据并构建研究包（config paths 相对 root 解析）。"""
    formal = load_formal_data(config, root, election_id)
    return build_research_pack(formal, ctx, config)
