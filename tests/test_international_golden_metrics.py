"""Wave 4 quantitative gold corpus gates."""

import json
from pathlib import Path

from validation.international_media.golden_metrics import evaluate_gold


ROOT = Path(__file__).resolve().parent.parent


def test_minimum_gold_counts_and_metrics_are_enforced():
    report = evaluate_gold(ROOT / "tests" / "fixtures" / "international")
    assert report.relevance.total >= 32
    assert report.events.total_pairs >= 12
    assert report.relevance.precision >= 0.95
    assert report.relevance.recall >= 0.90
    assert report.relevance.hard_negative_fp == 0
    assert report.events.status == "pending_wave5"
    assert report.events.counted is False
    assert report.events.rc_eligible is False
    assert report.events.pair_precision is None
    assert report.events.pair_recall is None
    assert report.events.cluster_exact is False
    assert report.events.canonical_exact is False
    assert report.events.coverage_exact is False
    assert report.events.coverage_samples == 1
    assert report.minimum_counts_pass is True
    assert report.thresholds_pass is True
    assert report.passed is False


def test_gold_report_keeps_tp_fp_fn_and_importance_metrics():
    report = evaluate_gold(ROOT / "tests" / "fixtures" / "international")
    assert report.relevance.tp == 16
    assert report.relevance.fp == 0
    assert report.relevance.fn == 0
    assert report.importance.level_accuracy >= 0.90
    assert report.importance.important_critical_precision >= 0.90


def test_four_source_coverage_fixture_uses_exact_stable_source_ids():
    rows = [
        json.loads(line)
        for line in (
            ROOT / "tests" / "fixtures" / "international" / "golden_events.jsonl"
        ).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    row = next(item for item in rows if item.get("coverage_sample_id"))
    expected = {
        "reuters_international",
        "ft_alphaville",
        "wsj_newsletter",
        "bloomberg_newsletter",
    }
    assert row["input_article_count"] == 4
    assert len(row["articles"]) == 4
    assert {item["source_id"] for item in row["articles"]} == expected
    assert set(row["input_source_ids"]) == expected
    assert set(row["expected_coverage_source_ids"]) == expected
