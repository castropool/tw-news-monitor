import pytest
from pathlib import Path
from types import SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RULES_PATH = PROJECT_ROOT / "config" / "importance_rules.yaml"
INTERNATIONAL_CONFIG_PATH = PROJECT_ROOT / "config" / "international_media.yaml"

SAMPLE_RULES = {
    "enabled": True,
    "thresholds": {"critical": 85, "important": 65, "normal": 0},
    "display": {"max_highlights": 5},
    "total_cap": 5,
    "lanes": {"election": {"min_slots": 1}},
    "scoring": {
        "official_source_bonus": 5,
        "multi_rule_bonus": 5,
        "category_bonus": {"politics": 3, "economy": 0, "international": 0},
        "official_sources": ["中央社"],
    },
    "rules": [
        {
            "id": "test_politics",
            "track": "politics_security",
            "description": "Test politics rule",
            "base_score": 78,
            "level_cap": "critical",
            "subjects": ["总统", "台湾"],
            "actions": ["宣布", "发表"],
            "scenes": ["520", "双十"],
            "negative": ["参访", "祝贺"],
            "boosts": [{"keywords": ["国家安全"], "add": 8}],
        },
        {
            "id": "test_election",
            "track": "election",
            "description": "Test election rule",
            "base_score": 72,
            "level_cap": "important",
            "subjects": ["民调"],
            "actions": ["公布"],
            "scenes": ["台南"],
            "negative": [],
            "boosts": [{"keywords": ["初选"], "add": 10}],
        },
        {
            "id": "test_diplomatic_meeting",
            "track": "politics_security",
            "description": "Test diplomatic meeting rule",
            "base_score": 74,
            "level_cap": "important",
            "subjects": ["总统"],
            "actions": ["接见"],
            "scenes": ["邦交国", "总理"],
            "negative": ["地方人士", "民间团体"],
            "boosts": [],
        },
        {
            "id": "test_maritime",
            "track": "politics_security",
            "description": "Test maritime rule",
            "base_score": 76,
            "level_cap": "critical",
            "subjects": ["大陆渔船", "海巡"],
            "actions": ["碰撞", "扣留"],
            "scenes": ["金门", "台海中线"],
            "negative": ["一般搁浅"],
            "boosts": [],
        },
    ],
}


def _article(title, source="中央社", category="politics", published_at=None):
    return SimpleNamespace(
        title=title,
        source_name=source,
        category=category,
        published_at=published_at,
    )


