from datetime import datetime
from pathlib import Path

import pytest

from app.main import deduplicate_articles_by_url
from app.models import Article


def make_article(url="https://test.com/a", title="Test", source_name="Src",
                 category="politics", position=1):
    now = datetime.now()
    return Article(
        source_id="test",
        source_name=source_name,
        category=category,
        title=title,
        url=url,
        published_at=now,
        fetched_at=now,
        position=position,
    )


class TestDedupBasic:
    def test_two_same_urls_keeps_one(self):
        articles = [
            make_article(url="https://test.com/a"),
            make_article(url="https://test.com/a"),
        ]
        unique, dups = deduplicate_articles_by_url(articles)
        assert len(unique) == 1
        assert len(dups) == 1
        assert unique[0].url == "https://test.com/a"

    def test_three_same_urls_keeps_one(self):
        articles = [make_article(url=f"https://test.com/x") for _ in range(3)]
        unique, dups = deduplicate_articles_by_url(articles)
        assert len(unique) == 1
        assert len(dups) == 2

    def test_all_different_urls(self):
        articles = [
            make_article(url=f"https://test.com/{i}") for i in range(10)
        ]
        unique, dups = deduplicate_articles_by_url(articles)
        assert len(unique) == 10
        assert len(dups) == 0

    def test_order_preserved(self):
        articles = [
            make_article(url="https://test.com/b", position=2),
            make_article(url="https://test.com/a", position=1),
            make_article(url="https://test.com/b", position=3),
        ]
        unique, dups = deduplicate_articles_by_url(articles)
        assert len(unique) == 2
        assert unique[0].url == "https://test.com/b"
        assert unique[0].position == 2  # First occurrence kept
        assert unique[1].url == "https://test.com/a"

    def test_first_occurrence_kept(self):
        articles = [
            make_article(url="https://test.com/dup", title="First"),
            make_article(url="https://test.com/dup", title="Second"),
        ]
        unique, _ = deduplicate_articles_by_url(articles)
        assert unique[0].title == "First"


class TestDedupMixed:
    def test_cross_category_dedup(self):
        articles = [
            make_article(url="https://test.com/same", category="politics"),
            make_article(url="https://test.com/same", category="economy"),
            make_article(url="https://test.com/diff", category="politics"),
        ]
        unique, dups = deduplicate_articles_by_url(articles)
        assert len(unique) == 2
        assert len(dups) == 1

    def test_same_title_different_url_all_kept(self):
        articles = [
            make_article(url="https://test.com/a", title="Same Title"),
            make_article(url="https://test.com/b", title="Same Title"),
        ]
        unique, dups = deduplicate_articles_by_url(articles)
        assert len(unique) == 2
        assert len(dups) == 0

    def test_different_media_same_title_all_kept(self):
        articles = [
            make_article(url="https://media1.com/a", title="Identical Headline"),
            make_article(url="https://media2.com/a", title="Identical Headline"),
        ]
        unique, dups = deduplicate_articles_by_url(articles)
        assert len(unique) == 2

    def test_empty_url_not_merged(self):
        articles = [
            make_article(url="", title="Empty"),
            make_article(url="", title="Empty Too"),
        ]
        unique, dups = deduplicate_articles_by_url(articles)
        assert len(unique) == 2
        assert len(dups) == 0

    def test_duplicate_groups_count_correct(self):
        """14 dup URLs each appearing twice: 14 groups, 14 removed."""
        articles = []
        for i in range(14):
            articles.append(make_article(url=f"https://test.com/dup{i}", title=f"A{i}"))
            articles.append(make_article(url=f"https://test.com/dup{i}", title=f"B{i}"))
        # Add some unique
        for i in range(5):
            articles.append(make_article(url=f"https://test.com/uniq{i}", title=f"U{i}"))

        unique, dups = deduplicate_articles_by_url(articles)
        assert len(unique) == 19  # 14 unique + 5 unique
        assert len(dups) == 14  # 14 removed
        # Count unique URLs among dups = number of groups
        dup_urls = set(a.url for a in dups)
        assert len(dup_urls) == 14  # 14 groups


