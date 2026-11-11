"""Provider 输出的纯格式归一化；绝不补充或改写报告语义。"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass


_FENCE_RE = re.compile(r"\A```(?:json)?\s*\r?\n?(.*?)\r?\n?```\Z", re.DOTALL | re.IGNORECASE)


class OutputNormalizationError(ValueError):
    """输出不能在纯格式边界内归一为唯一 JSON 对象。"""


@dataclass(frozen=True)
class NormalizedJSONObject:
    value: dict
    normalized_text: str
    operations: tuple[str, ...]


def _balanced_object_candidates(text: str) -> list[str]:
    candidates: list[str] = []
    start: int | None = None
    depth = 0
    in_string = False
    escaped = False
    for index, char in enumerate(text):
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"' and depth > 0:
            in_string = True
        elif char == "{":
            if depth == 0:
                start = index
            depth += 1
        elif char == "}" and depth > 0:
            depth -= 1
            if depth == 0 and start is not None:
                candidates.append(text[start : index + 1])
                start = None
    return candidates


def normalize_json_object(content: str) -> NormalizedJSONObject:
    """Parse one JSON object while allowing only deterministic formatting cleanup."""
    if not isinstance(content, str):
        raise OutputNormalizationError("provider 输出必须是文本")

    operations: list[str] = []
    text = content
    if text.startswith("\ufeff"):
        text = text.removeprefix("\ufeff")
        operations.append("removed_utf8_bom")
    stripped = text.strip()
    if stripped != text:
        operations.append("trimmed_outer_whitespace")
        text = stripped

    fence = _FENCE_RE.fullmatch(text)
    if fence:
        text = fence.group(1).strip()
        operations.append("unwrapped_single_json_fence")

    try:
        value = json.loads(text)
    except json.JSONDecodeError:
        parsed: list[tuple[str, dict]] = []
        for candidate in _balanced_object_candidates(text):
            try:
                candidate_value = json.loads(candidate)
            except json.JSONDecodeError:
                continue
            if isinstance(candidate_value, dict):
                parsed.append((candidate, candidate_value))
        if len(parsed) != 1:
            raise OutputNormalizationError(
                f"provider 输出不包含唯一、完整的 JSON 对象（候选数={len(parsed)}）"
            )
        text, value = parsed[0]
        operations.append("extracted_unique_json_object")

    if not isinstance(value, dict):
        raise OutputNormalizationError("provider 输出的 JSON 根节点不是对象")
    return NormalizedJSONObject(
        value=value,
        normalized_text=text,
        operations=tuple(operations),
    )
