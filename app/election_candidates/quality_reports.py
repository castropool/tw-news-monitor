"""Quality comparison reports between program output and the adjudication ledger."""

from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from .quality_gate import pairwise_cluster_metrics


def load_adjudication(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    return json.loads(p.read_text(encoding="utf-8")).get("articles", [])


def date_basis_stats(candidates: list[dict[str, Any]]) -> dict[str, int]:
    stats = {
        "explicit_event_date_count": 0,
        "relative_event_date_count": 0,
        "publication_inferred_date_count": 0,
        "unknown_event_date_count": 0,
    }
    for c in candidates:
        basis = c.get("event_date_basis", "")
        conf = c.get("event_date_confidence", "")
        if basis.startswith("explicit_in") and conf == "medium":
            stats["relative_event_date_count"] += 1
        elif basis.startswith("explicit_in"):
            stats["explicit_event_date_count"] += 1
        elif basis == "inferred_from_publication":
            stats["publication_inferred_date_count"] += 1
        else:
            stats["unknown_event_date_count"] += 1
    return stats


def assertion_kind_counts(candidates: list[dict[str, Any]], repo) -> dict[str, int]:
    counts = Counter()
    for c in candidates:
        for a in repo.get_assertions(c["candidate_id"]):
            counts[a["assertion_kind"]] += 1
    return {
        "observed_fact_assertions": counts.get("observed_fact", 0),
        "actor_statement_assertions": counts.get("actor_statement", 0),
        "allegation_assertions": counts.get("allegation", 0),
        "media_interpretation_assertions": counts.get("media_interpretation", 0),
        "planned_action_assertions": counts.get("planned_action", 0),
        "uncertain_report_assertions": counts.get("uncertain_report", 0),
        "unknown_assertions": counts.get("unknown", 0),
    }


def build_article_adjudication_comparison(
    program_labels: dict[str, str],
    adjudication_path: str | Path,
) -> dict[str, Any]:
    adjudication = load_adjudication(adjudication_path)
    rows = []
    agree = 0
    for a in adjudication:
        aid = str(a["article_id"])
        expected = a["relevance_label"]
        predicted = program_labels.get(aid, "irrelevant")
        rows.append(
            {
                "article_id": aid,
                "title": a["title"],
                "adjudicated_label": expected,
                "program_label": predicted,
                "consistent": expected == predicted,
            }
        )
        if expected == predicted:
            agree += 1
    return {
        "compared_article_count": len(rows),
        "agreement_count": agree,
        "agreement_rate": round(agree / max(1, len(rows)), 4),
        "rows": rows,
    }


def build_cluster_quality_report(
    program_clusters_by_article: dict[str, str],
    adjudication_path: str | Path,
) -> dict[str, Any]:
    adjudication = load_adjudication(adjudication_path)
    expected_groups: dict[str, list[str]] = {}
    for a in adjudication:
        key = a.get("expected_cluster_key") or f"single_{a['article_id']}"
        expected_groups.setdefault(key, []).append(str(a["article_id"]))
    predicted_groups: dict[str, list[str]] = {}
    for aid, key in program_clusters_by_article.items():
        predicted_groups.setdefault(key, []).append(aid)
    metrics = pairwise_cluster_metrics(
        list(expected_groups.values()), list(predicted_groups.values())
    )
    return {
        "expected_cluster_count": len(expected_groups),
        "predicted_cluster_count": len(predicted_groups),
        **metrics,
    }


def build_assertion_quality_report(
    program_kinds_by_article: dict[str, set[str]],
    adjudication_path: str | Path,
) -> dict[str, Any]:
    from .quality_gate import assertion_metrics

    adjudication = load_adjudication(adjudication_path)
    cases = [
        {
            "case_id": str(a["article_id"]),
            "expected_assertion_kinds": a.get("expected_assertion_kinds", []),
        }
        for a in adjudication
    ]
    metrics = assertion_metrics(cases, program_kinds_by_article)
    return metrics


def build_event_type_quality_report(
    program_types_by_article: dict[str, str],
    adjudication_path: str | Path,
) -> dict[str, Any]:
    from .quality_gate import event_type_accuracy

    adjudication = load_adjudication(adjudication_path)
    cases = [
        {
            "case_id": str(a["article_id"]),
            "expected_event_type": a.get("expected_event_type", ""),
        }
        for a in adjudication
    ]
    return event_type_accuracy(cases, program_types_by_article)


def build_candidate_quality_summary(
    run_stats: dict[str, Any],
    status_counts: dict[str, int],
    date_stats: dict[str, int],
    assertion_counts: dict[str, int],
    suggestion_stats: dict[str, int],
    unknown_event_type_count: int,
) -> dict[str, Any]:
    return {
        "run_stats": run_stats,
        "total_candidate_status_counts": status_counts,
        "date_stats": date_stats,
        "assertion_counts": assertion_counts,
        "suggestion_action_counts": suggestion_stats,
        "unknown_event_type_count": unknown_event_type_count,
    }


def render_all_candidate_audit(
    repo,
    run_dir: str | Path,
    config,
    adjudication_path: str | Path | None = None,
) -> Path:
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)
    adjudication = {str(a["article_id"]): a for a in load_adjudication(adjudication_path)} if adjudication_path else {}
    candidates = repo.list_candidates(limit=100000)
    by_status: dict[str, list[dict[str, Any]]] = {}
    for c in candidates:
        by_status.setdefault(c["review_status"], []).append(c)
    lines = ["# 候选质量审计预览（all candidates）", ""]
    order = ["review_required", "hold", "duplicate_candidate", "context_only", "auto_reject"]
    for status in order:
        group = by_status.get(status, [])
        lines.append(f"## {status}（{len(group)}）")
        lines.append("")
        if status == "auto_reject":
            group = group[:20]
            lines.append("（抽样展示前 20 条）")
            lines.append("")
        for c in group:
            articles = repo.get_articles(c["candidate_id"])
            assertions = repo.get_assertions(c["candidate_id"])
            sources = repo.get_sources(c["candidate_id"])
            suggestions = repo.get_duplicate_suggestions(c["candidate_id"])
            lines.append(f"### {c['candidate_id']}｜{c.get('candidate_title', '')}")
            lines.append("")
            lines.append(f"- 相关性裁决：{c.get('relevance_label', '')}")
            lines.append(f"- 聚类依据：{','.join(c.get('status_reason_codes_json', '[]') and []) or '见路由原因'}")
            lines.append(f"- 事件日期：{c.get('canonical_event_date', '')}（{c.get('event_date_basis', '')}，{c.get('event_date_confidence', '')}）")
            lines.append(f"- 事件类型：{c.get('candidate_event_type', '')}")
            lines.append(f"- 路由原因：{c.get('status_reason_codes_json', '[]')}")
            lines.append(f"- 人工裁决：")
            for aid in [a["news_article_id"] for a in articles]:
                adj = adjudication.get(str(aid))
                if adj:
                    consistent = adj["relevance_label"] == c.get("relevance_label", "")
                    lines.append(f"  - 文章 {aid}：{adj['relevance_label']}（程序 {c.get('relevance_label', '')}，{'一致' if consistent else '不一致'}）")
                else:
                    lines.append(f"  - 文章 {aid}：无裁决记录")
            lines.append("- Assertion 分区：")
            for kind in ["observed_fact", "actor_statement", "allegation", "media_interpretation", "planned_action", "uncertain_report", "unknown"]:
                items = [a for a in assertions if a["assertion_kind"] == kind]
                if items:
                    lines.append(f"  - {kind}: {len(items)}")
            source_text = ", ".join(
                f"{s['normalized_source_name']}({s['formal_match_status']})" for s in sources[:5]
            )
            lines.append(f"- 来源匹配：{source_text}")
            lines.append("- Top 5 正式事件查重：")
            for s in suggestions[:5]:
                lines.append(
                    f"  - {s['formal_event_id']}｜{s['similarity_score']}｜{s['suggested_action']}"
                )
            lines.append("")
    path = run_dir / "all_candidate_audit.md"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path
