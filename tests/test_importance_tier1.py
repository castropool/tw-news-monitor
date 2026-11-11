"""importance.py Tier-1 国际媒体来源加分测试（fixture 配置，禁止线上）。"""

import copy
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = PROJECT_ROOT / "config" / "importance_rules.yaml"

# 含 tier1 新键的规则配置：base 60 + 分类 economy(0) + tier1(3) = 63 < important(65)
RULES_WITH_TIER1 = {
    "enabled": True,
    "thresholds": {"critical": 85, "important": 65, "normal": 0},
    "display": {"max_highlights": 5},
    "total_cap": 5,
    "lanes": {"election": {"min_slots": 1}},
    "scoring": {
        "official_source_bonus": 5,
        "tier1_international_bonus": 3,
        "multi_rule_bonus": 5,
        "category_bonus": {"politics": 3, "economy": 0, "international": 0},
        "official_sources": ["中央社"],
        "tier1_international_media": [
            "Reuters", "Financial Times", "Wall Street Journal", "Bloomberg",
        ],
    },
    "rules": [
        {
            "id": "test_politics",
            "track": "politics_security",
            "description": "Test politics rule (base 60)",
            "base_score": 60,
            "level_cap": "critical",
            "subjects": ["总统"],
            "actions": ["宣布"],
            "scenes": ["520"],
            "negative": [],
            "boosts": [],
        },
    ],
}


def _without_tier1_keys(config):
    cfg = copy.deepcopy(config)
    cfg["scoring"].pop("tier1_international_bonus", None)
    cfg["scoring"].pop("tier1_international_media", None)
    return cfg


def test_tier1_bonus_applies():
    from app.importance import score_article

    tier1 = score_article("总统宣布520政策", "Reuters", "economy", "", RULES_WITH_TIER1)
    other = score_article("总统宣布520政策", "东森", "economy", "", RULES_WITH_TIER1)
    assert tier1.score == other.score + 3
    assert "国际媒体" in tier1.reasons


def test_tier1_bonus_case_insensitive_source():
    from app.importance import score_article

    r = score_article("总统宣布520政策", "reuters", "economy", "", RULES_WITH_TIER1)
    assert r.score == 63


def test_tier1_bonus_alone_not_enough_for_important():
    from app.importance import score_article

    # 60 + 3 = 63 < important(65) -> normal
    r = score_article("总统宣布520政策", "Reuters", "economy", "", RULES_WITH_TIER1)
    assert r.score == 63
    assert r.level == "normal"


def test_no_signal_article_stays_zero_even_for_tier1():
    from app.importance import score_article

    r = score_article("今日天气晴朗", "Reuters", "economy", "", RULES_WITH_TIER1)
    assert r.score == 0
    assert r.level == "normal"
    assert r.matched_rules == []


def test_official_and_tier1_coexist_correctly():
    from app.importance import score_article

    official = score_article("总统宣布520政策", "中央社", "economy", "", RULES_WITH_TIER1)
    tier1 = score_article("总统宣布520政策", "Reuters", "economy", "", RULES_WITH_TIER1)
    other = score_article("总统宣布520政策", "东森", "economy", "", RULES_WITH_TIER1)
    assert official.score == other.score + 5
    assert tier1.score == other.score + 3
    assert official.score == tier1.score + 2
    assert official.score == 65  # 60 + official 5 -> important 门槛
    assert tier1.score == 63     # 60 + tier1 3 -> 低于 important


def test_old_rules_without_tier1_keys_unaffected():
    from app.importance import score_article

    rules = _without_tier1_keys(RULES_WITH_TIER1)
    official = score_article("总统宣布520政策", "中央社", "economy", "", rules)
    other = score_article("总统宣布520政策", "东森", "economy", "", rules)
    tier1 = score_article("总统宣布520政策", "Reuters", "economy", "", rules)
    assert official.score == other.score + 5
    assert tier1.score == other.score  # 无 tier1 配置时 Reuters 不加分


def test_validate_accepts_tier1_keys():
    from app.importance import validate_rules_config

    assert validate_rules_config(RULES_WITH_TIER1) == []


def test_validate_accepts_old_rules():
    from app.importance import validate_rules_config

    assert validate_rules_config(_without_tier1_keys(RULES_WITH_TIER1)) == []


def test_validate_rejects_negative_tier1_bonus():
    from app.importance import validate_rules_config

    cfg = copy.deepcopy(RULES_WITH_TIER1)
    cfg["scoring"]["tier1_international_bonus"] = -1
    errors = validate_rules_config(cfg)
    assert any("tier1_international_bonus" in e for e in errors)


def test_validate_rejects_non_int_tier1_bonus():
    from app.importance import validate_rules_config

    cfg = copy.deepcopy(RULES_WITH_TIER1)
    cfg["scoring"]["tier1_international_bonus"] = "3"
    errors = validate_rules_config(cfg)
    assert any("tier1_international_bonus" in e for e in errors)


def test_validate_rejects_non_list_tier1_media():
    from app.importance import validate_rules_config

    cfg = copy.deepcopy(RULES_WITH_TIER1)
    cfg["scoring"]["tier1_international_media"] = "Reuters"
    errors = validate_rules_config(cfg)
    assert any("tier1_international_media" in e for e in errors)


def test_validate_rejects_tier1_bonus_exceeding_official():
    from app.importance import validate_rules_config

    cfg = copy.deepcopy(RULES_WITH_TIER1)
    cfg["scoring"]["tier1_international_bonus"] = 6  # > official_source_bonus 5
    errors = validate_rules_config(cfg)
    assert any("must not exceed" in e for e in errors)


def test_validate_tier1_equals_official_allowed():
    from app.importance import validate_rules_config

    cfg = copy.deepcopy(RULES_WITH_TIER1)
    cfg["scoring"]["tier1_international_bonus"] = 5
    assert validate_rules_config(cfg) == []


def test_real_config_contract():
    from app.importance import load_rules, validate_rules_config

    config = load_rules(RULES_PATH)
    scoring = config["scoring"]
    assert scoring["tier1_international_bonus"] == 3
    assert scoring["tier1_international_media"] == [
        "Reuters", "Financial Times", "Wall Street Journal", "Bloomberg",
    ]
    assert validate_rules_config(config) == []


def test_real_config_tier1_source_gets_bonus():
    from app.importance import load_rules, score_article

    config = load_rules(RULES_PATH)
    # 命中单条既有规则（top_leadership：总统/签署），避免多规则
    # 并存的 best 选取（既有 max 语义）干扰加分差值断言
    title = "總統簽署兩岸協議"
    r_t1 = score_article(title, "Reuters", "international", "", config)
    r_other = score_article(title, "東森", "international", "", config)
    assert r_t1.matched_rules == r_other.matched_rules
    assert len(r_t1.matched_rules) == 1
    assert r_t1.score == r_other.score + 3
