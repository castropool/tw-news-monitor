"""Article 模型测试：国际媒体监测层 Phase I 新增字段的兼容性。

覆盖：旧参数构造不破坏、新字段默认 None、可设置、slots 保持。
"""
from datetime import datetime

import pytest

from app.models import Article


def make_article(**kwargs) -> Article:
    now = datetime.now()
    return Article(
        source_id=kwargs.get("source_id", "test"),
        source_name=kwargs.get("source_name", "測試媒體"),
        category=kwargs.get("category", "politics"),
        title=kwargs.get("title", "測試新聞"),
        url=kwargs.get("url", "https://example.com/news/1"),
        published_at=kwargs.get("published_at", now),
        fetched_at=kwargs.get("fetched_at", now),
        position=kwargs.get("position", 1),
    )


class TestArticlePhaseIFields:
    """Phase I: section / language / access_level 三个可空字段。"""

    def test_new_fields_default_none(self):
        a = make_article()
        assert a.section is None
        assert a.language is None
        assert a.access_level is None

    def test_old_positional_construction_compatible(self):
        """旧位置参数构造（无新字段）仍可用，新字段默认 None。"""
        now = datetime.now()
        a = Article(
            "s1", "媒體", "politics", "標題", "https://example.com/x",
            now, now, 1,
        )
        assert a.source_id == "s1"
        assert a.title == "標題"
        assert a.section is None
        assert a.language is None
        assert a.access_level is None

    def test_old_keyword_construction_compatible(self):
        """旧关键字参数构造（含 summary 系列）仍可用。"""
        now = datetime.now()
        a = Article(
            source_id="s1",
            source_name="媒體",
            category="politics",
            title="標題",
            url="https://example.com/x",
            published_at=now,
            fetched_at=now,
            position=1,
            summary="梗概",
            summary_source="rss",
            summary_attempted_at=now,
        )
        assert a.summary == "梗概"
        assert a.summary_source == "rss"
        assert a.section is None
        assert a.language is None
        assert a.access_level is None

    def test_new_fields_settable_after_construction(self):
        a = make_article()
        a.section = "world"
        a.language = "en"
        a.access_level = "public"
        assert a.section == "world"
        assert a.language == "en"
        assert a.access_level == "public"

    def test_new_fields_via_constructor(self):
        now = datetime.now()
        a = Article(
            source_id="reuters_international",
            source_name="Reuters",
            category="international",
            title="T",
            url="https://www.reuters.com/world/1",
            published_at=now,
            fetched_at=now,
            position=1,
            section="world",
            language="en",
            access_level="public",
        )
        assert a.source_name == "Reuters"
        assert a.section == "world"
        assert a.language == "en"
        assert a.access_level == "public"

    def test_access_level_legal_values_accepted(self):
        """合法值 public / metadata_only / newsletter（及默认 None）。"""
        for v in ("public", "metadata_only", "newsletter", None):
            a = make_article()
            a.access_level = v
            assert a.access_level == v

    def test_dataclass_slots_preserved(self):
        """slots=True 保持：未声明属性赋值应抛 AttributeError。"""
        a = make_article()
        with pytest.raises(AttributeError):
            a.phase_i_unknown_field = 1
