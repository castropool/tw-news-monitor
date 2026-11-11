"""Write classifier parity report from the frozen golden article fixture."""

from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.election_candidates.candidate_models import MatchInfo
from app.election_candidates.classifier_parity import build_classifier_parity_report, write_parity_report
from app.election_candidates.config import load_config
from tests.election_candidates.golden_runner import article_from_row, load_articles


def main():
    config = load_config("config/election_candidate_pipeline.yaml")
    articles = load_articles()
    rows = list(articles.values())
    arts = [article_from_row(r) for r in rows if r.get("match")]
    expected = {}
    for r in rows:
        m = r.get("match") or {}
        expected[r["id"]] = MatchInfo(
            city=m.get("city", "tainan"),
            relevance=m.get("relevance", "low"),
            matched_people=list(m.get("matched_people", [])),
            matched_parties=list(m.get("matched_parties", [])),
            matched_issues=list(m.get("matched_issues", [])),
            matched_terms=list(m.get("matched_terms", [])),
            matched_basis=list(m.get("matched_basis", [])),
            region_match=bool(m.get("region_match", False)),
            election_context_match=bool(m.get("election_context_match", False)),
            match_score=float(m.get("match_score", 0)),
        )
    report = build_classifier_parity_report(arts, expected, config)
    out = ROOT / "data" / "election_candidates" / "tainan_2026" / "quality_calibration" / "classifier_parity_report.json"
    write_parity_report(report, out)
    print(json.dumps({k: v for k, v in report.items() if k != "classification_conflicts"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
