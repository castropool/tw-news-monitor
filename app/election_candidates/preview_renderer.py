"""Render the read-only review queue, markdown preview and run artifacts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def _json_loads(value: Any, default: Any = None) -> Any:
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError):
        return default


def build_queue_item(
    candidate: dict[str, Any],
    articles: list[dict[str, Any]],
    assertions: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    suggestions: list[dict[str, Any]],
) -> dict[str, Any]:
    facts = [a for a in assertions if a["assertion_kind"] == "observed_fact"]
    statements = [a for a in assertions if a["assertion_kind"] == "actor_statement"]
    allegations = [a for a in assertions if a["assertion_kind"] == "allegation"]
    media = [a for a in assertions if a["assertion_kind"] == "media_interpretation"]
    planned = [a for a in assertions if a["assertion_kind"] == "planned_action"]
    unknowns = [
        a for a in assertions
        if a["assertion_kind"] in ("uncertain_report", "unknown")
    ]
    status_reasons = _json_loads(candidate.get("status_reason_codes_json"), [])
    profile = _json_loads(candidate.get("assertion_profile_json"), {})
    return {
        "candidate_id": candidate["candidate_id"],
        "candidate_title": candidate.get("candidate_title", ""),
        "canonical_event_date": candidate.get("canonical_event_date", ""),
        "event_date_basis": candidate.get("event_date_basis", ""),
        "event_date_precision": candidate.get("event_date_precision", ""),
        "event_date_confidence": candidate.get("event_date_confidence", ""),
        "candidate_event_type": candidate.get("candidate_event_type", ""),
        "primary_actor": candidate.get("primary_actor", ""),
        "candidate_summary": candidate.get("candidate_summary", ""),
        "relevance_label": candidate.get("relevance_label", ""),
        "date_flagged_inferred": bool(candidate.get("date_flagged_inferred")),
        "observed_facts": facts,
        "actor_statements": statements,
        "allegations": allegations,
        "media_interpretations": media,
        "planned_actions": planned,
        "unknowns": unknowns,
        "article_count": candidate.get("article_count", len(articles)),
        "source_count": candidate.get("source_count", len(sources)),
        "source_list": [
            {
                "source_name": s.get("normalized_source_name", ""),
                "formal_match_status": s.get("formal_match_status", ""),
                "formal_source_id": s.get("formal_source_id", ""),
            }
            for s in sources
        ],
        "articles": [
            {
                "news_article_id": a["news_article_id"],
                "title": a.get("article_title", ""),
                "url": a.get("article_url", ""),
                "source_name": a.get("source_name", ""),
                "published_at": a.get("published_at", ""),
                "is_anchor": bool(a.get("is_anchor")),
            }
            for a in articles
        ],
        "formal_duplicate_suggestions": suggestions,
        "scores": {
            "relevance_score": candidate.get("relevance_score", 0),
            "completeness_score": candidate.get("completeness_score", 0),
            "cluster_confidence": candidate.get("cluster_confidence", 0),
            "date_confidence": candidate.get("date_confidence", 0),
            "source_confidence": candidate.get("source_confidence", 0),
            "assertion_risk_score": candidate.get("assertion_risk_score", 0),
            "formal_duplicate_score": candidate.get("formal_duplicate_score", 0),
        },
        "risk_level": candidate.get("risk_level", ""),
        "risk_flags": profile.get("risk_flags", []),
        "review_status": candidate.get("review_status", ""),
        "status_reasons": status_reasons,
    }


def render_markdown(item: dict[str, Any]) -> str:
    lines: list[str] = []
    lines.append(f"## {item['candidate_id']}｜{item['candidate_title']}")
    lines.append("")
    lines.append("【候选状态】" + item["review_status"])
    lines.append("【候选日期】" + (item["canonical_event_date"] or "未知"))
    lines.append("【日期依据】" + item["event_date_basis"])
    lines.append("【日期精度】" + item["event_date_precision"])
    lines.append("【日期置信度】" + item["event_date_confidence"])
    lines.append("【事件类型】" + item["candidate_event_type"])
    lines.append("【主要人物】" + (item["primary_actor"] or "未知"))
    lines.append("【完整度】" + f"{item['scores']['completeness_score']:.2f}")
    lines.append("【风险等级】" + item["risk_level"])
    lines.append(
        "【正式库查重结果】"
        + (
            "; ".join(
                f"{s['formal_event_id']}({s['similarity_score']:.2f},{s['suggested_action']})"
                for s in item["formal_duplicate_suggestions"]
            )
            or "无建议"
        )
    )
    lines.append("")

    def section(title: str, items: list[dict[str, Any]], empty: str) -> None:
        lines.append(f"## {title}")
        lines.append("")
        if not items:
            lines.append(empty)
        else:
            for a in items:
                lines.append(f"- {a.get('assertion_text', '')}（来源文章 {a.get('evidence_article_id', '')}）")
        lines.append("")

    section("一、可观察事实", item["observed_facts"], "无可观察事实")
    section("二、人物及组织表态", item["actor_statements"], "无人物表态")
    section("三、指控和争议", item["allegations"], "无指控")
    section("四、媒体解读", item["media_interpretations"], "无媒体解读")
    section("五、不确定项", item["unknowns"] + item["planned_actions"], "无不确定项")
    lines.append("## 六、关联新闻")
    lines.append("")
    for a in item["articles"]:
        anchor = "（anchor）" if a["is_anchor"] else ""
        lines.append(f"- [{a['title']}]({a['url']})｜{a['source_name']}｜{a['published_at']}{anchor}")
    lines.append("")
    lines.append("## 七、来源")
    lines.append("")
    for s in item["source_list"]:
        lines.append(f"- {s['source_name']}｜{s['formal_match_status']}｜{s['formal_source_id'] or '未关联'}")
    lines.append("")
    lines.append("## 八、疑似重复正式事件")
    lines.append("")
    for s in item["formal_duplicate_suggestions"]:
        lines.append(
            f"- {s['formal_event_id']}｜相似度 {s['similarity_score']:.2f}｜"
            f"{s['suggested_action']}｜理由: {_json_loads(s['matching_reasons_json'], [])}"
        )
    lines.append("")
    lines.append("## 九、需要人工裁决的问题")
    lines.append("")
    for reason in item["status_reasons"]:
        lines.append(f"- {reason}")
    lines.append("")
    return "\n".join(lines)


def render_run_outputs(repo, run_id: str, output_dir: str | Path, config) -> dict[str, Any]:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    candidates = repo.list_candidates(limit=100000)

    events_path = output_dir / config.get("outputs.candidate_events", "candidate_events.jsonl")
    articles_path = output_dir / config.get("outputs.candidate_event_articles", "candidate_event_articles.jsonl")
    assertions_path = output_dir / config.get("outputs.candidate_assertions", "candidate_assertions.jsonl")
    sources_path = output_dir / config.get("outputs.candidate_sources", "candidate_sources.jsonl")
    suggestions_path = output_dir / config.get("outputs.formal_duplicate_suggestions", "formal_duplicate_suggestions.jsonl")

    queue_items: dict[str, list[dict[str, Any]]] = {
        "review_required": [],
        "hold": [],
        "duplicate_candidate": [],
        "auto_reject": [],
        "context_only": [],
    }
    with open(events_path, "w", encoding="utf-8") as fev, \
         open(articles_path, "w", encoding="utf-8") as fart, \
         open(assertions_path, "w", encoding="utf-8") as fasr, \
         open(sources_path, "w", encoding="utf-8") as fsrc, \
         open(suggestions_path, "w", encoding="utf-8") as fdup:
        for c in candidates:
            fev.write(json.dumps(c, ensure_ascii=False) + "\n")
            articles = repo.get_articles(c["candidate_id"])
            assertions = repo.get_assertions(c["candidate_id"])
            sources = repo.get_sources(c["candidate_id"])
            suggestions = repo.get_duplicate_suggestions(c["candidate_id"])
            for a in articles:
                fart.write(json.dumps(a, ensure_ascii=False) + "\n")
            for a in assertions:
                fasr.write(json.dumps(a, ensure_ascii=False) + "\n")
            for s in sources:
                fsrc.write(json.dumps(s, ensure_ascii=False) + "\n")
            for s in suggestions:
                fdup.write(json.dumps(s, ensure_ascii=False) + "\n")
            item = build_queue_item(c, articles, assertions, sources, suggestions)
            status = c["review_status"]
            if status in queue_items:
                queue_items[status].append(item)

    def write_json(name: str, payload: Any) -> Path:
        p = output_dir / name
        p.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return p

    review_path = write_json(config.get("outputs.review_queue", "review_queue.json"), queue_items["review_required"])
    hold_path = write_json(config.get("outputs.hold_queue", "hold_queue.json"), queue_items["hold"])
    duplicate_path = write_json(
        config.get("outputs.duplicate_queue", "duplicate_queue.json"), queue_items["duplicate_candidate"]
    )
    context_only_path = write_json("context_only_queue.json", queue_items["context_only"])
    auto_reject_summary = {
        "count": len(queue_items["auto_reject"]),
        "items": [
            {"candidate_id": i["candidate_id"], "title": i["candidate_title"], "reasons": i["status_reasons"]}
            for i in queue_items["auto_reject"]
        ],
    }
    auto_path = write_json(config.get("outputs.auto_reject_summary", "auto_reject_summary.json"), auto_reject_summary)

    md_parts = []
    for item in queue_items["review_required"]:
        md_parts.append(render_markdown(item))
    review_md_path = output_dir / config.get("outputs.review_queue_md", "review_queue.md")
    review_md_path.write_text("\n".join(md_parts), encoding="utf-8")

    return {
        "review_queue": str(review_path),
        "review_queue_md": str(review_md_path),
        "hold_queue": str(hold_path),
        "duplicate_queue": str(duplicate_path),
        "context_only_queue": str(context_only_path),
        "auto_reject_summary": str(auto_path),
        "candidate_events": str(events_path),
        "candidate_event_articles": str(articles_path),
        "candidate_assertions": str(assertions_path),
        "candidate_sources": str(sources_path),
        "formal_duplicate_suggestions": str(suggestions_path),
        "candidate_count": len(candidates),
    }