class TestScoring:
    def test_single_rule_match(self):
        from app.importance import score_article

        r = score_article("总统宣布520重要政策", "中央社", "politics", "", SAMPLE_RULES)
        assert r.level == "critical"
        assert r.score > 0
        assert "test_politics" in r.matched_rules
        assert len(r.reasons) > 0

    def test_no_match(self):
        from app.importance import score_article

        r = score_article("今日天气晴朗", "中央社", "politics", "", SAMPLE_RULES)
        assert r.level == "normal"
        assert r.score == 0

    def test_negative_keyword(self):
        from app.importance import score_article

        r = score_article("总统参访地方行程", "中央社", "politics", "", SAMPLE_RULES)
        assert r.level == "normal"

    def test_critical_level(self):
        from app.importance import score_article

        r = score_article(
            "总统宣布国家安全重大政策", "中央社", "politics", "", SAMPLE_RULES
        )
        assert r.score >= 85
        assert r.level == "critical"

    def test_important_level(self):
        from app.importance import score_article

        r = score_article("总统发表两岸政策谈话", "东森", "politics", "", SAMPLE_RULES)
        assert r.level == "important"
        assert 65 <= r.score < 85

    def test_normal_level(self):
        from app.importance import score_article

        r = score_article("地方活动预告", "中央社", "politics", "", SAMPLE_RULES)
        assert r.level == "normal"

    def test_score_range(self):
        from app.importance import score_article

        r = score_article(
            "总统宣布国家安全重大政策", "中央社", "politics", "", SAMPLE_RULES
        )
        assert 0 <= r.score <= 100

    def test_maritime_match(self):
        from app.importance import score_article

        r = score_article(
            "大陆渔船与台湾海巡在金门海域发生碰撞",
            "中央社",
            "politics",
            "",
            SAMPLE_RULES,
        )
        assert r.level in ("critical", "important")
        assert "test_maritime" in r.matched_rules

    def test_disabled_returns_normal(self):
        from app.importance import score_article

        config = {"enabled": False}
        r = score_article("总统宣布520政策", "", "", "", config)
        assert r.level == "normal"
        assert r.score == 0

    def test_generic_us_domestic_international_story_cannot_be_highlight(self):
        from app.international import load_international_config
        from app.importance import score_article

        international_config = load_international_config(INTERNATIONAL_CONFIG_PATH)
        result = score_article(
            "美国总统宣布国内移民政策",
            "Reuters",
            "international",
            "",
            SAMPLE_RULES,
            international_config=international_config,
        )

        assert result.score > 0
        assert result.level == "normal"
        assert "国际关联性不足" in result.reasons

    def test_taiwan_related_international_story_can_be_highlight(self):
        from app.international import load_international_config
        from app.importance import load_rules, score_article

        rules = load_rules(RULES_PATH)
        international_config = load_international_config(INTERNATIONAL_CONFIG_PATH)
        result = score_article(
            "美國宣布對台灣軍售",
            "Reuters",
            "international",
            "",
            rules,
            international_config=international_config,
        )

        assert result.level in ("critical", "important")


class TestBoostAndBonus:
    def test_boost_raises_score(self):
        from app.importance import score_article

        plain = score_article("总统宣布520政策", "东森", "politics", "", SAMPLE_RULES)
        boosted = score_article(
            "总统宣布国家安全政策", "东森", "politics", "", SAMPLE_RULES
        )
        assert boosted.score > plain.score

    def test_official_source_bonus(self):
        from app.importance import score_article

        official = score_article("总统宣布520政策", "中央社", "politics", "", SAMPLE_RULES)
        other = score_article("总统宣布520政策", "东森", "politics", "", SAMPLE_RULES)
        assert official.score == other.score + 5

    def test_level_cap_respected(self):
        from app.importance import score_article

        # 72 + politics 3 + boost 10 = 85, but level_cap=important
        r = score_article("民调公布 台南初选", "东森", "politics", "", SAMPLE_RULES)
        assert r.score >= 85
        assert r.level == "important"

    def test_election_track_recorded(self):
        from app.importance import score_article

        r = score_article("民调公布 台南", "东森", "politics", "", SAMPLE_RULES)
        assert r.track == "election"
        assert "election" in r.matched_tracks

    def test_diplomatic_meeting_important(self):
        from app.importance import score_article

        r = score_article("总统接见邦交国总理", "东森", "politics", "", SAMPLE_RULES)
        assert r.level == "important"
        assert "test_diplomatic_meeting" in r.matched_rules

    def test_diplomatic_meeting_negative(self):
        from app.importance import score_article

        r = score_article("总统接见地方人士", "东森", "politics", "", SAMPLE_RULES)
        assert r.level == "normal"


