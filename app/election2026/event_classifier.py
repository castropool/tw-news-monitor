"""事件类型识别。

按 config/election_2026.yaml event_type_map 键顺序优先匹配；
多命中取第一个，无命中返回 "other"。
"""

from typing import Iterable


def classify_event_type(text: str, event_type_map: dict) -> str:
    """返回事件类型枚举值；无命中 other。"""
    if not text:
        return "other"
    for event_type, terms in (event_type_map or {}).items():
        if not isinstance(terms, list):
            continue
        if any(term and term in text for term in terms):
            return event_type
    return "other"


def classify_event_tags(
    text: str, event_type_map: dict, primary: str | None = None
) -> list[str]:
    """Return all matching event types, preserving configured map order."""
    if not text:
        return [primary] if primary and primary != "other" else []
    tags: list[str] = []
    for event_type, terms in (event_type_map or {}).items():
        if not isinstance(terms, list):
            continue
        if any(term and term in text for term in terms):
            if event_type not in tags and event_type != "other":
                tags.append(event_type)
    if primary and primary != "other" and primary not in tags:
        tags.insert(0, primary)
    return tags
