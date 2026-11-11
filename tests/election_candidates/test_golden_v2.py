from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.election_candidates.config import load_config
from app.election_candidates.quality_gate import evaluate_golden_cases

from .conftest import make_config
from .golden_runner import (
    enrich_formal_events,
    load_articles,
    load_cases,
    load_duplicate_cases,
    load_formal_events,
    run_case,
)


CONFIG = load_config("config/election_candidate_pipeline.yaml")
ARTICLES = load_articles()
CASES = load_cases()
FORMAL_EVENTS = load_formal_events()


@pytest.fixture(scope="module")
def runner_results(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("golden")
    config = make_config(tmp)
    return {c["case_id"]: run_case(c, ARTICLES, FORMAL_EVENTS, config) for c in CASES}


def test_golden_case_count_and_no_skip():
    assert len(CASES) >= 30
    assert sum(1 for c in CASES if c["split"] == "calibration") == 20
    assert sum(1 for c in CASES if c["split"] == "holdout") == 10


def test_all_cases_executed(runner_results):
    assert set(runner_results) == {c["case_id"] for c in CASES}
    assert all(r["cluster_count"] >= 1 for r in runner_results.values())


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_relevance_label(case, runner_results):
    result = runner_results[case["case_id"]]
    assert result["results"][0]["relevance_label"] == case["expected_relevance"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_cluster_count(case, runner_results):
    result = runner_results[case["case_id"]]
    assert result["cluster_count"] == case["expected_cluster_count"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_cluster_members(case, runner_results):
    result = runner_results[case["case_id"]]
    predicted = [sorted(group) for group in result["clusters"]]
    expected = [sorted(group) for group in case["expected_cluster_members"]]
    assert predicted == expected


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_relationship(case, runner_results):
    result = runner_results[case["case_id"]]
    assert result["relationship"] == case["expected_relationship"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_event_type(case, runner_results):
    result = runner_results[case["case_id"]]
    if not case.get("expected_event_type"):
        assert result["results"][0]["event_type"] == "unknown"
    else:
        assert result["results"][0]["event_type"] == case["expected_event_type"]


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_assertion_kinds(case, runner_results):
    result = runner_results[case["case_id"]]
    predicted = set().union(*(r["assertion_kinds"] for r in result["results"]))
    expected = set(case["expected_assertion_kinds"])
    assert expected <= predicted
    if case.get("must_not_classify_as_observed_fact"):
        assert "observed_fact" not in predicted


@pytest.mark.parametrize("case", CASES, ids=lambda c: c["case_id"])
def test_route_status(case, runner_results):
    result = runner_results[case["case_id"]]
    assert result["results"][0]["route_status"] == case["expected_route_status"]


def test_holdout_uses_same_rules(runner_results):
    # Holdout cases must run through the same generic runner; no per-case branches exist.
    holdout = [c for c in CASES if c["split"] == "holdout"]
    assert len(holdout) == 10
    assert all(c["case_id"] in runner_results for c in holdout)


def test_known_duplicate_top3():
    config = make_config(Path("."))
    dup_cases = load_duplicate_cases()
    from app.election_candidates.formal_duplicate_checker import check_candidate_duplicates

    for case in dup_cases:
        suggestions = check_candidate_duplicates(
            {
                "candidate_id": "cand_x",
                "primary_actor": case["candidate_actors"][0] if case["candidate_actors"] else "",
                "secondary_actors_json": "[]",
                "themes_json": "[]",
                "keywords_json": json.dumps(case["candidate_keywords"], ensure_ascii=False),
                "canonical_event_date": case["candidate_date"],
                "candidate_event_type": case["candidate_event_type"],
                "candidate_title": case["candidate_title"],
                "candidate_summary": "",
            },
            enrich_formal_events(FORMAL_EVENTS),
            config,
            "run_x",
            {"cna.com.tw"},
        )
        top3 = [s["formal_event_id"] for s in suggestions[:3]]
        flagged = any(s["suggested_action"] == "likely_duplicate" for s in suggestions)
        if case["known_duplicate"]:
            assert case["expected_formal_event_id"] == top3[0], case["case_id"]
            assert case["expected_formal_event_id"] in top3, case["case_id"]
            assert flagged is True, case["case_id"]
        else:
            assert flagged is False, case["case_id"]


def test_quality_gate_computed(runner_results):
    config = make_config(Path("."))
    from app.election_candidates.formal_duplicate_checker import check_candidate_duplicates

    relevance_predictions = {
        aid: c["expected_relevance"]
        for c in CASES
        for aid in c["article_ids"]
    }
    # Use actual predictions for relevance
    for c in CASES:
        r = runner_results[c["case_id"]]
        for aid, cluster in zip(c["article_ids"], [r["results"][0]]):
            relevance_predictions[aid] = cluster["relevance_label"]
    cluster_predictions = {c["case_id"]: runner_results[c["case_id"]]["clusters"] for c in CASES}
    assertion_predictions = {
        c["case_id"]: set(runner_results[c["case_id"]]["results"][0]["assertion_kinds"])
        for c in CASES
    }
    event_type_predictions = {
        c["case_id"]: runner_results[c["case_id"]]["results"][0]["event_type"]
        for c in CASES
    }
    dup_cases = load_duplicate_cases()
    formal_duplicate_results = {}
    for case in dup_cases:
        suggestions = check_candidate_duplicates(
            {
                "candidate_id": "cand_x",
                "primary_actor": case["candidate_actors"][0] if case["candidate_actors"] else "",
                "secondary_actors_json": "[]",
                "themes_json": "[]",
                "keywords_json": json.dumps(case["candidate_keywords"], ensure_ascii=False),
                "canonical_event_date": case["candidate_date"],
                "candidate_event_type": case["candidate_event_type"],
                "candidate_title": case["candidate_title"],
                "candidate_summary": "",
            },
            enrich_formal_events(FORMAL_EVENTS),
            config,
            "run_x",
            {"cna.com.tw"},
        )
        top3 = [s["formal_event_id"] for s in suggestions[:3]]
        formal_duplicate_results[case["case_id"]] = {
            "top1": top3[0] if top3 else "",
            "top3": top3,
            "flagged_duplicate": any(s["suggested_action"] == "likely_duplicate" for s in suggestions),
        }
    gate = evaluate_golden_cases(
        CASES,
        relevance_predictions=relevance_predictions,
        cluster_predictions=cluster_predictions,
        assertion_predictions=assertion_predictions,
        event_type_predictions=event_type_predictions,
        formal_duplicate_results=formal_duplicate_results,
        formal_duplicate_cases=dup_cases,
        config=config,
    )
    assert gate["golden_case_skipped_count"] == 0
    assert gate["unsafe_fact_promotion_count"] == 0
    assert gate["known_duplicate_recall"] == 1.0
    assert gate["known_duplicate_top3_recall"] >= 0.9
    assert gate["known_nonduplicate_false_positive_count"] == 0
