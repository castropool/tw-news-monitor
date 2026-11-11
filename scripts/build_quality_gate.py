"""Compute and write the Phase 1.5 golden quality gate."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.election_candidates.config import load_config
from app.election_candidates.quality_gate import evaluate_golden_cases, write_golden_quality_gate
from tests.election_candidates.golden_runner import (
    enrich_formal_events,
    load_articles,
    load_cases,
    load_duplicate_cases,
    load_formal_events,
    run_case,
)


def main():
    config = load_config("config/election_candidate_pipeline.yaml")
    articles = load_articles()
    cases = load_cases()
    formal_events = load_formal_events()
    dup_cases = load_duplicate_cases()
    from app.election_candidates.formal_duplicate_checker import check_candidate_duplicates

    results = {c["case_id"]: run_case(c, articles, formal_events, config) for c in cases}
    relevance_predictions = {}
    for c in cases:
        r = results[c["case_id"]]
        for aid in c["article_ids"]:
            relevance_predictions[aid] = r["results"][0]["relevance_label"]
    cluster_predictions = {c["case_id"]: results[c["case_id"]]["clusters"] for c in cases}
    assertion_predictions = {
        c["case_id"]: set().union(*(x["assertion_kinds"] for x in results[c["case_id"]]["results"]))
        for c in cases
    }
    event_type_predictions = {
        c["case_id"]: results[c["case_id"]]["results"][0]["event_type"] for c in cases
    }
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
            enrich_formal_events(formal_events),
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
        cases,
        relevance_predictions=relevance_predictions,
        cluster_predictions=cluster_predictions,
        assertion_predictions=assertion_predictions,
        event_type_predictions=event_type_predictions,
        formal_duplicate_results=formal_duplicate_results,
        formal_duplicate_cases=dup_cases,
        config=config,
    )
    out = ROOT / "data" / "election_candidates" / "tainan_2026" / "quality_calibration" / "golden_quality_gate.json"
    write_golden_quality_gate(gate, out)
    print(json.dumps(gate, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