class TestFinalizeCap:
    def _results(self, specs):
        return [
            (
                _article(title, published_at=published_at),
                _result(score, level, track),
            )
            for title, score, level, track, published_at in specs
        ]

    def test_cap_total_five_with_election_guarantee(self):
        from app.importance import finalize_importance

        specs = [
            (f"政经{i}", 90 - i, "critical", "politics_security", None) for i in range(6)
        ]
        specs += [
            (f"选情{i}", 80 - i, "important", "election", None) for i in range(4)
        ]
        results = self._results(specs)
        finalize_importance(results, SAMPLE_RULES)

        kept = [(a, r) for a, r in results if r.level in ("critical", "important")]
        assert len(kept) == 5
        assert any(r.track == "election" for _a, r in kept)
        assert sum(1 for _a, r in kept if r.track == "politics_security") <= 4

    def test_election_empty_politics_can_fill_five(self):
        from app.importance import finalize_importance

        specs = [
            (f"政经{i}", 90 - i, "critical", "politics_security", None) for i in range(6)
        ]
        results = self._results(specs)
        finalize_importance(results, SAMPLE_RULES)

        kept = [(a, r) for a, r in results if r.level in ("critical", "important")]
        assert len(kept) == 5

    def test_election_only_fills_five(self):
        from app.importance import finalize_importance

        specs = [
            (f"选情{i}", 80 - i, "important", "election", None) for i in range(6)
        ]
        results = self._results(specs)
        finalize_importance(results, SAMPLE_RULES)

        kept = [(a, r) for a, r in results if r.level in ("critical", "important")]
        assert len(kept) == 5
        assert all(r.track == "election" for _a, r in kept)

    def test_downgraded_candidates_marked_capped(self):
        from app.importance import finalize_importance

        specs = [
            (f"政经{i}", 90 - i, "critical", "politics_security", None) for i in range(6)
        ]
        results = self._results(specs)
        finalize_importance(results, SAMPLE_RULES)

        downgraded = [r for _a, r in results if r.level == "normal"]
        assert len(downgraded) == 1
        assert all(r.capped for r in downgraded)
        kept = [r for _a, r in results if r.level in ("critical", "important")]
        assert all(not r.capped for r in kept)

    def test_no_candidates_unchanged(self):
        from app.importance import finalize_importance

        results = [(_article("普通新闻"), _result(0, "normal", None))]
        out = finalize_importance(results, SAMPLE_RULES)
        assert out is results
        assert results[0][1].level == "normal"

    def test_taiwan_domestic_lane_reserves_three_shared_with_election(self):
        from app.importance import finalize_importance

        rules = _deep_copy(SAMPLE_RULES)
        rules["lanes"]["taiwan_domestic"] = {"min_slots": 3}
        specs = [
            ("選情", 80, "important", "election", "politics"),
            ("台灣政治", 79, "important", "politics_security", "politics"),
            ("台灣經濟", 78, "important", "politics_security", "economy"),
            ("美國內政一", 99, "critical", "politics_security", "international"),
            ("美國內政二", 98, "critical", "politics_security", "international"),
            ("美國內政三", 97, "critical", "politics_security", "international"),
        ]
        results = [
            (_article(title, category=category), _result(score, level, track))
            for title, score, level, track, category in specs
        ]

        finalize_importance(results, rules)

        kept = [(article, result) for article, result in results if result.level in ("critical", "important")]
        domestic = [
            result
            for article, result in kept
            if result.track == "election" or article.category != "international"
        ]
        assert len(kept) == 5
        assert len(domestic) >= 3
        assert any(result.track == "election" for result in domestic)

    def test_taiwan_domestic_lane_allows_international_fill_when_short(self):
        from app.importance import finalize_importance

        rules = _deep_copy(SAMPLE_RULES)
        rules["lanes"]["taiwan_domestic"] = {"min_slots": 3}
        specs = [
            ("唯一本地新闻", 70, "important", "politics_security", "politics"),
            ("国际新闻一", 99, "critical", "politics_security", "international"),
            ("国际新闻二", 98, "critical", "politics_security", "international"),
            ("国际新闻三", 97, "critical", "politics_security", "international"),
            ("国际新闻四", 96, "critical", "politics_security", "international"),
            ("国际新闻五", 95, "critical", "politics_security", "international"),
        ]
        results = [
            (_article(title, category=category), _result(score, level, track))
            for title, score, level, track, category in specs
        ]

        finalize_importance(results, rules)

        kept = [(article, result) for article, result in results if result.level in ("critical", "important")]
        assert len(kept) == 5
        assert sum(article.category != "international" for article, _result in kept) == 1


