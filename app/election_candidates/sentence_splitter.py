"""Deterministic sentence/clause splitting that preserves quotes."""

from __future__ import annotations


QUOTE_OPEN = {"「", "“", "『"}
QUOTE_CLOSE = {"」", "”", "』"}
DELIMITERS = "。；！？，、\n"


def split_clauses(text: str) -> list[str]:
    clauses: list[str] = []
    buf: list[str] = []
    depth = 0
    for ch in text or "":
        if ch in QUOTE_OPEN:
            depth += 1
        elif ch in QUOTE_CLOSE:
            depth = max(0, depth - 1)
        if ch in DELIMITERS and depth == 0:
            clause = "".join(buf).strip()
            if clause:
                clauses.append(clause)
            buf = []
        else:
            buf.append(ch)
    tail = "".join(buf).strip()
    if tail:
        clauses.append(tail)
    return clauses


def detect_quotes(text: str) -> list[dict[str, str]]:
    """Find quoted segments with an optional preceding speaker."""
    import re

    result = []
    for m in re.finditer(r"([\u4e00-\u9fffA-Za-z0-9]{1,12}?)[：:]?([「“『].+?[」”』])", text):
        speaker = m.group(1) or ""
        quote = m.group(2)
        result.append({"speaker": speaker, "quote": quote})
    return result
