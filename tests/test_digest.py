from datetime import datetime

import pytest

from app.digest import build_digest, count_new_by_category
from app.models import Article


def make_article(
    title: str = "測試新聞",
    category: str = "politics",
    url: str = "https://example.com/news/1",
    source_name: str = "測試媒體",
    published_at: datetime | None = None,
    position: int = 1,
) -> Article:
    now = datetime(2026, 7, 14, 20, 30)
    return Article(
        source_id="test",
        source_name=source_name,
        category=category,
        title=title,
        url=url,
        published_at=published_at or now,
        fetched_at=now,
        position=position,
    )


class TestDigest:
    def test_grouped_by_category(self):
        """1. Articles are grouped by politics, economy, international."""
        articles = [
            make_article(title="政治A", category="politics", position=1),
            make_article(title="經濟B", category="economy", position=1),
            make_article(title="國際C", category="international", position=1),
        ]
        now = datetime(2026, 7, 14, 20, 30)
        digest = build_digest(articles, now)

        assert "政治" in digest
        assert "经济" in digest
        assert "国际" in digest
        assert "政治A" in digest
        assert "經濟B" in digest
        assert "國際C" in digest

    def test_no_new_articles(self):
        """2. Empty articles generates correct 'no new' text."""
        now = datetime(2026, 7, 14, 20, 30)
        digest = build_digest([], now)

        assert "未发现新增新闻" in digest
        # Should NOT contain category headers
        assert "政治" not in digest

    def test_same_url_not_shown_twice(self):
        """3. Same URL doesn't appear twice (handled at DB level, digest assumes unique)."""
        articles = [
            make_article(title="唯一新聞", url="https://example.com/unique"),
        ]
        now = datetime(2026, 7, 14, 20, 30)
        digest = build_digest(articles, now)

        assert digest.count("唯一新聞") == 1

    def test_links_and_media_displayed(self):
        """4. Links and media name displayed correctly."""
        articles = [
            make_article(
                title="測試",
                source_name="中央社",
                url="https://www.cna.com.tw/news/aipl/123.aspx",
                published_at=datetime(2026, 7, 14, 20, 30),
            ),
        ]
        now = datetime(2026, 7, 14, 20, 30)
        digest = build_digest(articles, now)

        assert "中央社" in digest
        assert "https://www.cna.com.tw/news/aipl/123.aspx" in digest
        assert "20:30" in digest

    def test_count_new_by_category(self):
        """count_new_by_category returns correct counts."""
        articles = [
            make_article(category="politics"),
            make_article(category="politics"),
            make_article(category="economy"),
        ]
        counts = count_new_by_category(articles)
        assert counts == {"politics": 2, "economy": 1}

    def test_time_format_in_digest(self):
        """Time in digest shows HH:MM format."""
        articles = [
            make_article(
                title="時間測試",
                published_at=datetime(2026, 7, 14, 8, 5),
            ),
        ]
        now = datetime(2026, 7, 14, 20, 30)
        digest = build_digest(articles, now)
        assert "08:05" in digest

    def test_category_order(self):
        """Categories appear in correct order: politics, economy, international."""
        articles = [
            make_article(title="國際", category="international"),
            make_article(title="經濟", category="economy"),
            make_article(title="政治", category="politics"),
        ]
        now = datetime(2026, 7, 14, 20, 30)
        digest = build_digest(articles, now)

        pol_idx = digest.index("政治")
        eco_idx = digest.index("经济")
        int_idx = digest.index("国际")

        assert pol_idx < eco_idx < int_idx

    def test_digest_header_contains_time(self):
        """Digest header shows the correct timestamp."""
        now = datetime(2026, 7, 14, 20, 30)
        articles = [make_article()]
        digest = build_digest(articles, now)
        assert "2026-07-14 20:30" in digest
        assert "台湾新闻监测" in digest

    def test_empty_no_new_does_not_contain_category(self):
        """Empty digest doesn't have stray category headers."""
        now = datetime(2026, 7, 14, 20, 30)
        digest = build_digest([], now)
        for cat in ["政治", "经济", "国际"]:
            assert cat not in digest
        assert "未发现新增新闻" in digest
