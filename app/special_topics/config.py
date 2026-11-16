"""Load ``config/special_topics.yaml`` without ever breaking the pipeline."""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

DEFAULT_PATH = Path(__file__).resolve().parents[2] / "config" / "special_topics.yaml"

# Minimal built-in defaults so the gate still behaves sensibly when the
# YAML is missing or malformed (the dedicated sections stay usable).
DEFAULT_CONFIG: dict[str, Any] = {
    "filter_version": "special_topics_builtin",
    "poll": {
        "enabled": True,
        "required_keywords": ["民調", "民调", "民意調查", "民意调查", "滿意度", "支持度", "聲望"],
        "exclude_keywords": [],
        "pollsters": [],
        "figure_labels": ["滿意度", "不滿意", "支持度", "支持", "不支持", "信任", "聲望"],
        "max_figures": 6,
    },
    "betting": {
        "enabled": True,
        "required_keywords": ["賭盤", "赌盘", "簽賭", "签赌", "組頭", "賭博", "赌博", "博弈", "賠率"],
        "exclude_keywords": [],
        "election_keywords": ["選舉", "选举", "大選", "九合一", "候選人", "立委", "市長", "罷免"],
        "subtypes": {},
    },
}


def _as_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item or "").strip()]


def normalize_config(payload: Any) -> dict[str, Any]:
    """Merge a raw YAML payload over the built-in defaults."""
    raw = payload if isinstance(payload, dict) else {}
    result: dict[str, Any] = {
        "filter_version": str(raw.get("filter_version") or DEFAULT_CONFIG["filter_version"]),
    }
    for topic in ("poll", "betting"):
        base = dict(DEFAULT_CONFIG[topic])
        section = raw.get(topic)
        if isinstance(section, dict):
            for key, value in section.items():
                base[key] = value
        base["enabled"] = bool(base.get("enabled", True))
        for key in ("required_keywords", "exclude_keywords", "figure_labels", "election_keywords"):
            if key in base:
                base[key] = _as_str_list(base.get(key))
        if topic == "poll":
            pollsters = []
            for item in base.get("pollsters") or []:
                if not isinstance(item, dict) or not str(item.get("name") or "").strip():
                    continue
                aliases = _as_str_list(item.get("aliases")) or [str(item["name"]).strip()]
                pollsters.append({"name": str(item["name"]).strip(), "aliases": aliases})
            base["pollsters"] = pollsters
            try:
                base["max_figures"] = max(1, int(base.get("max_figures", 6)))
            except (TypeError, ValueError):
                base["max_figures"] = 6
        else:
            subtypes = base.get("subtypes") if isinstance(base.get("subtypes"), dict) else {}
            base["subtypes"] = {
                str(name): _as_str_list(words)
                for name, words in subtypes.items()
                if _as_str_list(words)
            }
        result[topic] = base
    return result


def load_special_topics_config(path: str | Path | None = None) -> dict[str, Any]:
    target = Path(path) if path else DEFAULT_PATH
    try:
        with target.open(encoding="utf-8") as handle:
            payload = yaml.safe_load(handle)
    except FileNotFoundError:
        logger.warning("special topics config missing (%s); using built-in defaults", target)
        payload = None
    except (OSError, yaml.YAMLError) as exc:
        logger.warning("special topics config unreadable (%s); using built-in defaults", exc)
        payload = None
    return normalize_config(payload)