class TestRealConfigCalibration:
    def test_config_valid_and_calibration_cases(self):
        from app.importance import load_rules, score_article, validate_rules_config

        config = load_rules(RULES_PATH)
        assert validate_rules_config(config) == []

        cases = [
            "華爾街日報：台美防衛合作趨公開 向中國展現關係穩固",
            "總統接見史瓦帝尼王國總理 感謝史國對臺灣的堅定支持 盼兩國邦誼穩固向前",
            "賴清德：台日最大威脅是中國 唯有團結合作才能守護民主",
            "柯文哲涉京華城案等 二審9/8首開庭",
            "立院三讀曾犯詐防條例有罪確定 不得參選總統、副總統",
            "蔡英文任陳瑩競總主委 台東綠營大團結",
            "英系插旗台東 蔡英文重出江湖為哪樁？",
        ]
        for title in cases:
            r = score_article(title, "中央社", "politics", "", config)
            assert r.level in ("critical", "important"), title


class TestConfigValidation:
    def test_validate_valid_config(self):
        from app.importance import validate_rules_config

        assert validate_rules_config(SAMPLE_RULES) == []

    def test_validate_bad_track(self):
        from app.importance import validate_rules_config

        cfg = _deep_copy(SAMPLE_RULES)
        cfg["rules"][0]["track"] = "invalid_track"
        assert len(validate_rules_config(cfg)) > 0

    def test_validate_bad_base_score(self):
        from app.importance import validate_rules_config

        cfg = _deep_copy(SAMPLE_RULES)
        cfg["rules"][0]["base_score"] = 120
        assert len(validate_rules_config(cfg)) > 0

    def test_validate_bad_level_cap(self):
        from app.importance import validate_rules_config

        cfg = _deep_copy(SAMPLE_RULES)
        cfg["rules"][0]["level_cap"] = "urgent"
        assert len(validate_rules_config(cfg)) > 0

    def test_validate_dimensions_rejected(self):
        from app.importance import validate_rules_config

        cfg = _deep_copy(SAMPLE_RULES)
        cfg["rules"][0]["dimensions"] = {"strategic_domain": 30}
        assert len(validate_rules_config(cfg)) > 0

    def test_validate_threshold_order(self):
        from app.importance import validate_rules_config

        cfg = _deep_copy(SAMPLE_RULES)
        cfg["thresholds"] = {"critical": 65, "important": 85, "normal": 0}
        assert len(validate_rules_config(cfg)) > 0

    def test_validate_total_cap(self):
        from app.importance import validate_rules_config

        cfg = _deep_copy(SAMPLE_RULES)
        cfg["total_cap"] = 0
        assert len(validate_rules_config(cfg)) > 0

    def test_validate_taiwan_domestic_lane_limits(self):
        from app.importance import validate_rules_config

        cfg = _deep_copy(SAMPLE_RULES)
        cfg["lanes"]["taiwan_domestic"] = {"min_slots": 6}
        errors = validate_rules_config(cfg)
        assert any("taiwan_domestic.min_slots" in error for error in errors)

        cfg["lanes"]["taiwan_domestic"] = {"min_slots": 2}
        cfg["lanes"]["election"]["min_slots"] = 3
        errors = validate_rules_config(cfg)
        assert any("election.min_slots" in error for error in errors)


class TestImportanceSummary:
    def test_summary_counts(self):
        from app.importance import ImportanceResult, importance_summary

        results = [
            (None, ImportanceResult(score=85, level="critical")),
            (None, ImportanceResult(score=70, level="important")),
            (None, ImportanceResult(score=30, level="normal")),
        ]
        s = importance_summary(results)
        assert "critical=1" in s
        assert "important=1" in s
        assert "normal=1" in s


def _result(score, level, track):
    from app.importance import ImportanceResult

    return ImportanceResult(score=score, level=level, track=track)


def _deep_copy(config):
    import copy

    return copy.deepcopy(config)
