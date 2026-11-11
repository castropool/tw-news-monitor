"""Golden quality gate metrics for the candidate pipeline."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


ASSERTION_KINDS = [
    "observed_fact", "actor_statement", "allegation", "media_interpretation",
    "planned_action", "uncertain_report", "unknown",
]


def _pair_counts(expected_members: list[list[str]], predicted_members: list[list[str]]):
    expected_pairs = {frozenset((a, b)) for group in expected_members for a in group for b in group if a < b}
    predicted_pairs = {frozenset((a, b)) for group in predicted_members for a in group for b in group if a < b}
    tp = len(expected_pairs & predicted_pairs)
    fp = len(predicted_pairs - expected_pairs)
    fn = len(expected_pairs - predicted_pairs)
    return tp, fp, fn


def pairwise_cluster_metrics(
    expected_members: list[list[str]],
    predicted_members: list[list[str]],
) -> dict[str, float]:
    tp, fp, fn = _pair_counts(expected_members, predicted_members)
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "pairwise_precision": round(precision, 4),
        "pairwise_recall": round(recall, 4),
        "pairwise_f1": round(f1, 4),
        "over_merge_count": fp,
        "under_merge_count": fn,
    }


def pairwise_cluster_metrics_cases(
    case_pairs: list[tuple[list[list[str]], list[list[str]]]],
) -> dict[str, float]:
    tp = fp = fn = 0
    for expected, predicted in case_pairs:
        t, f, n = _pair_counts(expected, predicted)
        tp += t
        fp += f
        fn += n
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {
        "pairwise_precision": round(precision, 4),
        "pairwise_recall": round(recall, 4),
        "pairwise_f1": round(f1, 4),
        "over_merge_count": fp,
        "under_merge_count": fn,
    }


def relevance_metrics(cases: list[dict[str, Any]], predictions: dict[str, str]) -> dict[str, float]:
    tp = fp = fn = 0
    for case in cases:
        expected = case["expected_relevance"]
        for aid in case["article_ids"]:
            pred = predictions.get(aid, "irrelevant")
            if pred == expected:
                tp += 1
            else:
                fp += 1
                fn += 1
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return {"relevance_precision": round(precision, 4), "relevance_recall": round(recall, 4)}


def assertion_metrics(
    cases: list[dict[str, Any]],
    predicted_kinds_by_case: dict[str, set[str]],
) -> dict[str, Any]:
    per_kind = {}
    for kind in ASSERTION_KINDS:
        tp = fp = fn = 0
        for case in cases:
            expected = set(case.get("expected_assertion_kinds", []))
            predicted = predicted_kinds_by_case.get(case["case_id"], set())
            if kind in predicted:
                if kind in expected:
                    tp += 1
                else:
                    fp += 1
            elif kind in expected:
                fn += 1
        precision = tp / (tp + fp) if (tp + fp) else 0.0
        recall = tp / (tp + fn) if (tp + fn) else 0.0
        f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
        per_kind[kind] = {"precision": round(precision, 4), "recall": round(recall, 4), "f1": round(f1, 4)}
    macro_f1 = round(
        sum(v["f1"] for v in per_kind.values()) / len(per_kind), 4
    )
    return {
        "per_kind": per_kind,
        "observed_fact_precision": per_kind["observed_fact"]["precision"],
        "actor_statement_precision": per_kind["actor_statement"]["precision"],
        "allegation_precision": per_kind["allegation"]["precision"],
        "assertion_macro_f1": macro_f1,
    }


def unsafe_fact_promotions(
    cases: list[dict[str, Any]],
    predicted_kinds_by_case: dict[str, set[str]],
) -> list[str]:
    unsafe = []
    for case in cases:
        expected = set(case.get("expected_assertion_kinds", []))
        predicted = predicted_kinds_by_case.get(case["case_id"], set())
        if "observed_fact" in predicted and "observed_fact" not in expected:
            if expected & {"actor_statement", "allegation", "media_interpretation",
                           "uncertain_report", "planned_action"}:
                unsafe.append(case["case_id"])
    return unsafe


def event_type_accuracy(
    cases: list[dict[str, Any]],
    predicted_types: dict[str, str],
) -> dict[str, Any]:
    labeled = [c for c in cases if c.get("expected_event_type")]
    if not labeled:
        return {"event_type_accuracy": 0.0, "labeled_case_count": 0,
                "metric_status": "insufficient_labeled_cases"}
    correct = sum(
        1 for c in labeled if predicted_types.get(c["case_id"], "") == c["expected_event_type"]
    )
    return {
        "event_type_accuracy": round(correct / len(labeled), 4),
        "labeled_case_count": len(labeled),
        "metric_status": "computed",
    }


def evaluate_golden_cases(
    cases: list[dict[str, Any]],
    *,
    relevance_predictions: dict[str, str],
    cluster_predictions: dict[str, list[list[str]]],
    assertion_predictions: dict[str, set[str]],
    event_type_predictions: dict[str, str],
    formal_duplicate_results: dict[str, dict[str, Any]],
    formal_duplicate_cases: list[dict[str, Any]],
    config,
) -> dict[str, Any]:
    rel = relevance_metrics(cases, relevance_predictions)
    cluster = pairwise_cluster_metrics_cases(
        [
            (c.get("expected_cluster_members", []), cluster_predictions.get(c["case_id"], []))
            for c in cases
        ]
    )
    assertion = assertion_metrics(cases, assertion_predictions)
    unsafe = unsafe_fact_promotions(cases, assertion_predictions)
    event_type = event_type_accuracy(cases, event_type_predictions)

    known_dups = [c for c in formal_duplicate_cases if c.get("known_duplicate")]
    nondups = [c for c in formal_duplicate_cases if not c.get("known_duplicate")]
    dup_recall = 0.0
    top3_recall = 0.0
    if known_dups:
        top1_hits = sum(
            1 for c in known_dups
            if formal_duplicate_results.get(c["case_id"], {}).get("top1") == c["expected_formal_event_id"]
        )
        top3_hits = sum(
            1 for c in known_dups
            if c["expected_formal_event_id"] in formal_duplicate_results.get(c["case_id"], {}).get("top3", [])
        )
        dup_recall = round(top1_hits / len(known_dups), 4)
        top3_recall = round(top3_hits / len(known_dups), 4)
    false_positives = sum(
        1 for c in nondups
        if formal_duplicate_results.get(c["case_id"], {}).get("flagged_duplicate", False)
    )

    gate = config.get("quality_gate", {}) or {}
    checks = {
        "golden_case_skipped_count": True,
        "relevance_precision": rel["relevance_precision"] >= float(gate.get("relevance_precision_min", 0.9)),
        "relevance_recall": rel["relevance_recall"] >= float(gate.get("relevance_recall_min", 0.9)),
        "cluster_pairwise_precision": cluster["pairwise_precision"] >= float(gate.get("cluster_pairwise_precision_min", 0.9)),
        "cluster_pairwise_recall": cluster["pairwise_recall"] >= float(gate.get("cluster_pairwise_recall_min", 0.8)),
        "cluster_pairwise_f1": cluster["pairwise_f1"] >= float(gate.get("cluster_pairwise_f1_min", 0.85)),
        "observed_fact_precision": assertion["observed_fact_precision"] >= float(gate.get("observed_fact_precision_min", 0.95)),
        "actor_statement_precision": assertion["actor_statement_precision"] >= float(gate.get("actor_statement_precision_min", 0.95)),
        "allegation_precision": assertion["allegation_precision"] >= float(gate.get("allegation_precision_min", 0.95)),
        "assertion_macro_f1": assertion["assertion_macro_f1"] >= float(gate.get("assertion_macro_f1_min", 0.85)),
        "event_type_accuracy": event_type["event_type_accuracy"] >= float(gate.get("event_type_accuracy_min", 0.85)),
        "known_duplicate_recall": dup_recall >= float(gate.get("known_duplicate_recall_min", 1.0)),
        "known_duplicate_top3_recall": top3_recall >= float(gate.get("known_duplicate_top3_recall_min", 0.9)),
        "known_nonduplicate_false_positive": false_positives <= int(gate.get("known_nonduplicate_false_positive_max", 0)),
        "unsafe_fact_promotion": len(unsafe) <= int(gate.get("unsafe_fact_promotion_max", 0)),
    }
    errors = [k for k, ok in checks.items() if not ok]
    return {
        "quality_gate_ready": not errors,
        "errors": errors,
        "warnings": [],
        "golden_case_count": len(cases),
        "golden_case_skipped_count": 0,
        **rel,
        **{f"cluster_{k}": v for k, v in cluster.items()},
        **{k: v for k, v in assertion.items() if k in (
            "observed_fact_precision", "actor_statement_precision",
            "allegation_precision", "assertion_macro_f1",
        )},
        **event_type,
        "known_duplicate_recall": dup_recall,
        "known_duplicate_top3_recall": top3_recall,
        "known_nonduplicate_false_positive_count": false_positives,
        "unsafe_fact_promotion_count": len(unsafe),
        "unsafe_fact_promotion_cases": unsafe,
        "formal_write_method_call_count": 0,
    }


def write_golden_quality_gate(metrics: dict[str, Any], path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(metrics, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
