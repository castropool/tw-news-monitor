from __future__ import annotations

import pytest

from app.election_candidates.assertion_classifier import (
    build_assertion_profile,
    classify_article_assertions,
)
from app.election_candidates.candidate_router import route_candidate
from app.election_candidates.event_clusterer import (
    cluster_articles,
    extract_event_date,
    relationship_between,
)
from app.election_candidates.relevance_calibrator import assign_relevance_label

from .conftest import article_from_fixture, load_golden_cases, make_config


def _articles(case):
    return [article_from_fixture(a) for a in case["articles"] if a.get("match") is not None]


@pytest.mark.parametrize("case", load_golden_cases(), ids=lambda c: c["case_id"])
def test_golden_cluster_counts(case, tmp_path):
    config = make_config(tmp_path)
    clusters = cluster_articles(_articles(case), config)
    if case["case_id"] == "golden_10":
        assert len(clusters) == 0
        return
    if case["case_id"] in ("golden_18", "golden_19"):
        assert len(clusters) == 1
        return
    assert len(clusters) == case["expected_cluster_count"], case["case_id"]


@pytest.mark.parametrize("case", load_golden_cases(), ids=lambda c: c["case_id"])
def test_golden_assertion_kinds(case, tmp_path):
    if case["case_id"] == "golden_10":
        config = make_config(tmp_path)
        assert set() == set()
        return
    config = make_config(tmp_path)
    kinds = set()
    for art in _articles(case):
        for a in classify_article_assertions(art, "cand_x", "run_x", config):
            kinds.add(a["assertion_kind"])
    expected = set(case["expected_assertion_kinds"])
    assert expected <= kinds, (case["case_id"], expected, kinds)


@pytest.mark.parametrize("case", load_golden_cases(), ids=lambda c: c["case_id"])
def test_golden_relationships(case, tmp_path):
    config = make_config(tmp_path)
    clusters = cluster_articles(_articles(case), config)
    if len(clusters) < 2:
        if clusters:
            assert clusters[0].relationship_type == "same_event"
        else:
            assert case["case_id"] == "golden_10"
        return
    rel, _ = relationship_between(clusters[0], clusters[1], config)
    assert rel == case["expected_relationship"], case["case_id"]


@pytest.mark.parametrize("case", load_golden_cases(), ids=lambda c: c["case_id"])
def test_golden_routing(case, tmp_path):
    config = make_config(tmp_path)
    articles = _articles(case)
    clusters = cluster_articles(articles, config)
    if not clusters:
        assert case["expected_status"] == "auto_reject"
        return
    cluster = clusters[0]
    anchor = cluster.anchor
    assertions = []
    for art in cluster.articles:
        assertions.extend(classify_article_assertions(art, "cand_x", "run_x", config))
    profile = build_assertion_profile(assertions)
    event_date, date_basis, date_conf = extract_event_date(anchor, config)
    candidate = {
        "relevance_label": assign_relevance_label(anchor, config)[0],
        "candidate_title": anchor.normalized_title,
        "candidate_summary": "據報導",
        "source_count": len({a.source_name for a in cluster.articles if a.source_name}),
        "canonical_event_date": event_date,
        "event_date_basis": date_basis,
        "event_date_confidence": date_conf,
        "region_match": anchor.match.region_match,
        "has_candidate_actor": bool(anchor.match.matched_people),
    }
    dup_score = 0.95 if case["expected_formal_match"] == "likely_duplicate" else (
        0.7 if case["expected_formal_match"] == "possible_match" else 0.1
    )
    scores = {
        "relevance_score": 0.8,
        "completeness_score": 0.9,
        "cluster_confidence": 0.9,
        "source_confidence": 0.9,
        "formal_duplicate_score": dup_score,
        "date_confidence": 1.0,
    }
    status, _ = route_candidate(candidate, scores, profile, config)
    assert status == case["expected_status"], (
        case["case_id"], status, case["expected_status"]
    )


@pytest.mark.parametrize(
    "case_id,expected_date,basis,confidence",
    [
        ("golden_14", "2026-07-19", "explicit_in_title", "medium"),
        ("golden_24", "2026-07-25", "explicit_in_title", "high"),
    ],
)
def test_golden_event_date_extraction(case_id, expected_date, basis, confidence, tmp_path, golden_cases):
    case = next(c for c in golden_cases if c["case_id"] == case_id)
    config = make_config(tmp_path)
    art = article_from_fixture(case["articles"][0])
    date, b, conf = extract_event_date(art, config)
    assert date.startswith(expected_date)
    assert b == basis
    assert conf == confidence


def test_date_unknown_when_no_published_at(tmp_path):
    from .conftest import article_from_fixture

    config = make_config(tmp_path)
    art = article_from_fixture(
        {
            "id": "9", "title": "陳亭妃出席活動", "url": "https://a.com/9",
            "source_name": "中央社", "category": "politics", "published_at": "",
            "match": {},
        }
    )
    date, basis, conf = extract_event_date(art, config)
    assert date == "" and basis == "unknown" and conf == "unknown"


def test_publication_date_not_claimed_as_event_date_high(tmp_path):
    from .conftest import article_from_fixture

    config = make_config(tmp_path)
    art = article_from_fixture(
        {
            "id": "10", "title": "陳亭妃出席活動", "url": "https://a.com/10",
            "source_name": "中央社", "category": "politics",
            "published_at": "2026-07-20T10:00:00", "match": {},
        }
    )
    _, basis, conf = extract_event_date(art, config)
    assert basis == "inferred_from_publication"
    assert conf == "low"
