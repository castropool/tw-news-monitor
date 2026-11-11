"""2026 九合一新闻识别主分类器。

评分模型（分值可由 config 调整）：
  strong(45) 命中 → 直接判正（无需组合）
  strong(45) + auxiliary(25)      = 70 → 直接判正
  entity(20) + region(15) + 语境  = 50 → 疑似区须保守子规则
  entity(20) + region(15)         = 35 < 40 → 不判正（防日常施政误判）

判定流程（确定性纯函数，无 LLM / 网络依赖）：
  1) 负向词一票否决（总统/立委/党主席/外国/历史选举等）→ 非九合一
  2) 强词/辅助词/实体/地区/语境评分 → score
  3) score >= direct(60)  → is_election=True
  4) review(40) <= score < direct(60) → 疑似区：须同时满足
     （实体 + 地区 + 任一辅助/语境）或 （辅助词 >= 2 + 地区），否则判负
  5) score < review(40) → 非九合一
  6) 判正后做 scope/region/regions/event_type 归类
"""

import logging

from .models import ElectionAnnotation
from .entity_loader import build_entity_index, find_matched_entities
from .region_resolver import (
    WEAK_NATIONAL_TERMS,
    is_multi_city_national,
    normalize_region,
    resolve_regions,
    strong_national_terms_hit,
    national_scene_hit,
)
from .event_classifier import classify_event_tags, classify_event_type
from .hanzi_utils import to_traditional

logger = logging.getLogger(__name__)

# 选举行为语境词（命中表示"有选举行为而非日常施政"，摘要/正文用）
_ACTION_CONTEXT_TERMS = (
    "競選團隊", "競選總部", "競選", "政見", "輔選", "造勢", "拜票",
    "掃街", "選戰", "選情", "初選", "徵召", "提名", "攻防",
    "挑戰", "連任", "接班", "佈局", "協調", "整合", "人選", "選舉",
)


def _hit_terms(text: str, terms: list[str]) -> list[str]:
    """返回 text 中命中的词（text 需已转繁体；保出现顺序、去重）。"""
    if not text or not terms:
        return []
    seen: list[str] = []
    for term in terms:
        if term and term in text and term not in seen:
            seen.append(term)
    return seen


def _score_article(
    title: str,
    summary: str,
    config: dict,
    matched_entities: list,
    region_in_title: bool,
    region_in_summary: bool,
    national_anchor: bool,
) -> tuple[int, list[str]]:
    """返回 (score, matched_terms)。"""
    scoring = config.get("scoring", {})
    combined = f"{title or ''} {summary or ''}"

    strong = _hit_terms(combined, config.get("strong_terms", []))
    auxiliary = _hit_terms(combined, config.get("auxiliary_terms", []))

    score = 0
    matched_terms: list[str] = []
    if strong:
        score += len(strong) * int(scoring.get("strong_term", 45))
        matched_terms.extend(strong)
    if auxiliary:
        score += len(auxiliary) * int(scoring.get("auxiliary_term", 25))
        matched_terms.extend(auxiliary)
    if matched_entities:
        score += int(scoring.get("entity", 20))
    if region_in_title:
        score += int(scoring.get("region_in_title", 15))
    if national_anchor:
        # 全国场景词（六都/全党/提名机制等）作为无地区锚点时的等价信号
        score += int(scoring.get("region_in_title", 15))
    if (region_in_title or region_in_summary or national_anchor) and (
        matched_entities or auxiliary
    ):
        score += int(scoring.get("action_context", 15))

    return score, matched_terms


def _finalize_scope(
    title: str,
    summary: str,
    regions_config: dict,
    config: dict,
    entity_regions: list[str],
    region_anchor: str | None = None,
) -> tuple[str, str | None, list[str]]:
    """判正后的 scope/region/regions 终判。

    region_anchor：标题/摘要首个命中的标准县市名（含别名归一结果），
    用于标准名未直现而别名命中时的归属锚定。
    """
    combined = f"{title or ''} {summary or ''}"
    national_scenes = config.get("national_scenes", [])

    # 全局动向强信号：命中强全国场景词（六都/全党/中央党部/提名机制等）
    if strong_national_terms_hit(combined, national_scenes):
        return "national", None, []

    # 跨 >=3 县市并列（含别名：台北/新北/桃园）→ 整体比较归全局动向
    if is_multi_city_national(combined, regions_config):
        return "national", None, []

    # 纯地区规则（标题 > 摘要；实体仅辅助）
    scope, region, regions = resolve_regions(
        title, summary, regions_config, entity_regions, region_anchor=region_anchor,
    )
    if scope:
        return scope, region, regions
    # 已判正但无任何县市锚点 → 全局动向（如"九合一政党民调"、全国性议题）
    return "national", None, []


