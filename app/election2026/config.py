"""配置加载：config/election_2026.yaml 与 config/election_2026_entities.yaml。

沿用项目 fail-closed 惯例：配置文件缺失或损坏时返回 disabled 形状，
专题功能关闭，主采集/简报链路不受影响。
"""

import logging
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "election_2026.yaml"
DEFAULT_ENTITIES_PATH = PROJECT_ROOT / "config" / "election_2026_entities.yaml"

DISABLED_CONFIG = {
    "enabled": False,
    "thresholds": {"direct": 60, "review": 40},
    "scoring": {
        "strong_term": 45,
        "auxiliary_term": 25,
        "entity": 20,
        "region_in_title": 15,
        "action_context": 15,
    },
    "strong_terms": [],
    "auxiliary_terms": [],
    "negative_terms": [],
    "national_scenes": [],
    "event_type_map": {},
    "regions": {"display_order": [], "merge_groups": {}, "aliases": {}},
}

DISABLED_ENTITIES = {"candidates": {}}


def load_election_config(path: str | Path | None = None) -> dict:
    """加载九合一识别配置；缺失/损坏返回 disabled 形状。"""
    config_path = Path(path) if path else DEFAULT_CONFIG_PATH
    try:
        with open(config_path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
        if not isinstance(config, dict):
            logger.warning("election_2026 config invalid shape, disabled")
            return _deep_copy(DISABLED_CONFIG)
        config.setdefault("enabled", False)
        # 保证所有引用键存在（即使 yaml 不完整也不抛 KeyError）
        for key, default in (
            ("thresholds", {"direct": 60, "review": 40}),
            ("scoring", {
                "strong_term": 45, "auxiliary_term": 25, "entity": 20,
                "region_in_title": 15, "action_context": 15,
            }),
            ("strong_terms", []),
            ("auxiliary_terms", []),
            ("negative_terms", []),
            ("national_scenes", []),
            ("event_type_map", {}),
            ("regions", {"display_order": [], "merge_groups": {}, "aliases": {}}),
        ):
            if key not in config or config[key] is None:
                config[key] = _deep_copy(default)
        if not isinstance(config["regions"], dict):
            config["regions"] = _deep_copy(DISABLED_CONFIG["regions"])
        return config
    except FileNotFoundError:
        logger.warning("election_2026 config not found: %s, feature disabled", config_path)
        return _deep_copy(DISABLED_CONFIG)
    except Exception as exc:  # noqa: BLE001 - config must never break the pipeline
        logger.warning("election_2026 config load failed (%s), disabled", exc)
        return _deep_copy(DISABLED_CONFIG)


def load_entities(path: str | Path | None = None) -> dict:
    """加载候选人实体库；缺失/损坏返回空实体形状。"""
    config_path = Path(path) if path else DEFAULT_ENTITIES_PATH
    try:
        with open(config_path, encoding="utf-8") as f:
            entities = yaml.safe_load(f)
        if not isinstance(entities, dict) or not isinstance(entities.get("candidates"), dict):
            logger.warning("election_2026 entities invalid shape, empty")
            return {"candidates": {}}
        return entities
    except FileNotFoundError:
        logger.warning("election_2026 entities not found: %s", config_path)
        return {"candidates": {}}
    except Exception as exc:  # noqa: BLE001
        logger.warning("election_2026 entities load failed (%s), empty", exc)
        return {"candidates": {}}


def _deep_copy(value):
    import copy

    return copy.deepcopy(value)
