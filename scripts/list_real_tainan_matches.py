"""Read-only: list July articles that ElectionClassifier would match for Tainan."""

from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.election_classifier import ElectionClassifier


def main():
    classifier = ElectionClassifier(ROOT / "config" / "election_watch.yaml")
    conn = sqlite3.connect(f"file:{ROOT / 'data' / 'news.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, source_name, category, title, url, published_at, "
        "COALESCE(summary,'') summary FROM articles "
        "WHERE published_at >= '2026-07-01' AND published_at <= '2026-07-31 23:59:59' "
        "ORDER BY published_at, id"
    ).fetchall()
    matched = []
    for r in rows:
        results = classifier.classify_article(r["title"], r["category"], r["source_name"])
        for res in results:
            if res["city"] == "tainan":
                matched.append((r, res))
    print(f"tainan_matched={len(matched)}")
    for r, res in matched:
        print(
            f"{r['id']}\t{r['published_at']}\t{r['source_name']}\t"
            f"{res['relevance']}\t{','.join(res.get('matched_people', []))}\t"
            f"{','.join(res.get('matched_issues', []))}\t{r['title'][:80]}\t{r['url']}"
        )
    conn.close()


if __name__ == "__main__":
    main()