def classify_article(
    title: str,
    summary: str = "",
    config: dict | None = None,
    entities: dict | None = None,
    entity_index: list | None = None,
) -> ElectionAnnotation:
    """判断单条新闻是否属于 2026 九合一选举。

    纯函数：相同输入永远返回相同结果（config/entities 视为输入一部分）。
    config 缺省或 disabled 时返回 is_election=False。
    """
    if not title:
        return ElectionAnnotation(reason="empty_title")
    if config is None or not config.get("enabled", False):
        return ElectionAnnotation(reason="disabled")

    title_text = to_traditional(title)
    summary_text = to_traditional(summary or "")
    combined = f"{title_text} {summary_text}"

    # 1) hard negative 一票否决；soft negative 只降低分数。
    hard_negative_terms = config.get("hard_negative_terms")
    if hard_negative_terms is None:
        hard_negative_terms = config.get("negative_terms", [])
    negative = _hit_terms(combined, hard_negative_terms)
    if negative:
        logger.info(
            '[election] rejected reason=%s title="%s"',
            f"rejected:{negative[0]}", title_text,
        )
        return ElectionAnnotation(reason=f"rejected:{negative[0]}")

    # 1b) 政务抑制：现任首长例行政务（视察/勘灾/主持…）不是选举行为。
    # 仅当文本没有强选举词时抑制，避免"市长视导"误判。
    strong_all = _hit_terms(combined, config.get("strong_terms", []))
    if not strong_all:
        routine = _hit_terms(combined, config.get("routine_governance_terms", []))
        if routine:
            logger.info(
                '[election] rejected reason=%s title="%s"',
                f"rejected:routine_governance:{routine[0]}", title_text,
            )
            return ElectionAnnotation(reason=f"rejected:routine_governance:{routine[0]}")

    # 2) 实体（仅辅助证据）
    if entity_index is None:
        entity_index = build_entity_index(entities or {"candidates": {}})
    matched_entities = find_matched_entities(combined, entity_index)
    entity_regions = [
        region
        for entity in matched_entities
        for region in entity.regions
    ]

    # 3) 地区（先归一标题/摘要中的别名）
    regions_config = config.get("regions", {})
    title_region = normalize_region(title_text, regions_config)
    summary_region = None if title_region else normalize_region(summary_text, regions_config)
    region_name = title_region or summary_region
    region_in_title = title_region is not None
    region_in_summary = summary_region is not None

    # 4) 评分（national_anchor：无县市锚点时的全国场景等价信号；
    # 弱全国词（蓝白/蓝白合）仅在无具体县市锚点时视为全国信号）
    weak_national_only = bool(
        national_scene_hit(combined, config.get("national_scenes", []))
        and not strong_national_terms_hit(combined, config.get("national_scenes", []))
        and region_name is None
        and not is_multi_city_national(combined, regions_config)
    )
    national_anchor = bool(
        strong_national_terms_hit(combined, config.get("national_scenes", []))
        or is_multi_city_national(combined, regions_config)
        or weak_national_only
    )
    score, matched_terms = _score_article(
        title_text, summary_text, config, matched_entities,
        region_in_title, region_in_summary, national_anchor,
    )
    soft_negative = _hit_terms(combined, config.get("soft_negative_terms", []))
    if soft_negative:
        penalty = max(0, int(config.get("soft_negative_penalty", 10)))
        score = max(0, score - penalty)
        matched_terms.extend(soft_negative)
    direct = int(config.get("thresholds", {}).get("direct", 60))
    review = int(config.get("thresholds", {}).get("review", 40))

    # 5) 阈值判定
    if score < review:
        return ElectionAnnotation(confidence=score, reason="below_review")

    if score < direct:
        # 疑似区保守子规则：宁可漏报不误报
        auxiliary = _hit_terms(combined, config.get("auxiliary_terms", []))
        has_anchor = bool(region_name or national_anchor)
        has_entity_and_anchor_and_ctx = bool(
            matched_entities and has_anchor
            and (auxiliary or any(t in summary_text for t in _ACTION_CONTEXT_TERMS))
        )
        has_double_aux_and_anchor = bool(
            has_anchor and len(auxiliary) >= 2
        )
        # 全国性场景（六都/全党/中执会等）本身具选举特异性：任一辅助词即判正
        has_national_anchor_and_aux = bool(
            national_anchor and auxiliary
        )
        if not (
            has_entity_and_anchor_and_ctx
            or has_double_aux_and_anchor
            or has_national_anchor_and_aux
        ):
            logger.info(
                '[election] review score=%d scope=%s region=%s title="%s"',
                score, "", region_name or "", title_text,
            )
            return ElectionAnnotation(
                confidence=score,
                matched_terms=matched_terms,
                reason="review_rejected",
            )
        scope, region, regions = _finalize_scope(
            title_text, summary_text, regions_config, config, entity_regions,
            region_anchor=region_name,
        )
        event_type = classify_event_type(combined, config.get("event_type_map", {}))
        event_tags = classify_event_tags(
            combined, config.get("event_type_map", {}), event_type
        )
        logger.info(
            '[election] matched=true score=%d scope=%s region=%s type=%s title="%s"',
            score, scope, region or "", event_type, title_text,
        )
        return ElectionAnnotation(
            is_election=True, scope=scope, region=region, regions=regions,
            event_type=event_type, confidence=score,
            matched_terms=matched_terms, event_tags=event_tags,
            reason="review_matched",
        )

    # score >= direct：直接判正
    scope, region, regions = _finalize_scope(
        title_text, summary_text, regions_config, config, entity_regions,
        region_anchor=region_name,
    )
    event_type = classify_event_type(combined, config.get("event_type_map", {}))
    event_tags = classify_event_tags(
        combined, config.get("event_type_map", {}), event_type
    )
    logger.info(
        '[election] matched=true score=%d scope=%s region=%s type=%s title="%s"',
        score, scope, region or "", event_type, title_text,
    )
    return ElectionAnnotation(
        is_election=True, scope=scope, region=region, regions=regions,
        event_type=event_type, confidence=score,
        matched_terms=matched_terms, event_tags=event_tags, reason="matched",
    )


def classify_articles(
    articles,
    config: dict | None = None,
    entities: dict | None = None,
) -> dict[str, ElectionAnnotation]:
    """批量分类：返回 {article.url: ElectionAnnotation}（仅含 is_election 的）。"""
    if config is None or not config.get("enabled", False):
        return {}
    entity_index = build_entity_index(entities or {"candidates": {}})
    result: dict[str, ElectionAnnotation] = {}
    for article in articles:
        ann = classify_article(
            article.title,
            article.summary or "",
            config=config,
            entity_index=entity_index,
        )
        if ann.is_election:
            result[article.url] = ann
    return result
