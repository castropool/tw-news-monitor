"""国际媒体免费监测层 Phase I — app/international.py 全覆盖测试（纯 fixture，禁止线上）。"""

import copy
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from app.international import (
    DISABLED_CONFIG,
    classify_international,
    dedupe_international_for_digest,
    display_name,
    filter_international,
    is_international_media,
    load_international_config,
    normalize_tokens,
    title_similarity,
    word_contains,
)
from app.models import Article

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_PATH = PROJECT_ROOT / "config" / "international_media.yaml"

UTC = timezone.utc
BASE = datetime(2026, 8, 13, 10, 0, tzinfo=UTC)


def _article(
    title,
    source="Reuters",
    category="international",
    published_at=None,
    summary=None,
    url=None,
):
    if url is None:
        url = f"https://example.com/{abs(hash(title + source))}"
    return Article(
        source_id="test",
        source_name=source,
        category=category,
        title=title,
        url=url,
        published_at=published_at,
        fetched_at=BASE,
        position=1,
        summary=summary,
    )


@pytest.fixture(scope="module")
def cfg():
    return load_international_config(CONFIG_PATH)


# ────────────────────────────────────────────────────────────────
# 配置加载容错
# ────────────────────────────────────────────────────────────────

class TestLoadConfig:
    def test_missing_file_returns_disabled(self, tmp_path):
        cfg = load_international_config(tmp_path / "nope.yaml")
        assert cfg["enabled"] is False
        assert cfg["tier1_international_media"] == []

    def test_broken_yaml_returns_disabled(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("{broken: [", encoding="utf-8")
        cfg = load_international_config(p)
        assert cfg["enabled"] is False

    def test_non_mapping_yaml_returns_disabled(self, tmp_path):
        p = tmp_path / "list.yaml"
        p.write_text("- a\n- b\n", encoding="utf-8")
        cfg = load_international_config(p)
        assert cfg["enabled"] is False

    def test_default_file_loads_enabled(self, cfg):
        assert cfg["enabled"] is True
        assert cfg["tier1_international_media"] == [
            "Reuters", "Financial Times", "Wall Street Journal", "Bloomberg",
        ]
        rk = cfg["relevance_keywords"]
        for tier in (
            "taiwan_direct", "china_related", "us_international",
            "china_relevant_cross", "china_us_china_entities", "china_us_us_entities",
        ):
            assert rk[tier], f"keywords empty for {tier}"
        assert cfg["dedup"]["similarity_threshold"] == 0.70
        assert cfg["dedup"]["window_hours"] == 24

    def test_disabled_config_never_shared_across_calls(self, tmp_path):
        c1 = load_international_config(tmp_path / "nope.yaml")
        c2 = load_international_config(tmp_path / "nope.yaml")
        c1["display_names"]["X"] = "Y"
        assert "X" not in c2["display_names"]


# ────────────────────────────────────────────────────────────────
# 词边界/短语匹配
# ────────────────────────────────────────────────────────────────

class TestWordContains:
    def test_word_boundary_china_not_chinatown(self):
        assert word_contains("Chinatown protests in New York", "China") is False
        assert word_contains("China's economy grows", "China") is True

    def test_word_boundary_taiwan_not_taiwanese(self):
        assert word_contains("Taiwanese chip makers", "Taiwan") is False
        assert word_contains("Taiwan chip makers", "Taiwan") is True

    def test_case_insensitive(self):
        assert word_contains("TAIWAN STRAIT tensions", "taiwan") is True
        assert word_contains("taipei", "Taipei") is True

    def test_phrase_substring(self):
        assert word_contains("ships cross the Taiwan Strait", "Taiwan Strait") is True
        assert word_contains("new chip export controls unveiled", "chip export controls") is True
        assert word_contains("US approves arms sales", "arms sales") is True

    def test_phrase_not_crossing_tokens(self):
        # "China" 与 "Strait" 不相邻 -> 短语不命中
        assert word_contains("China and the strait", "China Strait") is False

    def test_hyphenated_phrase(self):
        assert word_contains("Cross-Strait talks resume", "Cross-Strait") is True
        assert word_contains("cross-strait relations", "Cross-Strait") is True

    def test_punctuation_and_apostrophe(self):
        assert word_contains("Taiwan's chip exports", "Taiwan") is True
        assert word_contains("People's Liberation Army drills", "People's Liberation Army") is True

    def test_dotted_abbreviation_is_not_split_into_single_letters(self):
        assert word_contains("U.S. officials met Chinese counterparts", "U.S.") is True
        assert word_contains("Russia's Far East tests unmanned trucks", "U.S.") is False

    def test_empty_keyword(self):
        assert word_contains("anything", "") is False
        assert word_contains("anything", "   ") is False

    def test_keyword_list_from_config(self, cfg):
        rk = cfg["relevance_keywords"]
        for kw in rk["taiwan_direct"]:
            assert word_contains(f"headline about {kw} today", kw), kw
        for kw in rk["china_related"]:
            assert word_contains(f"headline about {kw} today", kw), kw
        for kw in rk["us_international"]:
            assert word_contains(f"headline about {kw} today", kw), kw


# ────────────────────────────────────────────────────────────────
# 相关度判定（四层规则）
# ────────────────────────────────────────────────────────────────

class TestClassifyInternational:
    def test_taiwan_direct_relevant(self, cfg):
        r = classify_international("TSMC announces new chip plant in Taiwan", "", "Reuters", cfg)
        assert r.relevant is True
        assert r.tier == "taiwan_direct"

    def test_taiwan_direct_beats_china_related(self, cfg):
        r = classify_international(
            "Beijing urges Taiwan's Lai Ching-te to halt talks", "", "Reuters", cfg
        )
        assert r.relevant is True
        assert r.tier == "taiwan_direct"

    def test_china_related_with_cross_relevant(self, cfg):
        r = classify_international(
            "China holds large-scale military drills in East China Sea", "", "Reuters", cfg
        )
        assert r.relevant is True
        assert r.tier == "china_related"

    def test_china_economy_only_not_relevant(self, cfg):
        r = classify_international(
            "Chinese economy grows 5 percent in second quarter", "", "Reuters", cfg
        )
        assert r.relevant is False
        assert r.tier == "china_related"

    def test_us_international_only_not_relevant(self, cfg):
        r = classify_international(
            "Japan and Philippines hold joint military exercise", "", "Financial Times", cfg
        )
        assert r.relevant is False
        assert r.tier == "us_international"

    def test_us_international_with_china_cohit_relevant(self, cfg):
        # 中美关系：中国与美国实体同时命中 -> relevant（tier=china_us）
        r = classify_international(
            "Washington and Beijing resume trade talks", "", "Reuters", cfg
        )
        assert r.relevant is True
        assert r.tier == "china_us"

    def test_china_only_story_is_not_promoted_to_china_us(self, cfg):
        r = classify_international("China credit growth slows", "", "Reuters", cfg)
        assert r.relevant is False
        assert r.tier == "china_related"

    def test_us_international_china_with_cross_prefers_china_tier(self, cfg):
        r = classify_international(
            "Pentagon says China's chip export controls hit US firms", "", "Reuters", cfg
        )
        assert r.relevant is True
        assert r.tier == "china_related"

    def test_none_hit_other(self, cfg):
        r = classify_international("Apple stock rises on strong earnings", "", "Reuters", cfg)
        assert r.relevant is False
        assert r.tier == "other"

    def test_summary_also_matched(self, cfg):
        r = classify_international("Markets open mixed", "trade with Taiwan to resume", "Reuters", cfg)
        assert r.relevant is True
        assert r.tier == "taiwan_direct"

    def test_disabled_config_classifies_as_other(self):
        r = classify_international("TSMC fab in Taiwan", "", "Reuters", DISABLED_CONFIG)
        assert r.relevant is False
        assert r.tier == "other"

    def test_topic_best_effort(self, cfg):
        r = classify_international(
            "China launches new missile tests near Taiwan Strait", "", "Reuters", cfg
        )
        assert r.topic == "military"
        r2 = classify_international("TSMC reports record chip revenue", "", "Reuters", cfg)
        assert r2.topic == "semiconductor"


# ────────────────────────────────────────────────────────────────
# 来源识别与展示名
# ────────────────────────────────────────────────────────────────

class TestSourceAndDisplayName:
    def test_is_international_media(self, cfg):
        assert is_international_media("Reuters", cfg) is True
        assert is_international_media("Financial Times", cfg) is True
        assert is_international_media("Wall Street Journal", cfg) is True
        assert is_international_media("Bloomberg", cfg) is True
        assert is_international_media("reuters", cfg) is True  # 大小写不敏感
        assert is_international_media("中央社", cfg) is False
        assert is_international_media("", cfg) is False
        assert is_international_media(None, cfg) is False

    def test_display_name_mapping(self, cfg):
        assert display_name("Reuters", cfg) == "路透社"
        assert display_name("Financial Times", cfg) == "金融时报"
        assert display_name("Wall Street Journal", cfg) == "华尔街日报"
        assert display_name("Bloomberg", cfg) == "彭博社"

    def test_display_name_fallback(self, cfg):
        assert display_name("BBC", cfg) == "BBC"
        assert display_name(None, cfg) == ""


# ────────────────────────────────────────────────────────────────
# filter_international：只作用于国际媒体文章
# ────────────────────────────────────────────────────────────────

class TestFilterInternational:
    def test_only_international_articles_filtered(self, cfg):
        arts = [
            _article("TSMC announces new fab in Taiwan", source="Reuters", url="u1"),
            _article("Apple stock rises on strong earnings", source="Reuters", url="u2"),
            _article("Japan and Philippines hold joint military exercise", source="Financial Times", url="u3"),
            _article("中央社 本地新聞", source="中央社", category="politics", url="u4"),
        ]
        included, excluded = filter_international(arts, cfg)
        assert [a.url for a in included] == ["u1", "u4"]
        assert [a.url for a in excluded] == ["u2", "u3"]

    def test_non_international_never_excluded(self, cfg):
        arts = [
            _article("美食新店開幕", source="中央社", category="economy", url="d1"),
            _article("China holds military drills in East China Sea", source="Reuters", url="u1"),
        ]
        included, excluded = filter_international(arts, cfg)
        assert [a.url for a in included] == ["d1", "u1"]
        assert excluded == []

    def test_disabled_config_keeps_all(self):
        arts = [
            _article("Apple stock rises", source="Reuters", url="u1"),
            _article("China economy grows", source="Financial Times", url="u2"),
        ]
        included, excluded = filter_international(arts, DISABLED_CONFIG)
        assert len(included) == 2
        assert excluded == []

    def test_empty_articles(self, cfg):
        included, excluded = filter_international([], cfg)
        assert included == []
        assert excluded == []


# ────────────────────────────────────────────────────────────────
# 标题相似度归一化
# ────────────────────────────────────────────────────────────────

class TestNormalizeAndSimilarity:
    def test_synonym_normalization(self):
        s = normalize_tokens("China launches military drills near Taiwan")
        assert "exercise" in s
        assert "start" in s

    def test_stopwords_and_stemming(self):
        s = normalize_tokens("The United States launches missile tests")
        assert "the" not in s
        assert "us" not in s
        assert "test" in s
        assert "missile" in s

    def test_similarity_synonym_pair_is_one(self):
        a = "China launches military drills near Taiwan"
        b = "China holds military exercises off Taiwan"
        assert title_similarity(a, b) == 1.0

    def test_similarity_identical_is_one(self):
        assert title_similarity("Taiwan semiconductor exports surge", "Taiwan semiconductor exports surge") == 1.0

    def test_similarity_unrelated_is_low(self):
        assert title_similarity("Apple stock rises on strong earnings", "China launches new satellite into orbit") < 0.3

    def test_similarity_empty_inputs(self):
        assert title_similarity("", "anything") == 0.0
        assert title_similarity("", "") == 0.0

    def test_config_synonyms_override_defaults(self):
        custom = {"drills": "practice"}
        s = normalize_tokens("China holds military drills", custom)
        assert "practice" in s
        assert "exercise" not in s


# ────────────────────────────────────────────────────────────────
# 跨媒体事件去重
# ────────────────────────────────────────────────────────────────

class TestDedupeInternational:
    def _mk(self, title, source, offset_hours, url):
        return _article(
            title, source=source,
            published_at=BASE + timedelta(hours=offset_hours),
            url=url,
        )

    def test_merge_positive_cross_source_synonyms(self, cfg):
        arts = [
            self._mk("China launches military drills near Taiwan", "Reuters", 2, "u1"),
            self._mk("China holds military exercises off Taiwan", "Financial Times", 0, "u2"),
            self._mk("China begins military drills near Taiwan", "Bloomberg", 1, "u3"),
        ]
        canonical, coverage = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 1
        c = canonical[0]
        # canonical 取 published_at 最早（FT）
        assert c.source_name == "Financial Times"
        assert len(coverage[c.url]) == 3
        assert {a.url for a in coverage[c.url]} == {"u1", "u2", "u3"}

    def test_task_case4_three_sources_same_event_merged(self, cfg):
        """task Case 4：Reuters/FT/Bloomberg 三条同事件报道聚为一簇
        （一个 canonical + 2 coverage），相似度经同义词归一后均 >= 0.70。"""
        arts = [
            self._mk("China launches drills near Taiwan", "Reuters", 0, "u1"),
            self._mk("China begins military exercises around Taiwan", "Financial Times", 1, "u2"),
            self._mk("China starts new Taiwan military drills", "Bloomberg", 2, "u3"),
        ]
        canonical, coverage = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 1
        c = canonical[0]
        # canonical 取 published_at 最早（Reuters，0h）
        assert c.source_name == "Reuters"
        assert c.url == "u1"
        assert len(coverage["u1"]) == 3
        assert {a.url for a in coverage["u1"]} == {"u1", "u2", "u3"}

    def test_transitive_closure_merges_chain(self, cfg):
        """传递闭包：A~B 且 B~C（A≁C，相似度不足）仍聚为同一事件。"""
        arts = [
            self._mk("China launches military drills near Taiwan", "Reuters", 0, "u1"),
            self._mk("China launches military drills near Taiwan coast", "Financial Times", 1, "u2"),
            self._mk("China begins military exercises near Taiwan coast in big storm", "Bloomberg", 2, "u3"),
        ]
        # 验证 A≁C 无直接边（相似度 < 0.70），仅经 B 传递连通
        assert title_similarity(arts[0].title, arts[2].title) < 0.70
        assert title_similarity(arts[0].title, arts[1].title) >= 0.70
        assert title_similarity(arts[1].title, arts[2].title) >= 0.70
        canonical, coverage = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 1
        assert len(coverage[canonical[0].url]) == 3
        assert {a.url for a in coverage[canonical[0].url]} == {"u1", "u2", "u3"}

    def test_negative_chip_export_controls_not_merged(self, cfg):
        """负例 (b)：“China drills near Taiwan” vs “China approves new chip
        export controls targeting Taiwan”：共享多个核心词但相似度不足 -> 不合并。"""
        arts = [
            self._mk("China drills near Taiwan", "Reuters", 0, "u1"),
            self._mk("China approves new chip export controls targeting Taiwan", "Financial Times", 1, "u2"),
        ]
        canonical, coverage = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 2
        assert all(len(v) == 1 for v in coverage.values())

    def test_canonical_earliest_beats_priority(self, cfg):
        arts = [
            self._mk("China launches military drills near Taiwan", "Reuters", 2, "u1"),
            self._mk("China holds military exercises off Taiwan", "Financial Times", 0, "u2"),
        ]
        canonical, _ = dedupe_international_for_digest(arts, cfg)
        assert canonical[0].source_name == "Financial Times"

    def test_canonical_tie_prefers_source_priority(self, cfg):
        arts = [
            self._mk("China launches military drills near Taiwan", "Bloomberg", 0, "u1"),
            self._mk("China holds military exercises off Taiwan", "Reuters", 0, "u2"),
            self._mk("China begins military drills near Taiwan", "Financial Times", 0, "u3"),
        ]
        canonical, _ = dedupe_international_for_digest(arts, cfg)
        assert canonical[0].source_name == "Reuters"

    def test_negative_two_different_china_news_not_merged(self, cfg):
        arts = [
            self._mk("China launches new high-speed rail line in Tibet", "Reuters", 0, "u1"),
            self._mk("Chinese inflation data beats expectations", "Financial Times", 1, "u2"),
        ]
        canonical, coverage = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 2
        assert all(len(v) == 1 for v in coverage.values())

    def test_negative_same_source_different_events_not_merged(self, cfg):
        arts = [
            self._mk("China launches military drills near Taiwan", "Reuters", 0, "u1"),
            self._mk("China launches new high-speed rail in Tibet", "Reuters", 1, "u2"),
        ]
        canonical, _ = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 2

    def test_below_threshold_not_merged_even_with_core_word(self, cfg):
        # 共享 Taiwan/Strait 核心词但相似度不足 -> 保守不合并
        arts = [
            self._mk("Taiwan says Chinese warships entered Taiwan Strait", "Reuters", 0, "u1"),
            self._mk("Taiwan says China sent warships into Taiwan Strait", "Reuters", 1, "u2"),
        ]
        canonical, _ = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 2

    def test_window_24h_excluded(self, cfg):
        arts = [
            self._mk("China launches military drills near Taiwan", "Reuters", 0, "u1"),
            self._mk("China holds military exercises off Taiwan", "Financial Times", 25, "u2"),
        ]
        canonical, _ = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 2

    def test_window_24h_boundary_included(self, cfg):
        arts = [
            self._mk("China launches military drills near Taiwan", "Reuters", 0, "u1"),
            self._mk("China holds military exercises off Taiwan", "Financial Times", 24, "u2"),
        ]
        canonical, coverage = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 1
        assert len(coverage[canonical[0].url]) == 2

    def test_missing_published_at_never_merged(self, cfg):
        arts = [
            self._mk("China launches military drills near Taiwan", "Reuters", 0, "u1"),
            _article(
                "China holds military exercises off Taiwan",
                source="Financial Times",
                published_at=None,
                url="u2",
            ),
        ]
        canonical, _ = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 2

    def test_both_missing_published_at_not_merged(self, cfg):
        arts = [
            _article("China launches military drills near Taiwan", published_at=None, url="u1"),
            _article("China holds military exercises off Taiwan", published_at=None, url="u2"),
        ]
        canonical, _ = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 2

    def test_aware_naive_mix_not_merged(self, cfg):
        arts = [
            self._mk("China launches military drills near Taiwan", "Reuters", 0, "u1"),
            _article(
                "China holds military exercises off Taiwan",
                source="Financial Times",
                published_at=datetime(2026, 8, 13, 12, 0),  # naive
                url="u2",
            ),
        ]
        canonical, _ = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 2

    def test_non_international_passthrough_unmerged(self, cfg):
        domestic = _article(
            "中央社 本地新聞", source="中央社", category="politics",
            published_at=BASE, url="d1",
        )
        arts = [
            self._mk("China launches military drills near Taiwan", "Reuters", 0, "u1"),
            self._mk("China holds military exercises off Taiwan", "Financial Times", 1, "u2"),
            domestic,
        ]
        canonical, coverage = dedupe_international_for_digest(arts, cfg)
        assert len(canonical) == 2
        assert domestic in canonical
        assert coverage["d1"] == [domestic]
        assert len(coverage["u1"]) == 2

    def test_disabled_config_passthrough(self):
        arts = [
            self._mk("China launches military drills near Taiwan", "Reuters", 0, "u1"),
            self._mk("China holds military exercises off Taiwan", "Financial Times", 1, "u2"),
        ]
        canonical, coverage = dedupe_international_for_digest(arts, DISABLED_CONFIG)
        assert canonical == arts
        assert all(len(v) == 1 for v in coverage.values())

    def test_input_order_preserved_for_canonical(self, cfg):
        # 两个事件簇交错输入：canonical 按其在输入中的位置输出
        arts = [
            self._mk("China holds military exercises off Taiwan", "Financial Times", 1, "u2"),
            self._mk("Taiwan launches naval exercises near the island", "Reuters", 0, "x1"),
            self._mk("China launches military drills near Taiwan", "Reuters", 0, "u1"),
            self._mk("Taiwan holds naval drills around the island", "Financial Times", 0, "x2"),
        ]
        canonical, coverage = dedupe_international_for_digest(arts, cfg)
        assert [a.url for a in canonical] == ["x1", "u1"]
        assert len(coverage["u1"]) == 2
        assert len(coverage["x1"]) == 2

    def test_empty_input(self, cfg):
        canonical, coverage = dedupe_international_for_digest([], cfg)
        assert canonical == []
        assert coverage == {}
