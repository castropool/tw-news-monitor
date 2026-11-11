"""Strict configuration loading for the independent event pipeline."""
from pathlib import Path
import math

import yaml

DEFAULT_RULES_PATH = Path(__file__).resolve().parents[2] / "config/military_rules.yaml"
CATEGORY_IDS = {"taiwan_exercise", "taiwan_equipment", "arms_sale", "defense_technology",
                "pla_activity", "us_taiwan_military", "other_military"}


def load_rules(path=None) -> dict:
    with Path(path or DEFAULT_RULES_PATH).open(encoding="utf-8-sig") as stream:
        try:
            rules = yaml.safe_load(stream)
        except yaml.YAMLError as exc:
            raise ValueError("Invalid military rules YAML syntax") from exc
    validate_rules(rules)
    return rules


def validate_rules(rules):
    def require(condition, name):
        if not condition:
            raise ValueError(f"Invalid military rules: {name}")

    require(isinstance(rules, dict), "mapping required")
    for key in ("entities", "actions", "categories", "category_actions", "clustering", "importance", "scope", "negative", "sources"):
        require(isinstance(rules.get(key), dict), key)
    require(rules.get("version") == 1, "version")
    require(isinstance(rules.get("word_enabled"), bool), "word_enabled")
    require(set(rules["categories"]) == CATEGORY_IDS, "seven categories")
    require(all(isinstance(v, str) and v.strip() for v in rules["categories"].values()), "category labels")
    def terms(value, name):
        require(isinstance(value, list) and bool(value) and all(isinstance(v, str) and v.strip() for v in value), name)
    for group in ("organizations", "weapons", "equipment", "named_events", "places"):
        require(isinstance(rules["entities"].get(group), dict), group)
        require(all(isinstance(k, str) and k for k in rules["entities"][group]), "entity IDs")
        for value in rules["entities"][group].values():
            terms(value, group)
    for key, value in rules["actions"].items():
        terms(value, key)
    terms(rules.get("action_priority"), "action_priority")
    require(set(rules["action_priority"]) == set(rules["actions"]), "action priority coverage")
    for group, names in (("scope", ("taiwan", "foreign", "us", "strong_signals", "military_context")),
                         ("negative", ("contexts", "political_noise", "status_negative"))):
        for name in names:
            terms(rules[group].get(name), name)
    def number(value, name, lo=0, hi=100):
        require(type(value) in (int, float) and math.isfinite(value) and lo <= value <= hi, name)
    for name in ("window_hours", "missing_time_window_hours"):
        number(rules["clustering"].get(name), name, 1, 168)
    for name in ("threshold", "entity_weight", "action_weight", "title_weight", "place_weight", "time_weight", "time_penalty"):
        number(rules["clustering"].get(name), name)
    number(rules["clustering"].get("min_title_similarity"), "min_title_similarity", 0, 1)
    terms(rules["clustering"].get("broad_places"), "broad_places")
    terms(rules["clustering"].get("daily_bulletins"), "daily_bulletins")
    importance = rules["importance"]
    require(isinstance(importance.get("category_base"), dict) and set(importance["category_base"]) == CATEGORY_IDS, "category_base")
    for category, value in importance["category_base"].items():
        if category == "us_taiwan_military":
            require(value is None, "US-related reports must remain unscored")
        else:
            number(value, "category score")
    for name in ("equipment_bonus", "source_bonus", "max_source_bonus"):
        number(importance.get(name), name)
    terms(importance.get("key_equipment"), "key_equipment")
    require(isinstance(importance.get("signals"), dict), "signals")
    for signal in importance["signals"].values():
        require(isinstance(signal, dict), "signal")
        terms(signal.get("terms"), "signal terms")
        terms(signal.get("actions"), "signal actions")
        require(set(signal["actions"]) <= set(rules["actions"]), "signal action IDs")
        number(signal.get("weight"), "signal weight")
        require(isinstance(signal.get("reason"), str), "signal reason")
    for category, actions in rules["category_actions"].items():
        require(category in CATEGORY_IDS, "category action ID")
        terms(actions, "category actions")
        require(set(actions) <= set(rules["actions"]), "category actions exist")
    for source in rules["sources"].values():
        require(isinstance(source, dict), "source")
        terms(source.get("aliases"), "source aliases")
        number(source.get("quality"), "source quality", 0, 30)
        require(isinstance(source.get("mainstream"), bool), "mainstream")
