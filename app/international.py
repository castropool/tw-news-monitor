"""国际媒体免费监测层 Phase I — 相关度过滤与跨媒体事件去重（纯函数模块）。

本模块不接 main 流程、不写数据库、不访问网络；由后续任务（main / Word
接线）调用。配置来自 ``config/international_media.yaml``：缺文件/损坏时
返回禁用安全默认（与 content_filter 容错风格一致），绝不抛异常崩主流程。

公开 API：
- ``load_international_config(path=None) -> dict``
- ``is_international_media(source_name, config) -> bool``
- ``display_name(source_name, config) -> str``
- ``word_contains(text, keyword) -> bool``（词边界/短语匹配帮助函数）
- ``classify_international(title, summary, source_name, config) -> InternationalClassification``
- ``filter_international(articles, config) -> (included, excluded)``
- ``normalize_tokens(text, synonyms=None) -> set[str]``（去重归一化）
- ``title_similarity(a, b, synonyms=None) -> float``（归一化 token 集 Jaccard）
- ``dedupe_international_for_digest(articles, config) -> (canonical, coverage)``

注意：``Article`` 是 ``eq=True`` 的 dataclass（不可哈希），因此 coverage 的键
使用 canonical 文章的 URL（``dict[str, list[Article]]``），而非 Article 对象。
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from .models import Article

logger = logging.getLogger(__name__)

DEFAULT_INTERNATIONAL_CONFIG_PATH = "config/international_media.yaml"

# tier 合法值
TIERS = ("taiwan_direct", "china_related", "us_international", "other")

# topic 合法值（best-effort 内部分类，不持久化）
TOPICS = (
    "military",
    "diplomacy",
    "policy",
    "sanctions",
    "politics",
    "economy",
    "technology",
    "semiconductor",
    "trade",
    "taiwan",
    "cross_strait",
    "china",
    "us_taiwan",
    "international",
)

# 禁用安全默认（缺文件/损坏时返回）
DISABLED_CONFIG: dict[str, Any] = {
    "enabled": False,
    "display_names": {},
    "tier1_international_media": [],
    "relevance_keywords": {},
    "dedup": {
        "similarity_threshold": 0.70,
        "window_hours": 24,
        "source_priority": ["Reuters", "Financial Times", "Wall Street Journal", "Bloomberg"],
        "core_words": [],
        "synonyms": {},
    },
    "source_bonus": {"tier1": 3},
}

# canonical 并列时的来源优先级（与 config 中 dedup.source_priority 同步）
DEFAULT_SOURCE_PRIORITY = ("Reuters", "Financial Times", "Wall Street Journal", "Bloomberg")

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_DOTTED_ACRONYM_RE = re.compile(r"\b(?:[a-z]\.){2,}", re.IGNORECASE)
_RELEVANCE_KEY_GROUPS = (
    "taiwan_direct",
    "china_related",
    "us_international",
    "china_relevant_cross",
)


def _is_number(value: Any) -> bool:
    return (
        isinstance(value, (int, float))
        and not isinstance(value, bool)
        and math.isfinite(float(value))
    )


def _valid_string_list(value: Any) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) for item in value)


def _valid_string_mapping(value: Any) -> bool:
    return (
        isinstance(value, dict)
        and all(isinstance(key, str) and isinstance(item, str) for key, item in value.items())
    )


def _validate_config_shape(config: dict[str, Any]) -> str | None:
    """Validate the small YAML schema before enabling any source logic.

    Unknown keys remain allowed for forward compatibility, but a known key
    with the wrong type fails closed.  In particular, YAML values such as
    ``enabled: "true"`` must never accidentally enable collection.
    """
    if not isinstance(config.get("enabled"), bool):
        return "enabled must be a boolean"
    if not _valid_string_mapping(config.get("display_names")):
        return "display_names must be a string-to-string mapping"
    if not isinstance(config.get("source_bonus"), dict):
        return "source_bonus must be a mapping"
    for key, value in config["source_bonus"].items():
        if not isinstance(key, str) or not _is_number(value):
            return "source_bonus values must be finite numbers"
    if "relevance_rule_version" in config and not isinstance(
        config["relevance_rule_version"], str
    ):
        return "relevance_rule_version must be a string"
    if not _valid_string_list(config.get("tier1_international_media")):
        return "tier1_international_media must be a list of strings"
    for key in config["tier1_international_media"]:
        if not key.strip():
            return "tier1_international_media entries must be non-empty strings"

    keywords = config.get("relevance_keywords")
    if not isinstance(keywords, dict):
        return "relevance_keywords must be a mapping"
    if config["enabled"]:
        missing = [key for key in _RELEVANCE_KEY_GROUPS if key not in keywords]
        if missing:
            return "relevance_keywords missing groups: " + ",".join(missing)
    for key, value in keywords.items():
        if not isinstance(key, str) or not _valid_string_list(value):
            return f"relevance_keywords.{key} must be a list of strings"
        if any(not item.strip() for item in value):
            return f"relevance_keywords.{key} entries must be non-empty strings"

    dedup = config.get("dedup")
    if not isinstance(dedup, dict):
        return "dedup must be a mapping"
    if "similarity_threshold" in dedup and not _is_number(dedup["similarity_threshold"]):
        return "dedup.similarity_threshold must be numeric"
    if "similarity_threshold" in dedup and not 0 <= float(dedup["similarity_threshold"]) <= 1:
        return "dedup.similarity_threshold must be between 0 and 1"
    if "window_hours" in dedup and (
        not _is_number(dedup["window_hours"]) or float(dedup["window_hours"]) <= 0
    ):
        return "dedup.window_hours must be a positive number"
    for key in ("source_priority", "core_words"):
        if key in dedup and (
            not _valid_string_list(dedup[key])
            or any(not item.strip() for item in dedup[key])
        ):
            return f"dedup.{key} must be a non-empty string list"
    if "synonyms" in dedup and not _valid_string_mapping(dedup["synonyms"]):
        return "dedup.synonyms must be a string-to-string mapping"
    if "tier1" in config["source_bonus"] and not _is_number(config["source_bonus"]["tier1"]):
        return "source_bonus.tier1 must be numeric"
    return None


# ────────────────────────────────────────────────────────────────
# 配置加载
# ────────────────────────────────────────────────────────────────

def load_international_config(path: str | Path | None = None) -> dict:
    """加载国际媒体层配置。

    缺文件/损坏/非 dict 时返回禁用安全默认（enabled=False），不抛异常。
    """
    if path is None:
        path = Path(__file__).resolve().parent.parent / DEFAULT_INTERNATIONAL_CONFIG_PATH
    cfg_path = Path(path)
    if not cfg_path.exists():
        logger.warning("International media config %s missing; layer disabled", cfg_path)
        return copy.deepcopy(DISABLED_CONFIG)
    try:
        with open(cfg_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as exc:
        logger.warning("Failed to load international media config %s: %s", cfg_path, exc)
        return copy.deepcopy(DISABLED_CONFIG)
    if not isinstance(config, dict):
        logger.warning("International media config %s is not a mapping; layer disabled", cfg_path)
        return copy.deepcopy(DISABLED_CONFIG)
    config.setdefault("enabled", False)
    config.setdefault("display_names", {})
    config.setdefault("tier1_international_media", [])
    config.setdefault("relevance_keywords", {})
    config.setdefault("dedup", {})
    config.setdefault("source_bonus", {})
    # YAML 1.1 treats an unquoted ``off`` mapping key as boolean ``false``.
    # The existing config predates this strict validator, so canonicalize that
    # one well-known spelling before validating rather than disabling an
    # otherwise valid config (and losing the intended off -> near synonym).
    dedup = config.get("dedup")
    if isinstance(dedup, dict) and isinstance(dedup.get("synonyms"), dict):
        synonyms = dedup["synonyms"]
        if False in synonyms and "off" not in synonyms:
            synonyms["off"] = synonyms.pop(False)
    error = _validate_config_shape(config)
    if error:
        logger.warning("Invalid international media config %s; layer disabled: %s", cfg_path, error)
        return copy.deepcopy(DISABLED_CONFIG)
    return config


# ────────────────────────────────────────────────────────────────
# 来源识别与展示名
# ────────────────────────────────────────────────────────────────

def is_international_media(source_name: str | None, config: dict | None) -> bool:
    """source_name 是否属于 tier1_international_media（大小写不敏感）。"""
    cfg = config or {}
    tier1 = cfg.get("tier1_international_media", []) or []
    names = {str(n).strip().lower() for n in tier1 if str(n).strip()}
    return bool(source_name) and str(source_name).strip().lower() in names


def display_name(source_name: str | None, config: dict | None) -> str:
    """中文展示名映射；未知来源回退原 source_name。"""
    cfg = config or {}
    if source_name is None:
        return ""
    names = cfg.get("display_names", {}) or {}
    return names.get(str(source_name), str(source_name))


# ────────────────────────────────────────────────────────────────
# 词边界/短语匹配
# ────────────────────────────────────────────────────────────────

def _tokenize(text: str) -> list[str]:
    # Keep dotted abbreviations as one token: ``U.S.`` must become ``us``.
    # Otherwise it is split into ``u`` and ``s`` and the latter can falsely
    # match possessives such as ``Russia's``.
    normalized = _DOTTED_ACRONYM_RE.sub(
        lambda match: match.group(0).replace(".", ""), (text or "").lower()
    )
    return _TOKEN_RE.findall(normalized)


def word_contains(text: str, keyword: str) -> bool:
    """大小写不敏感的词/短语匹配。

    - 英文单关键词按词边界匹配："China" 不命中 "Chinatown"、"Taiwanese"。
    - 多词短语按连续 token 子序列匹配："Taiwan Strait"、"chip export
      controls"、"Cross-Strait"（连字符）均可命中。
    - 非 ASCII 关键词（如中文）按原样子串匹配。
    """
    kw = str(keyword or "").strip().lower()
    if not kw:
        return False
    text_lower = (text or "").lower()
    if re.search(r"[^\x00-\x7f]", kw):
        return kw in text_lower
    text_tokens = _tokenize(text_lower)
    kw_tokens = _tokenize(kw)
    if not kw_tokens:
        return False
    if len(kw_tokens) == 1:
        return kw_tokens[0] in set(text_tokens)
    n = len(kw_tokens)
    return any(
        text_tokens[i : i + n] == kw_tokens for i in range(len(text_tokens) - n + 1)
    )


# ────────────────────────────────────────────────────────────────
# 相关度分类
# ────────────────────────────────────────────────────────────────

@dataclass(slots=True)
class InternationalClassification:
    """一条国际媒体文章的相关度判定结果。

    tier ∈ {"taiwan_direct", "china_related", "china_us", "us_international", "other"}；
    topic 为 best-effort 内部分类（不持久化）；relevant 决定是否进入简报。
    """

    tier: str = "other"
    topic: str = "international"
    relevant: bool = False
    matched_keywords: list[str] = field(default_factory=list)


@dataclass(slots=True)
class RelevanceDecision:
    """Explainable, runtime-only international relevance decision.

    The object intentionally is not persisted in ``Article``/SQLite.  Lists are
    used instead of sets so the result remains deterministic and JSON-friendly.
    ``matched_keywords`` is retained as a compatibility/debug projection for
    callers that need the raw configured hits.
    """

    relevant: bool = False
    tier: str = "other"
    topics: list[str] = field(default_factory=list)
    entities: list[str] = field(default_factory=list)
    reason: str = ""
    rule_version: str = "international-relevance-v2"
    input_hash: str = ""
    matched_keywords: list[str] = field(default_factory=list)


RELEVANCE_RULE_VERSION = "international-relevance-v2"

# Canonical entity labels.  These are deliberately narrower than issue
# keywords: "Washington" or "semiconductor" alone is not a Taiwan-relevant
# entity/context relationship.
_ENTITY_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Taiwan", (
        "Taiwan", "Taiwanese", "Taipei", "Taiwan Strait", "Cross-Strait", "Formosa",
        "台灣", "台湾", "臺灣", "台北", "台北", "臺北", "台海", "兩岸", "两岸",
        "台美", "台日", "台澳", "台灣政府", "台湾政府", "台灣軍方", "台湾军方",
    )),
    ("TSMC", (
        "TSMC", "Taiwan Semiconductor Manufacturing", "台積電", "台积电",
    )),
    ("Lai Ching-te", ("Lai Ching-te", "William Lai", "賴清德", "赖清德")),
    ("DPP", (
        "DPP", "Democratic Progressive Party", "Taiwan's ruling party", "民進黨", "民进党",
    )),
    ("KMT", (
        "KMT", "Kuomintang", "Chinese Nationalist Party", "國民黨", "国民党",
    )),
    ("China", (
        "China", "Chinese", "Beijing", "Xi Jinping", "CCP", "Chinese Communist Party",
        "中國", "中国", "中共", "習近平", "习近平", "大陸", "大陆",
    )),
    ("Beijing", ("Beijing", "北京")),
    ("PLA", (
        "PLA", "People's Liberation Army", "People's Liberation Army of China",
        "解放軍", "解放军", "共軍", "共军", "中國人民解放軍", "中国人民解放军",
    )),
    ("Taiwan Affairs Office", ("Taiwan Affairs Office", "TAO", "國台辦", "国台办")),
    ("United States", (
        "United States", "US", "U.S.", "Washington", "White House",
        "美國", "美国", "華盛頓", "华盛顿", "白宮", "白宫",
    )),
    ("White House", ("White House", "白宮", "白宫")),
    ("Washington", ("Washington", "華盛頓", "华盛顿")),
    ("Pentagon", ("Pentagon", "五角大廈", "五角大厦")),
    ("Japan", ("Japan", "Tokyo", "日本", "東京", "东京")),
    ("Philippines", ("Philippines", "菲律賓", "菲律宾")),
)

_TOPIC_ALIASES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("military", (
        "military", "army", "navy", "warship", "warships", "missile", "missiles",
        "drill", "drills", "exercise", "exercises", "maneuver", "maneuvers",
        "defense", "defence", "arms sales", "naval", "maritime", "deterrence",
        "troops", "deployment", "south china sea", "indo-pacific",
        "軍事", "军事", "軍售", "军售", "軍演", "军演", "演習", "演习", "國防", "国防",
        "海巡", "導彈", "导弹", "飛彈", "飞弹", "台海",
    )),
    ("diplomacy", (
        "diplomatic", "diplomacy", "talks", "summit", "minister", "ambassador",
        "alliance", "allies", "support", "respond", "response",
        "consultation", "consultations", "clash", "relations",
        "外交", "外交部", "峰會", "峰会", "大使", "部長", "部长", "聯盟", "联盟",
        "關係", "关系", "回應", "回应",
    )),
    ("policy", (
        "policy", "policies", "export controls", "arms sales", "commander",
        "restricts", "controls", "政策", "出口管制", "管制", "指揮官", "指挥官",
    )),
    ("sanctions", ("sanction", "sanctions", "sanctioned", "制裁")),
    ("semiconductor", (
        "tsmc", "semiconductor", "semiconductors", "chip", "chips", "fab", "fabs",
        "foundry", "wafer", "wafers", "processor", "processors",
        "半導體", "半导体", "晶片", "芯片", "台積電", "台积电",
    )),
    ("trade", (
        "trade", "tariff", "tariffs", "export", "exports", "import", "imports", "duties",
        "貿易", "贸易", "關稅", "关税", "出口", "進口", "进口",
    )),
    ("cross_strait", (
        "taiwan strait", "cross-strait", "cross strait", "strait", "台灣海峽", "台湾海峡",
        "兩岸", "两岸", "台海",
    )),
    ("politics", (
        "election", "vote", "voting", "ruling party", "選舉", "选举", "投票", "執政黨", "执政党",
    )),
    ("economy", (
        "economy", "economic", "growth", "gdp", "inflation", "market", "markets",
        "stock", "stocks", "yuan", "renminbi", "recession", "bond", "bonds",
        "經濟", "经济", "成長", "成长", "通膨", "通胀", "市場", "市场",
    )),
    ("technology", (
        "technology", "technologies", "tech", "ai", "artificial intelligence",
        "software", "internet", "app", "apps", "data", "smartphone", "smartphones",
        "科技", "技術", "技术", "人工智慧", "人工智能", "軟體", "软件", "網路", "网络",
    )),
    ("taiwan", (
        "taiwan", "taiwanese", "taipei", "formosa", "kuomintang", "kmt", "dpp",
        "lai ching-te", "taiwan affairs office",
        "台灣", "台湾", "臺灣", "台北", "臺北", "台海", "兩岸", "两岸", "台美", "台日",
        "台積電", "台积电", "賴清德", "赖清德", "民進黨", "民进党", "國民黨", "国民党", "國台辦", "国台办",
    )),
    ("china", (
        "china", "chinese", "beijing", "xi jinping", "ccp", "communist party", "pla",
        "中國", "中国", "北京", "習近平", "习近平", "中共", "共產黨", "共产党", "解放軍", "解放军",
    )),
)

_STRONG_CHINA_CONTEXT = frozenset({
    "Taiwan", "Taiwan Strait", "Cross-Strait", "TSMC", "military", "Pentagon",
    "sanctions", "sanction", "chip", "chips", "export", "exports", "tariff",
    "tariffs", "arms", "missile", "missiles", "exercise", "exercises",
    "drill", "drills", "PLA", "Taiwan Affairs Office", "台灣", "台湾", "臺灣",
    "台海", "兩岸", "两岸", "軍事", "军事", "軍售", "军售", "軍演", "军演",
    "演習", "演习", "國防", "国防", "外交", "政策", "制裁", "關稅", "关税",
    "貿易", "贸易", "半導體", "半导体", "晶片", "芯片", "出口管制", "印太",
    "台灣海峽", "台湾海峡",
})


def _configured_hits(text: str, keywords_cfg: dict, key: str) -> list[str]:
    """Return configured keyword hits in configuration order."""
    return [
        str(keyword)
        for keyword in (keywords_cfg.get(key, []) or [])
        if word_contains(text, str(keyword))
    ]


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        marker = value.casefold()
        if marker not in seen:
            seen.add(marker)
            result.append(value)
    return result


def _extract_entities(text: str) -> list[str]:
    entities: list[str] = []
    for label, aliases in _ENTITY_ALIASES:
        if any(word_contains(text, alias) for alias in aliases):
            entities.append(label)
    # A phrase such as “Beijing ...” is both China context and a useful
    # concrete entity; avoid duplicating labels when aliases overlap.
    return _unique(entities)


def _extract_topics(text: str) -> list[str]:
    topics = [
        topic
        for topic, aliases in _TOPIC_ALIASES
        if any(word_contains(text, alias) for alias in aliases)
    ]
    return topics or ["international"]


def _is_taiwan_semiconductor_ambiguity(text: str) -> bool:
    """Reject company-name ``Taiwan Semiconductor`` without geopolitics.

    The well-known TSMC full name remains a direct hit.  A generic company
    named “Taiwan Semiconductor” is not itself evidence about Taiwan policy,
    cross-strait affairs, or security.
    """
    ambiguous_names = ("Taiwan Semiconductor", "台灣半導體", "台湾半导体")
    if not any(word_contains(text, alias) for alias in ambiguous_names):
        return False
    if (
        word_contains(text, "Taiwan Semiconductor Manufacturing")
        or word_contains(text, "TSMC")
        or word_contains(text, "台積電")
        or word_contains(text, "台积电")
    ):
        return False
    context_aliases = (
        "Taiwan Strait", "Cross-Strait", "military", "missile", "arms", "export",
        "tariff", "sanction", "Pentagon", "China", "Beijing", "Washington",
        "United States", "policy", "geopolitical",
        "台灣海峽", "台湾海峡", "兩岸", "两岸", "軍事", "军事", "軍售", "军售",
        "出口", "關稅", "关税", "制裁", "中國", "中国", "北京", "美國", "美国", "政策",
    )
    return not any(word_contains(text, alias) for alias in context_aliases)


def _input_hash(title: str | None, summary: str | None, source_name: str | None) -> str:
    payload = {
        "title": title or "",
        "summary": summary or "",
        "source_name": source_name or "",
    }
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def evaluate_relevance(
    title: str | None,
    summary: str | None,
    source_name: str | None,
    config: dict | None,
) -> RelevanceDecision:
    """Evaluate international relevance using entity + issue + relationship rules.

    Only the supplied title/teaser/summary is inspected.  No article URL is
    fetched and the publisher name is never sufficient to make an item
    relevant.  The returned reason/version/hash make every decision auditable.
    """
    cfg = config or {}
    keywords_cfg = cfg.get("relevance_keywords", {}) or {}
    text = f"{title or ''} {summary or ''}".strip()
    text_hash = _input_hash(title, summary, source_name)
    rule_version = str(cfg.get("relevance_rule_version", RELEVANCE_RULE_VERSION))
    entities = _extract_entities(text)
    topics = _extract_topics(text)
    direct = _configured_hits(text, keywords_cfg, "taiwan_direct")
    china = _configured_hits(text, keywords_cfg, "china_related")
    us = _configured_hits(text, keywords_cfg, "us_international")
    china_us_china = _configured_hits(text, keywords_cfg, "china_us_china_entities")
    china_us_us = _configured_hits(text, keywords_cfg, "china_us_us_entities")
    if not us and any(entity in {"Japan", "Philippines", "Pentagon", "Washington", "United States"} for entity in entities):
        # ``Tokyo`` and other concrete regional entities are not required to
        # be repeated in every configuration, but still belong to the
        # international gate (without being relevant on their own).
        us = [entity for entity in entities if entity in {"Japan", "Philippines", "Pentagon", "Washington", "United States"}]
    cross = _configured_hits(text, keywords_cfg, "china_relevant_cross")
    matched = _unique(direct + china + us + china_us_china + china_us_us + cross)

    def decision(relevant: bool, tier: str, reason: str) -> RelevanceDecision:
        return RelevanceDecision(
            relevant=relevant,
            tier=tier,
            topics=list(topics),
            entities=list(entities),
            reason=reason,
            rule_version=rule_version,
            input_hash=text_hash,
            matched_keywords=matched,
        )

    if not keywords_cfg:
        return decision(False, "other", "no configured relevance entities or context")

    if _is_taiwan_semiconductor_ambiguity(text):
        return decision(
            False,
            "taiwan_direct",
            "Taiwan Semiconductor name ambiguity without geopolitical context",
        )

    # A direct Taiwan entity is the highest-confidence relationship.  It is
    # intentionally evaluated before broad China/US buckets.
    if direct:
        return decision(
            True,
            "taiwan_direct",
            "direct Taiwan entity/context matched: " + ", ".join(direct[:5])
            + "; topics=" + ",".join(topics),
        )

    # China/Taiwan security and semiconductor contexts retain their existing
    # higher-priority tier even if an American institution is also mentioned.
    strong = [hit for hit in cross if hit in _STRONG_CHINA_CONTEXT]
    if china and strong:
        return decision(
            True,
            "china_related",
            "China entity linked to relevant context: " + ", ".join(strong[:5])
            + "; topics=" + ",".join(topics),
        )

    if china_us_china and china_us_us:
        return decision(
            True,
            "china_us",
            "China-US relationship matched: China=" + ", ".join(china_us_china[:5])
            + "; US=" + ", ".join(china_us_us[:5])
            + "; topics=" + ",".join(topics),
        )

    if china:
        if us:
            return decision(
                True,
                "us_international",
                "international entity linked to China context: " + ", ".join(us[:5])
                + "; topics=" + ",".join(topics),
            )
        return decision(
            False,
            "china_related",
            "China entity matched without Taiwan/US/Indo-Pacific, military, diplomacy, policy, trade, sanctions or semiconductor context",
        )

    if us:
        return decision(
            False,
            "us_international",
            "international keyword matched without Taiwan/China context",
        )

    return decision(False, "other", "no relevant Taiwan/China/US-Indo-Pacific relationship matched")


# topic 关键词映射（best-effort，不要求精确；按列表顺序首个命中生效）
_TOPIC_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("semiconductor", ("tsmc", "chip", "chips", "semiconductor", "semiconductors", "fab", "fabs", "foundry", "wafer", "wafers")),
    ("military", ("military", "army", "navy", "air force", "marine", "marines", "warship", "warships", "missile", "missiles", "drill", "drills", "exercise", "exercises", "maneuver", "maneuvers", "weapon", "weapons", "defense", "defence", "aircraft carrier", "submarine", "submarines", "munitions", "troops")),
    ("cross_strait", ("taiwan strait", "strait", "cross-strait", "cross strait", "mainland", "reunification", "unification")),
    ("us_taiwan", ("taiwan", "taipei", "united states", "washington", "pentagon", "arms sales", "indopacific", "indo-pacific", "lai ching-te")),
    ("trade", ("tariff", "tariffs", "trade", "export", "exports", "import", "imports", "quota", "quotas", "embargo", "duties")),
    ("economy", ("economy", "economic", "growth", "gdp", "inflation", "market", "markets", "stock", "stocks", "yuan", "renminbi", "recession", "bond", "bonds")),
    ("technology", ("technology", "technologies", "tech", "ai", "artificial intelligence", "software", "internet", "app", "apps", "data", "smartphone", "smartphones")),
    ("diplomacy", ("diplomat", "diplomats", "ambassador", "ambassadors", "summit", "talks", "visit", "visits", "foreign minister", "embassy", "treaty", "alliance", "allies")),
    ("taiwan", ("taiwan", "taiwanese", "taipei", "kuomintang", "kmt", "dpp", "lai ching-te")),
    ("china", ("china", "chinese", "beijing", "xi jinping", "ccp", "communist party")),
    ("international", ("united states", "washington", "pentagon", "congress", "state department", "japan", "philippines", "south china sea", "east china sea", "indo-pacific")),
]


def _classify_topic(text: str) -> str:
    for topic, keywords in _TOPIC_KEYWORDS:
        if any(word_contains(text, kw) for kw in keywords):
            return topic
    return "international"


def classify_international(
    title: str | None,
    summary: str | None,
    source_name: str | None,
    config: dict | None,
) -> InternationalClassification:
    """Backward-compatible projection of :func:`evaluate_relevance`.

    ``topic`` is intentionally sourced from the frozen Phase-I classifier,
    not from the richer multi-topic decision.  Existing consumers therefore
    continue to receive values such as ``us_taiwan`` and ``economy``.
    """
    decision = evaluate_relevance(title, summary, source_name, config)
    topic = _classify_topic(f"{title or ''} {summary or ''}")
    return InternationalClassification(
        tier=decision.tier,
        topic=topic,
        relevant=decision.relevant,
        matched_keywords=list(decision.matched_keywords),
    )


def filter_international(
    articles: list[Article],
    config: dict | None,
) -> tuple[list[Article], list[Article]]:
    """仅对国际媒体文章做相关度过滤。

    非国际媒体文章不受影响（始终进入 included）。禁用配置放行全部。
    """
    cfg = config or {}
    if not cfg.get("enabled", False):
        return list(articles), []

    included: list[Article] = []
    excluded: list[Article] = []
    for article in articles:
        if not is_international_media(article.source_name, cfg):
            included.append(article)
            continue
        cls = classify_international(
            article.title, article.summary, article.source_name, cfg
        )
        if cls.relevant:
            included.append(article)
        else:
            excluded.append(article)
    return included, excluded


# ────────────────────────────────────────────────────────────────
# 跨媒体事件去重（显示层，不写库）
# ────────────────────────────────────────────────────────────────

_STOPWORDS = frozenset(
    """a about above after again against all am an and any are as at be because been
    before being below between both but by can cannot could did do does doing down
    during each few for from further had has have having he her here hers herself
    him himself his how i if in into is it its itself just me more most my myself
    no nor not now of off on once only or other our ours ourselves out over own
    same she should so some such than that the their theirs them themselves then
    there these they this those through to too under until up very was we were
    what when where which while who whom why will with you your yours yourself
    yourselves s t d ll re ve don doesn didn isn aren wasn weren hasn haven hadn
    wouldn couldn shouldn must may might would could should am m o us""".split()
)

# 轻量同义词归一默认值（config dedup.synonyms 会覆盖/扩充）
_DEFAULT_SYNONYMS: dict[str, str] = {
    "drill": "exercise",
    "drills": "exercise",
    "exercise": "exercise",
    "exercises": "exercise",
    "maneuver": "exercise",
    "maneuvers": "exercise",
    "manoeuvres": "exercise",
    "wargame": "exercise",
    "wargames": "exercise",
    "launch": "start",
    "launches": "start",
    "begins": "start",
    "begin": "start",
    "starts": "start",
    "start": "start",
    "commences": "start",
    "holds": "start",
    "near": "near",
    "around": "near",
    "off": "near",
    "surrounding": "near",
    "nearby": "near",
}


def _light_stem(word: str) -> str:
    """轻量词干化：-s/-es/-ing/-ed（对 <=3 字母单词不做处理）。"""
    if len(word) <= 3:
        return word
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith("ing"):
        return word[:-3]
    if word.endswith("ed"):
        return word[:-2]
    if word.endswith("es"):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


def normalize_tokens(text: str, synonyms: dict | None = None) -> set[str]:
    """标题归一化 token 集。

    顺序：小写 token 化 -> 同义词归一（须先于停用词过滤，如 off->near） ->
    去英文停用词 -> 轻量词干化。
    """
    syn = dict(_DEFAULT_SYNONYMS)
    if synonyms:
        syn.update({str(k).strip().lower(): str(v).strip().lower() for k, v in synonyms.items() if str(k).strip()})
    normalized: list[str] = []
    for tok in _tokenize(text or ""):
        tok = syn.get(tok, tok)
        if tok in _STOPWORDS:
            continue
        normalized.append(_light_stem(tok))
    return set(normalized)


def title_similarity(a: str, b: str, synonyms: dict | None = None) -> float:
    """两标题的归一化 token 集 Jaccard 相似度（0~1）。"""
    ta = normalize_tokens(a, synonyms)
    tb = normalize_tokens(b, synonyms)
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def _safe_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def _within_window(
    dt_a: datetime | None, dt_b: datetime | None, window_hours: float
) -> bool:
    """时间窗判定：任一为空或 aware/naive 混合时保守不合并。"""
    if dt_a is None or dt_b is None:
        return False
    if (dt_a.tzinfo is None) != (dt_b.tzinfo is None):
        return False
    return abs((dt_a - dt_b).total_seconds()) <= window_hours * 3600.0


def _normalized_core_concepts(
    core_words: list[str], synonyms: dict | None = None
) -> tuple[set[str], list[str]]:
    """把 config ``dedup.core_words`` 归一化为“核心概念”：

    - 归一化后为单个 token 的进 ``single``（去重后的概念集合，如 drill/drills/
      exercise/exercises 都收敛为 exercise 一个概念）；
    - 归一化后为多 token 短语的保留原文进 ``phrases``（用 word_contains 短语
      匹配，如 “South China Sea”）。
    """
    single: set[str] = set()
    phrases: list[str] = []
    for cw in core_words:
        toks = normalize_tokens(cw, synonyms)
        if len(toks) == 1:
            single.update(toks)
        elif toks:
            phrases.append(cw)
    return single, phrases


def _shared_core_concepts(
    tokens_a: set[str],
    tokens_b: set[str],
    title_a: str,
    title_b: str,
    single: set[str],
    phrases: list[str],
) -> int:
    """两篇标题归一化后共享的核心概念数。

    - 单 token 概念：取两标题归一化 token 集的交集再与概念集合求交；
    - 多 token 短语概念：两标题都含该短语则 +1。
    合并边条件之一为 shared >= 2。
    """
    count = len(tokens_a & tokens_b & single)
    count += sum(
        1
        for ph in phrases
        if word_contains(title_a, ph) and word_contains(title_b, ph)
    )
    return count


def _published_ts(article: Article) -> float:
    """published_at 的 epoch 秒（无/不可解析时返回 +inf，排在最后）。

    统一转 timestamp 后比较，避免 aware/naive datetime 直接比较抛 TypeError。
    """
    published = article.published_at
    if published is None:
        return float("inf")
    try:
        return published.timestamp()
    except (ValueError, OverflowError):
        return float("inf")


def _canonical_key(
    article: Article, index: int, priority: dict[str, int]
) -> tuple:
    """canonical 排序键：published_at 最早 -> 来源优先级 -> 输入顺序。"""
    return (
        _published_ts(article),
        priority.get((article.source_name or "").lower(), len(priority)),
        index,
    )


def dedupe_international_for_digest(
    articles: list[Article], config: dict | None
) -> tuple[list[Article], dict[str, list[Article]]]:
    """跨媒体事件去重（显示层，不写库）。

    仅对国际媒体文章聚类：在“时间窗内相似度图”上做传递闭包（连通分量）——
    两篇国际文章之间构成一条边，当且仅当同时满足：

      (1) 在 dedup.window_hours 时间窗内（无 published_at 或 aware/naive
          混合不构成边，保守不合并）；
      (2) 标题归一化 token 集 Jaccard >= dedup.similarity_threshold；
      (3) 归一化后共享 >= 2 个 dedup.core_words 核心概念。

    每个连通分量视为同一事件（transitive closure：A~B 且 B~C 即使 A≁C
    也归为同一事件）。canonical 取 published_at 最早者，并列按
    dedup.source_priority（Reuters > FT > WSJ > Bloomberg），再按输入顺序。

    保守优先：宁可少合并——无 published_at、超时间窗、aware/naive 混合、
    相似度不足、共享核心词不足 2 个的都不构成边；非国际媒体文章原样通过。

    返回 (canonical, coverage)：coverage 以 canonical.url 为键，值为该事件
    的全部成员（含 canonical 自身）。
    """
    cfg = config or {}
    if not cfg.get("enabled", False):
        return list(articles), {a.url: [a] for a in articles}

    dedup_cfg = cfg.get("dedup", {}) or {}
    threshold = _safe_float(dedup_cfg.get("similarity_threshold"), 0.70)
    window_hours = _safe_float(dedup_cfg.get("window_hours"), 24)
    core_words = [str(w) for w in (dedup_cfg.get("core_words", []) or []) if str(w).strip()]
    synonyms = dedup_cfg.get("synonyms", {}) or {}
    priority_source = dedup_cfg.get("source_priority", []) or list(DEFAULT_SOURCE_PRIORITY)
    priority = {str(name).strip().lower(): i for i, name in enumerate(priority_source)}

    # 仅国际媒体文章参与聚类；非国际媒体文章各自独立成簇，原样通过
    intl_indices = [
        i
        for i, a in enumerate(articles)
        if is_international_media(a.source_name, cfg)
    ]
    non_intl_indices = [i for i in range(len(articles)) if i not in intl_indices]

    if intl_indices:
        single, phrases = _normalized_core_concepts(core_words, synonyms)
        tokens_by_idx = {
            i: normalize_tokens(articles[i].title, synonyms) for i in intl_indices
        }

        def _edge(i: int, j: int) -> bool:
            a, b = articles[i], articles[j]
            if not _within_window(a.published_at, b.published_at, window_hours):
                return False
            if title_similarity(a.title, b.title, synonyms) < threshold:
                return False
            shared = _shared_core_concepts(
                tokens_by_idx[i],
                tokens_by_idx[j],
                a.title,
                b.title,
                single,
                phrases,
            )
            return shared >= 2

        # 并查集：求时间窗内相似度图的连通分量（传递闭包聚类）
        parent = {i: i for i in intl_indices}

        def _find(x: int) -> int:
            root = x
            while parent[root] != root:
                root = parent[root]
            while parent[x] != root:
                parent[x], x = root, parent[x]
            return root

        def _union(x: int, y: int) -> None:
            rx, ry = _find(x), _find(y)
            if rx != ry:
                parent[ry] = rx

        for k, i in enumerate(intl_indices):
            for j in intl_indices[k + 1:]:
                if _edge(i, j):
                    _union(i, j)

        root_to_members: dict[int, list[int]] = {}
        for i in intl_indices:
            root_to_members.setdefault(_find(i), []).append(i)
        clusters: list[list[int]] = list(root_to_members.values())
    else:
        clusters = []

    for i in non_intl_indices:
        clusters.append([i])

    canonical: list[Article] = []
    coverage: dict[str, list[Article]] = {}
    for cluster in clusters:
        members = [articles[i] for i in cluster]
        canon = min(
            ((i, articles[i]) for i in cluster),
            key=lambda pair: _canonical_key(pair[1], pair[0], priority),
        )[1]
        canonical.append(canon)
        coverage[canon.url] = members

    # 按输入顺序输出 canonical（排序交由调用方）
    canonical.sort(key=lambda a: articles.index(a))
    return canonical, coverage
