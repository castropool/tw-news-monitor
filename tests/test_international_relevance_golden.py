"""Wave 4 国际相关性黄金样本与可解释判定门禁。"""

import hashlib
import json
from pathlib import Path

import pytest

from app.international import (
    RelevanceDecision,
    classify_international,
    evaluate_relevance,
    load_international_config,
)


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "international" / "golden_relevance.jsonl"
CONFIG = load_international_config(ROOT / "config" / "international_media.yaml")


def _rows():
    with FIXTURE.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_plain_china_keyword_without_context_is_excluded():
    decision = evaluate_relevance(
        "China restaurant expands",
        "Local dining story",
        "Reuters",
        CONFIG,
    )
    assert isinstance(decision, RelevanceDecision)
    assert decision.relevant is False
    assert "context" in decision.reason.lower()


def test_relevance_decision_is_explainable_and_deterministic():
    first = evaluate_relevance(
        "China announces military drills near Taiwan",
        "The exercise affects the Taiwan Strait.",
        "Reuters",
        CONFIG,
    )
    second = evaluate_relevance(
        "China announces military drills near Taiwan",
        "The exercise affects the Taiwan Strait.",
        "Reuters",
        CONFIG,
    )
    assert first == second
    assert first.relevant is True
    assert first.tier == "taiwan_direct"
    assert "military" in first.topics
    assert first.entities
    assert first.reason
    assert first.rule_version
    assert len(first.input_hash) == hashlib.sha256().digest_size * 2


def test_extended_entities_and_topics_cover_dpp_kmt_pla_tao_and_taiwan_gdp():
    dpp = evaluate_relevance("DPP debates Taiwan election policy", "The KMT responded.", "Reuters", CONFIG)
    assert dpp.relevant is True
    assert "DPP" in dpp.entities
    assert "KMT" in dpp.entities
    assert "taiwan" in dpp.topics

    pla = evaluate_relevance("PLA conducts drills near Taiwan", "The exercise tests readiness.", "Reuters", CONFIG)
    assert pla.relevant is True
    assert "PLA" in pla.entities
    assert "Taiwan" in pla.entities
    assert "military" in pla.topics

    tao = evaluate_relevance(
        "Taiwan Affairs Office announces cross-strait policy",
        "The office outlined a new measure.",
        "Reuters",
        CONFIG,
    )
    assert tao.relevant is True
    assert "Taiwan Affairs Office" in tao.entities
    assert "policy" in tao.topics

    gdp = evaluate_relevance("Taiwan GDP growth slows", "Taipei officials cited exports.", "Reuters", CONFIG)
    assert gdp.relevant is True
    assert "Taiwan" in gdp.entities
    assert "economy" in gdp.topics

    china_gdp = evaluate_relevance("China GDP growth slows", "Domestic data missed forecasts.", "Reuters", CONFIG)
    assert china_gdp.relevant is False
    assert "economy" in china_gdp.topics


def test_classify_international_keeps_phase_one_topic_projection():
    taiwan = classify_international("Taiwan election debate continues", "", "Reuters", CONFIG)
    china = classify_international("China economy grows 5 percent", "", "Reuters", CONFIG)
    assert taiwan.topic == "us_taiwan"
    assert china.topic == "economy"


def test_load_config_fails_closed_on_known_schema_type_errors(tmp_path):
    cases = [
        "enabled: 'true'\n",
        "enabled: true\nrelevance_keywords: []\n",
        "enabled: true\nrelevance_keywords: {}\ndedup: []\n",
        "enabled: true\ndisplay_names: {Reuters: 1}\n",
        "enabled: true\ndedup: {synonyms: {drill: [exercise]}}\n",
        "enabled: true\nsource_bonus: {tier1: '3'}\n",
    ]
    for index, contents in enumerate(cases):
        path = tmp_path / f"invalid_{index}.yaml"
        path.write_text(contents, encoding="utf-8")
        assert load_international_config(path)["enabled"] is False


@pytest.mark.parametrize("row", _rows(), ids=lambda row: row["case_id"])
def test_relevance_gold_row(row):
    decision = evaluate_relevance(row["title"], row.get("summary"), row["source_id"], CONFIG)
    assert decision.relevant is row["expected_relevant"]
    assert decision.tier == row["expected_tier"]
    assert set(decision.topics) == set(row["expected_topics"])
    assert set(decision.entities) == set(row["expected_entities"])
    for fragment in row.get("expected_reason_contains", []):
        assert fragment.lower() in decision.reason.lower()
    assert row.get("body_fetch_forbidden") is True


def test_relevance_gold_has_required_balanced_categories():
    rows = _rows()
    assert len(rows) >= 32
    assert sum(row["expected_relevant"] for row in rows) == 16
    assert sum(not row["expected_relevant"] for row in rows) == 16
    required = {
        "military": 4,
        "diplomacy_policy_sanctions": 4,
        "semiconductor_trade": 4,
        "china_us_indopacific_security": 4,
        "china_restaurant_social": 3,
        "washington_local": 3,
        "taiwan_semiconductor_ambiguity": 3,
        "generic_semiconductor": 3,
        "pentagon_personnel": 2,
        "japan_domestic": 2,
    }
    for category, minimum in required.items():
        assert sum(row.get("gold_category") == category for row in rows) >= minimum
