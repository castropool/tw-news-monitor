"""Deterministic analyzer for underground-betting developments.

Classifies a publicly reported item (title + teaser) into explainable
subtypes such as election betting, sports betting, online gambling,
law-enforcement action and money laundering, and flags whether it is
election-related.  The output only restates what the text says.
"""

from __future__ import annotations

from typing import Any

SUBTYPE_LABELS = {
    "election_betting": "选举赌盘",
    "sports_betting": "运动签赌",
    "online_gambling": "线上博弈",
    "enforcement": "查缉执法",
    "money_laundering": "洗钱金流",
}


def _matches(text: str, words: list[str]) -> list[str]:
    return [word for word in words if word and word in text]


def analyze_betting(title: str | None, summary: str | None, config: dict[str, Any]) -> dict[str, Any]:
    text = f"{title or ''}\n{summary or ''}"
    required = _matches(text, config.get("required_keywords") or [])
    excluded = _matches(text, config.get("exclude_keywords") or [])
    election_hits = _matches(text, config.get("election_keywords") or [])
    subtypes: list[str] = []
    for name, words in (config.get("subtypes") or {}).items():
        if _matches(text, words):
            subtypes.append(name)
    election_related = bool(required) and (bool(election_hits) or "election_betting" in subtypes)
    if election_related and "election_betting" not in subtypes:
        subtypes.insert(0, "election_betting")
    relevant = bool(required) and not excluded
    return {
        "relevant": relevant,
        "reason": (
            "excluded:" + ",".join(excluded) if excluded
            else ("matched:" + ",".join(required[:5]) if required else "no_betting_keyword")
        ),
        "election_related": election_related,
        "subtypes": subtypes,
        "matched_keywords": required,
        "election_keywords": election_hits,
    }


def format_betting_line(annotation: dict[str, Any] | None) -> str:
    if not annotation:
        return ""
    parts: list[str] = []
    labels = [SUBTYPE_LABELS.get(s, s) for s in annotation.get("subtypes") or []]
    if labels:
        parts.append("类型：" + "、".join(labels))
    if annotation.get("election_related"):
        parts.append("涉及选举：是")
    return "｜".join(parts)
