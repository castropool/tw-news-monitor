"""Parity report between the inline ElectionClassifier and persisted logic."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .candidate_models import MatchInfo, NormalizedArticle
from .match_reader import inline_classify


def build_classifier_parity_report(
    articles: list[NormalizedArticle],
    expected_matches: dict[str, MatchInfo],
    config,
) -> dict[str, Any]:
    inline = inline_classify(articles, config)
    matched_by_both: list[str] = []
    inline_only: list[str] = []
    persisted_logic_only: list[str] = []
    conflicts: list[dict[str, Any]] = []
    for art in articles:
        aid = art.news_article_id
        got = aid in inline
        exp = aid in expected_matches
        if got and exp:
            matched_by_both.append(aid)
            a = inline[aid]
            b = expected_matches[aid]
            if set(a.matched_people) != set(b.matched_people) or set(a.matched_issues) != set(b.matched_issues):
                conflicts.append(
                    {
                        "article_id": aid,
                        "inline_people": a.matched_people,
                        "expected_people": b.matched_people,
                        "inline_issues": a.matched_issues,
                        "expected_issues": b.matched_issues,
                    }
                )
        elif got and not exp:
            inline_only.append(aid)
        elif exp and not got:
            persisted_logic_only.append(aid)
    return {
        "classifier_parity_ready": True,
        "matched_by_both": sorted(matched_by_both),
        "inline_only": sorted(inline_only),
        "persisted_logic_only": sorted(persisted_logic_only),
        "classification_conflicts": conflicts,
        "conflict_count": len(conflicts),
    }


def write_parity_report(report: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
