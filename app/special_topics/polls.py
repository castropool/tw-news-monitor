"""Deterministic poll-release analyzer.

Extracts what a reader of the digest needs from a published poll item's
title and teaser: which polling organization released it, the headline
percentage figures, sample size, margin of error and fieldwork window.
Nothing is inferred beyond the text; missing fields stay ``None``.
"""

from __future__ import annotations

import re
from typing import Any

_PERCENT = r"(\d{1,3}(?:\.\d{1,2})?)\s*(?:%|％|趴)"
_GENERIC_FIGURE_RE = re.compile(r"([一-鿿A-Za-z]{2,6})\s*(?:為|为|有|達|达|約|约|僅|仅|是)?\s*" + _PERCENT)
_SAMPLE_RE = re.compile(
    r"(?:有效樣本|有效样本|樣本數|样本数|成功訪問|成功访问|成功訪問了|共訪問|共访问|訪問了|访问了|有效問卷|有效问卷)"
    r"[^0-9]{0,4}([0-9][0-9,]{2,6})"
)
_MARGIN_RE = re.compile(r"(?:抽樣誤差|抽样误差|誤差|误差)[^0-9]{0,10}(\d{1,2}(?:\.\d{1,2})?)\s*(?:個|个)?\s*(?:百分點|百分点|%|％)")
_FIELDWORK_RE = re.compile(
    r"(?:(\d{4})年)?(\d{1,2})月(\d{1,2})日?\s*(?:至|到|～|~|－|-|—)\s*(?:(\d{1,2})月)?(\d{1,2})日"
)


def _haystack(title: str | None, summary: str | None) -> str:
    return f"{title or ''}\n{summary or ''}"


def matched_keywords(text: str, keywords: list[str]) -> list[str]:
    return [kw for kw in keywords if kw and kw in text]


def identify_pollster(text: str, pollsters: list[dict[str, Any]]) -> str | None:
    """Return the configured pollster whose alias appears earliest in the text."""
    best: tuple[int, int, str] | None = None
    for pollster in pollsters:
        for alias in pollster.get("aliases") or []:
            pos = text.find(alias)
            if pos < 0:
                continue
            # earliest mention wins; longer alias breaks ties
            key = (pos, -len(alias), pollster["name"])
            if best is None or key < best:
                best = key
    return best[2] if best else None


def extract_figures(text: str, labels: list[str], max_figures: int = 6) -> list[dict[str, Any]]:
    """Extract ``label → percent`` pairs, labelled figures first."""
    figures: list[dict[str, Any]] = []
    seen_spans: list[tuple[int, int]] = []

    def _overlaps(span: tuple[int, int]) -> bool:
        return any(not (span[1] <= s[0] or span[0] >= s[1]) for s in seen_spans)

    # Longest labels first so "不滿意" wins over "滿意".
    for label in sorted(labels, key=len, reverse=True):
        pattern = re.compile(re.escape(label) + r"[^0-9%％\n]{0,6}?" + _PERCENT)
        for match in pattern.finditer(text):
            span = (match.start(), match.end())
            if _overlaps(span):
                continue
            seen_spans.append(span)
            subject = _standalone_subject(text, match.start())
            figures.append({
                "label": f"{subject}{label}" if subject else label,
                "value": float(match.group(1)),
                "pos": match.start(),
            })
    for match in _GENERIC_FIGURE_RE.finditer(text):
        span = (match.start(), match.end())
        if _overlaps(span):
            continue
        seen_spans.append(span)
        figures.append({"label": match.group(1), "value": float(match.group(2)), "pos": match.start()})
    figures = [f for f in figures if 0 <= f["value"] <= 100]
    figures.sort(key=lambda f: f["pos"])
    for figure in figures:
        figure.pop("pos", None)
    return figures[:max_figures]


_SUBJECT_RE = re.compile(r"(?:^|[^一-鿿])([一-鿿]{2,3})$")
_SUBJECT_STOPWORDS = {"民調", "民调", "調查", "调查", "最新", "整體", "整体", "政府", "施政", "顯示", "显示"}


def _standalone_subject(text: str, label_start: int) -> str:
    """Return a 2-3 char subject (e.g. a name) directly before a label.

    Only a short CJK token delimited by punctuation/space/start is taken, so
    running prose such as "國政民調侯友宜支持度" yields no subject rather than
    a wrong one.
    """
    match = _SUBJECT_RE.search(text[max(0, label_start - 4):label_start])
    if not match:
        return ""
    subject = match.group(1)
    return "" if subject in _SUBJECT_STOPWORDS else subject


def extract_sample_size(text: str) -> int | None:
    match = _SAMPLE_RE.search(text)
    if not match:
        return None
    try:
        value = int(match.group(1).replace(",", ""))
    except ValueError:
        return None
    return value if 100 <= value <= 1_000_000 else None


def extract_margin_of_error(text: str) -> float | None:
    match = _MARGIN_RE.search(text)
    if not match:
        return None
    value = float(match.group(1))
    return value if 0 < value <= 20 else None


def extract_fieldwork(text: str) -> str | None:
    match = _FIELDWORK_RE.search(text)
    if not match:
        return None
    year, m1, d1, m2, d2 = match.groups()
    end_month = m2 or m1
    prefix = f"{year}年" if year else ""
    return f"{prefix}{int(m1)}月{int(d1)}日至{int(end_month)}月{int(d2)}日"


def analyze_poll(title: str | None, summary: str | None, config: dict[str, Any]) -> dict[str, Any]:
    """Return an explainable poll annotation for one article."""
    text = _haystack(title, summary)
    required = matched_keywords(text, config.get("required_keywords") or [])
    excluded = matched_keywords(text, config.get("exclude_keywords") or [])
    pollster = identify_pollster(text, config.get("pollsters") or [])
    relevant = bool(required) and not excluded
    return {
        "relevant": relevant,
        "reason": (
            "excluded:" + ",".join(excluded) if excluded
            else ("matched:" + ",".join(required[:5]) if required else "no_poll_keyword")
        ),
        "pollster": pollster,
        "figures": extract_figures(text, config.get("figure_labels") or [], int(config.get("max_figures") or 6)),
        "sample_size": extract_sample_size(text),
        "margin_of_error": extract_margin_of_error(text),
        "fieldwork": extract_fieldwork(text),
        "matched_keywords": required,
    }


def format_poll_line(annotation: dict[str, Any] | None) -> str:
    """One-line Chinese summary used by the text digest and Word."""
    if not annotation:
        return ""
    parts: list[str] = []
    if annotation.get("pollster"):
        parts.append(f"民调机构：{annotation['pollster']}")
    figures = annotation.get("figures") or []
    if figures:
        rendered = "、".join(f"{f['label']}{_fmt_pct(f['value'])}" for f in figures)
        parts.append(f"关键数据：{rendered}")
    if annotation.get("sample_size"):
        parts.append(f"样本：{annotation['sample_size']}")
    if annotation.get("margin_of_error"):
        parts.append(f"误差：±{_fmt_num(annotation['margin_of_error'])}个百分点")
    if annotation.get("fieldwork"):
        parts.append(f"调查期间：{annotation['fieldwork']}")
    return "｜".join(parts)


def _fmt_num(value: float) -> str:
    return f"{value:g}"


def _fmt_pct(value: float) -> str:
    return f"{value:g}%"
