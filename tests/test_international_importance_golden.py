"""Wave 4 国际重要性黄金样本门禁。"""

import json
from pathlib import Path

import pytest

from app.importance import load_rules, score_article


ROOT = Path(__file__).resolve().parent.parent
FIXTURE = ROOT / "tests" / "fixtures" / "international" / "golden_importance.jsonl"
RULES = load_rules(ROOT / "config" / "importance_rules.yaml")


def _rows():
    with FIXTURE.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def test_tier1_bonus_alone_does_not_make_reuters_important():
    result = score_article("Ordinary Reuters market note", "Reuters", "international", "", RULES)
    assert result.score < 65
    assert result.level == "normal"


@pytest.mark.parametrize("row", _rows(), ids=lambda row: row["case_id"])
def test_importance_gold_row(row):
    result = score_article(
        row["title"],
        row["source_id"],
        row.get("category", "international"),
        row.get("summary", ""),
        RULES,
    )
    assert result.level == row["expected_importance_level"]
    assert result.score >= row["expected_min_score"]
    assert (result.level in {"important", "critical"}) is row["expected_notification"]
    assert row.get("body_fetch_forbidden") is True


def test_importance_gold_has_balanced_minimum_and_no_hard_negative_highlights():
    rows = _rows()
    assert len(rows) >= 32
    assert sum(row["expected_relevant"] for row in rows) == 16
    assert sum(not row["expected_relevant"] for row in rows) == 16
    negative = [row for row in rows if not row["expected_relevant"]]
    assert all(row["expected_importance_level"] == "normal" for row in negative)

