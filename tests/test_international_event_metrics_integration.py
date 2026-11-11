import app.international_events

from validation.international_media.golden_metrics import evaluate_gold


def test_wave5_event_metrics_use_real_clusterer_and_four_article_coverage():
    report = evaluate_gold(
        "tests/fixtures/international",
        event_cluster_module=app.international_events,
    )
    assert report.events.status == "pass"
    assert report.events.counted is True
    assert report.events.pair_precision == 1.0
    assert report.events.pair_recall == 1.0
    assert report.events.cluster_exact is True
    assert report.events.canonical_exact is True
    assert report.events.coverage_exact is True
