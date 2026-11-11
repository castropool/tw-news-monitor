"""Read-only export of the 79 July 2026 Tainan-matched articles for adjudication."""

from __future__ import annotations

import json
import sqlite3
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.election_classifier import ElectionClassifier


OUT = ROOT / "data" / "election_candidates" / "tainan_2026" / "quality_calibration"


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    classifier = ElectionClassifier(ROOT / "config" / "election_watch.yaml")
    conn = sqlite3.connect(f"file:{ROOT / 'data' / 'news.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, source_id, source_name, category, title, url, published_at, "
        "COALESCE(summary,'') summary FROM articles "
        "WHERE published_at >= '2026-07-01' AND published_at <= '2026-07-31 23:59:59' "
        "ORDER BY published_at, id"
    ).fetchall()
    matched = []
    for r in rows:
        results = classifier.classify_article(r["title"], r["category"], r["source_name"])
        for res in results:
            if res["city"] == "tainan":
                matched.append(
                    {
                        "article_id": r["id"],
                        "title": r["title"],
                        "summary": r["summary"],
                        "source_name": r["source_name"],
                        "category": r["category"],
                        "url": r["url"],
                        "published_at": r["published_at"],
                        "classifier_match": {
                            "relevance": res["relevance"],
                            "matched_people": res.get("matched_people", []),
                            "matched_parties": res.get("matched_parties", []),
                            "matched_issues": res.get("matched_issues", []),
                            "matched_terms": res.get("matched_terms", []),
                            "matched_basis": res.get("matched_basis", []),
                        },
                    }
                )
                break
    conn.close()
    payload = {
        "scope": "2026-07-01..2026-07-31",
        "matched_count": len(matched),
        "articles": matched,
    }
    (OUT / "july_2026_article_adjudication.base.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"exported={len(matched)} -> {OUT / 'july_2026_article_adjudication.base.json'}")


if __name__ == "__main__":
    main()
