"""县市地区解析：别名归一 + 主/多地区判定。

匹配原则（配置驱动，非纯 contains）：
  - 归一采用"最长名字优先"：短别名（如"北市"）天然不会误吞更长的
    标准名（如"新北市長"会先命中"新北市"）。
  - 多县市检测只看标准名（别名不参与），避免"北市"类短别名误判。

归属顺序：标题 > 摘要。
规则：
  1) 标题命中 >=3 个标准名 → national（跨县市整体比较，不复制）
  2) 标题命中 2 个标准名 → multi_region（regions=两标准名）
  3) 标题命中 1 个 → local
  4) 标题无命中：摘要命中 >=2 → multi_region；1 个 → local
  5) 以上皆无：实体关联县市（仅辅助）取首个 → local
"""

import logging

from .hanzi_utils import to_traditional

logger = logging.getLogger(__name__)


def _build_region_names(regions_config: dict) -> list[tuple[str, list[str]]]:
    """返回 [(标准名, [标准名+别名...])] 保持配置顺序。"""
    aliases_config = (regions_config or {}).get("aliases", {}) or {}
    result: list[tuple[str, list[str]]] = []
    for canonical, alias_list in aliases_config.items():
        names = [canonical] + [a for a in (alias_list or []) if isinstance(a, str)]
        result.append((canonical, names))
    return result


def _sorted_names(region_names: list[tuple[str, list[str]]]) -> list[tuple[str, str]]:
    """全部 (名字, 标准名)，按名字长度倒序（同长保持配置顺序）。"""
    pairs: list[tuple[str, str]] = []
    for canonical, names in region_names:
        for name in names:
            if name:
                pairs.append((name, canonical))
    # 稳定排序：长度倒序，同长维持原相对顺序
    pairs.sort(key=lambda p: len(p[0]), reverse=True)
    return pairs


def _matched_spans(
    text: str, regions_config: dict
) -> list[tuple[str, str, int, int]]:
    """返回 (名字, 标准名, start, end) 命中列表。

    名字包含标准名与别名；文本先转繁体（配置词表为繁体）。
    """
    norm_text = to_traditional(text)
    spans: list[tuple[str, str, int, int]] = []
    for name, canonical in _sorted_names(_build_region_names(regions_config)):
        start = 0
        while True:
            pos = norm_text.find(name, start)
            if pos < 0:
                break
            spans.append((name, canonical, pos, pos + len(name)))
            start = pos + 1
    return spans


def _resolve_overlaps(
    spans: list[tuple[str, str, int, int]]
) -> list[tuple[str, str, int, int]]:
    """重叠裁决：保留最长命中；同长保留最左（出现最早）。

    例："竹北市長人選" 中"竹北"与"北市"同长重叠，取最左的"竹北"
    （新竹縣），避免误归台北市。
    """
    kept: list[tuple[str, str, int, int]] = []
    # 长度降序、起点升序、配置顺序稳定 → 先处理更长/更左的命中
    ordered = sorted(spans, key=lambda s: (-(s[3] - s[2]), s[2]))
    for span in ordered:
        _, _, start, end = span
        overlapped = False
        for _, _, k_start, k_end in kept:
            if start < k_end and k_start < end:  # 区间重叠
                overlapped = True
                break
        if not overlapped:
            kept.append(span)
    # 按文本出现顺序返回
    kept.sort(key=lambda s: (s[2], -(s[3] - s[2])))
    return kept


def normalize_region(text: str, regions_config: dict) -> str | None:
    """把文本中出现的第一个县市名归一为标准名；无命中返回 None。

    使用重叠裁决（最长优先、同长取最左），避免短别名误吞
    （如"竹北市"里的"北市"不应归台北市）。
    """
    if not text or not regions_config:
        return None
    resolved = _resolve_overlaps(_matched_spans(text, regions_config))
    if not resolved:
        return None
    return resolved[0][1]  # 最左命中的标准名


def _standard_names_in(text: str, regions_config: dict) -> list[str]:
    """返回文本中命中的标准县市名（仅标准名），按首次出现位置排序去重。"""
    if not text or not regions_config:
        return []
    norm_text = to_traditional(text)
    found: list[tuple[int, str]] = []
    region_names = _build_region_names(regions_config)
    for canonical, _names in region_names:
        pos = norm_text.find(canonical)
        if pos >= 0:
            found.append((pos, canonical))
    found.sort(key=lambda x: x[0])
    return [c for _, c in found]


def distinct_regions_in(text: str, regions_config: dict) -> list[str]:
    """返回文本中命中的不同县市（标准名直现或别名命中），去重保序。

    用于"台北新北桃园市长选情"这类别名并列 → 判定跨多县市（national）。
    短别名（北市/高市/中市）在并列语境下不会单发命中过长文本，由
    _resolve_overlaps 保底。
    """
    if not text or not regions_config:
        return []
    resolved = _resolve_overlaps(_matched_spans(text, regions_config))
    # 解析后每个保留命中归一为标准名，去重保序
    seen: list[str] = []
    for _name, canonical, _start, _end in sorted(resolved, key=lambda s: s[2]):
        if canonical not in seen:
            seen.append(canonical)
    return seen


def is_multi_city_national(text: str, regions_config: dict) -> bool:
    """文本中 >=3 个不同县市并列（别名或标准名）→ 跨县市整体比较。"""
    return len(distinct_regions_in(text, regions_config)) >= 3


def resolve_regions(
    title: str,
    summary: str,
    regions_config: dict,
    entity_regions: list[str] | None = None,
    region_anchor: str | None = None,
) -> tuple[str, str | None, list[str]]:
    """返回 (scope, region, regions)。

    见模块 docstring 的规则 1-5。region_anchor 为已通过别名归一得到的
    标准县市名（标题/摘要首个命中），用于别名出现而标准名未直现时
    （如"竹北"→新竹縣）锚定归属。
    """
    if not regions_config:
        return "", None, []
    entity_regions = entity_regions or []

    title_names = _standard_names_in(title or "", regions_config)
    if len(title_names) >= 3:
        return "national", None, []
    if len(title_names) == 2:
        return "multi_region", title_names[0], list(title_names)
    if len(title_names) == 1:
        return "local", title_names[0], list(title_names)

    # 标题无标准名直现但别名已归一 → 用该锚点
    if region_anchor:
        return "local", region_anchor, [region_anchor]

    summary_names = _standard_names_in(summary or "", regions_config)
    if len(summary_names) >= 2:
        return "multi_region", summary_names[0], list(summary_names)
    if len(summary_names) == 1:
        return "local", summary_names[0], list(summary_names)

    if entity_regions:
        unique = list(dict.fromkeys(entity_regions))
        return "local", unique[0], [unique[0]]
    return "", None, []


# 全国性场景词中，纯蓝白合作类（弱全国信号）不应压过具体县市：
# 如"藍白協調竹北市長人選"归新竹縣而非全局动向。
WEAK_NATIONAL_TERMS = ("藍白", "藍白合", "跨黨")


def strong_national_terms_hit(text: str, national_scenes: list[str]) -> list[str]:
    """返回命中的"强全国场景词"（排除弱全国信号）。"""
    if not text:
        return []
    norm_text = to_traditional(text)
    return [
        term for term in (national_scenes or [])
        if term and term not in WEAK_NATIONAL_TERMS and term in norm_text
    ]


def national_scene_hit(text: str, national_scenes: list[str]) -> bool:
    """是否有任一全国场景词命中（含弱信号，用于无地区锚点时的兜底）。"""
    if not text:
        return False
    norm_text = to_traditional(text)
    return any(term and term in norm_text for term in (national_scenes or []))
