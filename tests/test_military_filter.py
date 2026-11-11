import json
from datetime import datetime
from pathlib import Path

from app.military import (
    filter_military_articles,
    is_military_source,
    load_military_config,
)
from app.models import Article


ROOT = Path(__file__).resolve().parents[1]
CONFIG = load_military_config(ROOT / "config" / "military.yaml")
GOLDEN = json.loads(
    (ROOT / "tests" / "fixtures" / "military" / "filter_golden.json").read_text(
        encoding="utf-8"
    )
)


def make_article(title: str, summary: str | None = None) -> Article:
    now = datetime(2026, 9, 4, 12, 0)
    return Article(
        source_id="military_test",
        source_name="军武测试源",
        category="military",
        title=title,
        url=f"https://example.test/{abs(hash(title))}",
        published_at=now,
        fetched_at=now,
        position=1,
        summary=summary,
    )


def test_config_is_enabled_and_complete():
    assert CONFIG["enabled"] is True
    assert "飞弹" in CONFIG["keep_keywords"]
    assert "军人节优惠" in CONFIG["drop_phrases"]


def test_military_source_requires_explicit_topic_and_valid_type():
    assert is_military_source(
        {"topic": "military", "military_source_type": "commercial_military"}
    )
    assert is_military_source(
        {"topic": "military", "military_source_type": "official_military"}
    )
    assert not is_military_source(
        {"topic": "military", "military_source_type": "unknown"}
    )
    assert not is_military_source({"category": "military"})


def test_major_military_news_is_kept():
    article = make_article("国防部宣布新一批军购与防空飞弹部署")
    kept, blocked = filter_military_articles([article], CONFIG)
    assert kept == [article]
    assert blocked == []


def test_military_day_discount_is_dropped():
    article = make_article("军人节优惠餐厅名单公布")
    kept, blocked = filter_military_articles([article], CONFIG)
    assert kept == []
    assert blocked == [article]


def test_officer_arts_activity_is_dropped():
    article = make_article("官兵文艺活动展示创作成果")
    kept, blocked = filter_military_articles([article], CONFIG)
    assert kept == []
    assert blocked == [article]


def test_keep_keyword_overrides_noise_phrase():
    article = make_article("部长视导飞弹战备慰问活动")
    kept, blocked = filter_military_articles([article], CONFIG)
    assert kept == [article]
    assert blocked == []


def test_plain_condolence_is_dropped():
    article = make_article("地方首长单纯慰问官兵")
    kept, blocked = filter_military_articles([article], CONFIG)
    assert kept == []
    assert blocked == [article]


def test_summary_can_protect_real_military_story():
    article = make_article("部长赴营区慰问活动", summary="现场视导飞弹战备整备")
    kept, blocked = filter_military_articles([article], CONFIG)
    assert kept == [article]
    assert blocked == []


def test_disabled_filter_keeps_everything():
    articles = [make_article("军人节优惠"), make_article("官兵家庭日")]
    kept, blocked = filter_military_articles(articles, {"enabled": False})
    assert kept == articles
    assert blocked == []


def test_golden_positive_retention_at_least_98_percent():
    articles = [make_article(title) for title in GOLDEN["positive"]]
    kept, _ = filter_military_articles(articles, CONFIG)
    assert len(kept) / len(articles) >= 0.98


def test_golden_noise_filter_accuracy_at_least_95_percent():
    articles = [make_article(title) for title in GOLDEN["noise"]]
    _, blocked = filter_military_articles(articles, CONFIG)
    assert len(blocked) / len(articles) >= 0.95