class TestDedupHistorical:
    @pytest.fixture
    def json_records(self):
        import json
        p = Path(__file__).resolve().parent / "fixtures" / "historical_collection_20260717_230913.json"
        if not p.exists():
            pytest.skip("Historical JSON not found")
        with open(p, encoding="utf-8") as f:
            return json.load(f)

    def test_historical_144_to_130(self, json_records):
        articles = []
        for rec in json_records:
            pub = None
            if rec.get("published_at_parsed"):
                try:
                    pub = datetime.fromisoformat(rec["published_at_parsed"])
                except (ValueError, TypeError):
                    pass
            fetched = datetime.now()
            try:
                fetched = datetime.fromisoformat(rec.get("fetched_at", ""))
            except (ValueError, TypeError):
                pass
            articles.append(Article(
                source_id=rec.get("source_id", "?"),
                source_name=rec.get("source_name", "?"),
                category=rec.get("category", "?"),
                title=rec.get("original_title", rec.get("normalized_title", "?")),
                url=rec.get("normalized_url", ""),
                published_at=pub,
                fetched_at=fetched,
                position=int(rec.get("position", 0)),
            ))

        unique, dups = deduplicate_articles_by_url(articles)
        raw_count = len(json_records)
        unique_count = len(unique)
        removed_count = len(dups)
        dup_groups = len(set(a.url for a in dups))

        assert raw_count == 144, f"Expected 144 raw, got {raw_count}"
        assert unique_count == 130, f"Expected 130 unique, got {unique_count}"
        assert removed_count == 14, f"Expected 14 removed, got {removed_count}"
        assert dup_groups == 14, f"Expected 14 groups, got {dup_groups}"
        assert len(set(a.url for a in unique)) == 130

        # Verify no duplicate URLs in unique list
        urls = [a.url for a in unique]
        assert len(urls) == len(set(urls)), "Unique list contains duplicate URLs"

        # Save validation results for report
        diag = Path(__file__).resolve().parent.parent / "data" / "diagnostics"
        lines = [
            "# Dedup Validation (Historical JSON)",
            "",
            f"Using: {p.name if 'p' in dir() else 'latest_collection.json'}",
            "",
            "## Results",
            "",
            f"- Raw records: {raw_count}",
            f"- Unique URLs: {unique_count}",
            f"- Removed (duplicates): {removed_count}",
            f"- Duplicate groups: {dup_groups}",
            f"- Deduped count: {unique_count}",
            "",
            "## Sample dedup groups (first 5)",
            "",
        ]
        # Show first 5 groups
        shown = set()
        for a in dups:
            if a.url not in shown:
                shown.add(a.url)
                kept = [u for u in articles if u.url == a.url][0]
                lines.append(f"### Group: {a.url}")
                lines.append(f"")
                lines.append(f"- Kept: {kept.title} (source={kept.source_name}, cat={kept.category})")
                lines.append(f"- Removed: {a.title} (source={a.source_name}, cat={a.category})")
                lines.append(f"")
                if len(shown) >= 5:
                    break

        lines.append("## Verification")
        lines.append("")
        lines.append(f"- Unique list has {unique_count} articles")
        lines.append(f"- Unique list URLs are all different: {len(set(a.url for a in unique)) == unique_count}")
        lines.append("")

        vp = diag / "dedup_validation.md"
        with open(vp, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))
        print(f"\nDedup validation written to: {vp}")
        print(f"  Raw: {raw_count}, Unique: {unique_count}, Removed: {removed_count}, Groups: {dup_groups}")

    def test_historical_no_dup_in_unique(self, json_records):
        """Unique articles list contains no duplicate URLs."""
        articles = []
        for rec in json_records:
            pub = None
            if rec.get("published_at_parsed"):
                try:
                    pub = datetime.fromisoformat(rec["published_at_parsed"])
                except (ValueError, TypeError):
                    pass
            fetched = datetime.now()
            try:
                fetched = datetime.fromisoformat(rec.get("fetched_at", ""))
            except (ValueError, TypeError):
                pass
            articles.append(Article(
                source_id=rec.get("source_id", "?"),
                source_name=rec.get("source_name", "?"),
                category=rec.get("category", "?"),
                title=rec.get("original_title", rec.get("normalized_title", "?")),
                url=rec.get("normalized_url", ""),
                published_at=pub,
                fetched_at=fetched,
                position=int(rec.get("position", 0)),
            ))
        unique, _ = deduplicate_articles_by_url(articles)
        urls = [a.url for a in unique]
        assert len(urls) == len(set(urls))
