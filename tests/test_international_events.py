from datetime import datetime, timezone
import re

from app.international import load_international_config
from app.international_events import cluster_international_articles
from app.models import Article


CONFIG = load_international_config()


def _article(title, source_id="reuters_international", source_name="Reuters", *, hour=1, url=None):
    published = datetime(2026, 8, 13, hour, tzinfo=timezone.utc)
    return Article(
        source_id=source_id,
        source_name=source_name,
        category="international",
        title=title,
        url=url or f"https://example.test/{source_id}/{hour}",
        published_at=published,
        fetched_at=published,
        position=1,
    )


def test_four_sources_same_event_make_one_canonical_and_full_coverage():
    articles = [
        _article("China launches military drills near Taiwan", hour=1),
        _article("China holds military drills near Taiwan", "ft_alphaville", "Financial Times", hour=2),
        _article("China begins military drills near Taiwan", "wsj_newsletter", "Wall Street Journal", hour=3),
        _article("China starts military drills near Taiwan", "bloomberg_newsletter", "Bloomberg", hour=4),
    ]
    clusters, coverage = cluster_international_articles(articles, CONFIG)
    assert len(clusters) == 1
    cluster = clusters[0]
    assert cluster.canonical is articles[0]
    assert len(cluster.members) == 4
    assert {a.source_id for a in cluster.coverage} == {
        "reuters_international", "ft_alphaville", "wsj_newsletter", "bloomberg_newsletter"
    }
    assert coverage[articles[0].url] == cluster.members


def test_similar_but_different_events_remain_separate():
    pairs = [
        ("China launches drills near Taiwan", "China approves chip export controls targeting Taiwan"),
        ("US approves arms sales to Taiwan", "US approves arms sales to Philippines"),
        ("TSMC opens a new fab in Arizona", "TSMC opens a new fab in Japan"),
        ("China announces tariffs on US goods", "China announces tariffs on EU goods"),
    ]
    for left, right in pairs:
        clusters, _ = cluster_international_articles(
            [_article(left, hour=1), _article(right, "ft_alphaville", "Financial Times", hour=2)],
            CONFIG,
        )
        assert len(clusters) == 2, (left, right, clusters)
        assert all(len(c.members) == 1 for c in clusters)


def test_cross_day_major_follow_up_does_not_merge():
    first = _article("China launches military drills near Taiwan", hour=1)
    second = Article(
        source_id="ft_alphaville",
        source_name="Financial Times",
        category="international",
        title="China ends military drills near Taiwan",
        url="https://example.test/follow-up",
        published_at=datetime(2026, 8, 15, 2, tzinfo=timezone.utc),
        fetched_at=first.fetched_at,
        position=1,
    )
    clusters, _ = cluster_international_articles([first, second], CONFIG)
    assert len(clusters) == 2


def test_same_url_merges_even_when_titles_differ():
    left = _article("China announces policy", url="https://example.test/same/")
    right = _article("Updated title", "ft_alphaville", "Financial Times", hour=2, url="https://EXAMPLE.TEST/same#fragment")
    clusters, _ = cluster_international_articles([left, right], CONFIG)
    assert len(clusters) == 1
    assert len(clusters[0].members) == 2


def test_non_international_articles_are_never_clustered():
    left = _article("China launches military drills near Taiwan", source_id="local", source_name="中央社")
    right = _article("China holds military drills near Taiwan", source_id="local2", source_name="自由時報", hour=2)
    clusters, _ = cluster_international_articles([left, right], CONFIG)
    assert len(clusters) == 2


def test_event_id_is_content_hash_stable_across_order_and_repeated_runs():
    articles = [
        _article("China launches military drills near Taiwan", hour=1),
        _article(
            "China holds military exercises near Taiwan",
            "ft_alphaville",
            "Financial Times",
            hour=2,
            url="https://example.test/ft/same-event",
        ),
    ]
    first, _ = cluster_international_articles(articles, CONFIG)
    reversed_run, _ = cluster_international_articles(list(reversed(articles)), CONFIG)
    repeated, _ = cluster_international_articles(list(articles), CONFIG)
    assert first[0].event_id == reversed_run[0].event_id == repeated[0].event_id
    assert re.fullmatch(r"evt_[0-9a-f]{64}", first[0].event_id)


def test_negative_pair_has_two_canonical_clusters_and_distinct_ids():
    left = _article("US approves arms sales to Taiwan", hour=1)
    right = _article(
        "US approves arms sales to Philippines",
        "ft_alphaville",
        "Financial Times",
        hour=2,
    )
    clusters, _ = cluster_international_articles([left, right], CONFIG)
    assert len(clusters) == 2
    assert {cluster.canonical.url for cluster in clusters} == {left.url, right.url}
    assert len({cluster.event_id for cluster in clusters}) == 2


def test_same_event_family_different_entities_have_distinct_content_ids():
    left = _article("China launches military drills near Taiwan", hour=1)
    right = _article(
        "China launches military drills near Philippines",
        "ft_alphaville",
        "Financial Times",
        hour=2,
    )
    clusters, _ = cluster_international_articles([left, right], CONFIG)
    assert len(clusters) == 2
    assert len({cluster.event_id for cluster in clusters}) == 2


def test_cross_day_follow_up_gets_a_different_content_id():
    first = _article("China launches military drills near Taiwan", hour=1)
    second = Article(
        source_id="ft_alphaville",
        source_name="Financial Times",
        category="international",
        title="China launches military drills near Taiwan",
        url="https://example.test/follow-up-same-title",
        published_at=datetime(2026, 8, 15, 2, tzinfo=timezone.utc),
        fetched_at=first.fetched_at,
        position=1,
    )
    clusters, _ = cluster_international_articles([first, second], CONFIG)
    assert len(clusters) == 2
    assert len({cluster.event_id for cluster in clusters}) == 2
