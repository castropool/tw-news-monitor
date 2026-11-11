from datetime import datetime
from pathlib import Path

import pytest

from app.content_filter import (
    DEFAULT_FILTER_PATH,
    blocked_keywords,
    filter_articles,
    load_content_filter,
)
from app.models import Article


def _article(title, category="economy", summary=None, url=None):
    if url is None:
        url = f"https://example.com/{abs(hash(title))}"
    return Article(
        source_id="test",
        source_name="测试来源",
        category=category,
        title=title,
        url=url,
        published_at=datetime(2026, 8, 1, 12, 0),
        fetched_at=datetime(2026, 8, 1, 12, 0),
        position=1,
        summary=summary,
    )


class TestLoadContentFilter:
    def test_missing_file_disabled(self, tmp_path):
        cfg = load_content_filter(tmp_path / "nope.yaml")
        assert cfg["enabled"] is False

    def test_default_file_exists(self):
        cfg = load_content_filter()
        assert "categories" in cfg

    def test_broken_yaml_disabled(self, tmp_path):
        p = tmp_path / "bad.yaml"
        p.write_text("{broken: [", encoding="utf-8")
        cfg = load_content_filter(p)
        assert cfg["enabled"] is False


class TestFilterArticles:
    def test_disabled_keeps_all(self):
        cfg = {"enabled": False, "categories": {"_default": ["美食"]}}
        arts = [_article("美食新店開幕"), _article("央行政策")]
        kept, blocked = filter_articles(arts, cfg)
        assert len(kept) == 2
        assert blocked == []

    def test_default_keyword_blocks(self):
        cfg = {"enabled": True, "categories": {"_default": ["美食", "網紅"]}}
        arts = [_article("美食新店開幕"), _article("央行宣布利率")]
        kept, blocked = filter_articles(arts, cfg)
        assert [a.title for a in kept] == ["央行宣布利率"]
        assert [a.title for a in blocked] == ["美食新店開幕"]

    def test_economy_specific_keyword(self):
        cfg = {"enabled": True, "categories": {"economy": ["大樂透"]}}
        arts = [_article("大樂透頭獎連22槓"), _article("台股收紅")]
        kept, blocked = filter_articles(arts, cfg)
        assert len(kept) == 1
        assert len(blocked) == 1

    def test_category_scoped_keyword_ignored_for_other_category(self):
        cfg = {"enabled": True, "categories": {"economy": ["大樂透"]}}
        arts = [_article("大樂透頭獎", category="politics")]
        kept, blocked = filter_articles(arts, cfg)
        assert len(kept) == 1
        assert blocked == []

    def test_case_insensitive_english(self):
        cfg = {"enabled": True, "categories": {"economy": ["ETF"]}}
        arts = [_article("热门 etf 推荐")]
        kept, blocked = filter_articles(arts, cfg)
        assert kept == []
        assert len(blocked) == 1

    def test_summary_also_matched(self):
        cfg = {"enabled": True, "categories": {"economy": ["開獎"]}}
        arts = [_article("今晚開獎結果揭曉", summary="今彩539開獎")]
        kept, blocked = filter_articles(arts, cfg)
        assert len(blocked) == 1

    def test_no_match_keeps(self):
        cfg = {"enabled": True, "categories": {"economy": ["大樂透", "美食"]}}
        arts = [_article("央行宣布升息")]
        kept, blocked = filter_articles(arts, cfg)
        assert len(kept) == 1
        assert blocked == []

    def test_blocked_keywords_reports_matches(self):
        cfg = {"enabled": True, "categories": {"economy": ["大樂透", "美食"]}}
        matched = blocked_keywords("大樂透開獎", "", "economy", cfg)
        assert matched == ["大樂透"]


class TestDefaultConfig:
    def test_real_config_filters_lottery(self):
        cfg = load_content_filter()
        arts = [_article("快訊／大樂透頭獎連22槓！下期頭獎上看9億元")]
        kept, blocked = filter_articles(arts, cfg)
        assert blocked and not kept

    def test_real_config_keeps_serious_economy(self):
        cfg = load_content_filter()
        arts = [_article("主計總處第2季經濟成長有望上修")]
        kept, blocked = filter_articles(arts, cfg)
        assert kept and not blocked


class TestCollectAllIntegration:
    """Content filter must be applied before articles hit the database."""

    def test_drop_before_save_blocks_lottery(self):
        from unittest.mock import MagicMock, patch

        import app.main as main_mod
        from app.content_filter import load_content_filter

        class FakeCollector:
            def __init__(self, source):
                self.source = source

            def collect(self):
                return [
                    _article("快訊／大樂透頭獎連22槓", url="https://example.com/1"),
                    _article("主計總處上修GDP", url="https://example.com/2"),
                ]

            def close(self):
                pass

        source = {
            "id": "s1", "name": "測試", "type": "rss",
            "category": "economy", "url": "https://example.com/rss",
        }
        config = load_content_filter()
        config["mode"] = "drop_before_save"
        db = MagicMock()
        db.get_all_article_urls.return_value = []
        db.save_articles.side_effect = lambda arts: list(arts)

        with patch.object(main_mod, "COLLECTOR_MAP", {"rss": FakeCollector}):
            inserted, total, dup, failed, run_removed, hist_id_dup, filtered = (
                main_mod.collect_all([source], db, config)
            )

        assert [a.title for a in inserted] == ["主計總處上修GDP"]
        assert filtered == 1
        assert dup == total - len(inserted) - filtered
        assert failed == []
