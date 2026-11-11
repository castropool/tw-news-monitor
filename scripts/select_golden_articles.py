"""Read-only selection of real July 2026 Tainan-related articles."""

from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
KEYWORDS = [
    "台南", "陳亭妃", "謝龍介", "林俊憲", "王定宇", "李全教",
    "民調", "提名", "初選", "藍白", "侯友宜", "新北",
    "颱風", "災", "視察", "市政", "議員", "指控", "批評",
    "質疑", "造勢", "掃街", "拜票", "民進黨", "國民黨",
]


def main():
    conn = sqlite3.connect(f"file:{ROOT / 'data' / 'news.db'}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id, source_id, source_name, category, title, url, published_at, "
        "COALESCE(summary,'') summary FROM articles "
        "WHERE published_at >= '2026-07-01' AND published_at <= '2026-07-31 23:59:59' "
        "ORDER BY published_at, id"
    ).fetchall()
    selected = []
    for r in rows:
        text = f"{r['title']} {r['summary']}"
        hits = [k for k in KEYWORDS if k in text]
        if hits:
            selected.append((r, hits))
    print(f"total_july_articles={len(rows)}")
    print(f"selected={len(selected)}")
    for r, hits in selected:
        print(
            f"{r['id']}\t{r['published_at']}\t{r['source_name']}\t"
            f"{','.join(hits)}\t{r['title'][:70]}\t{r['url']}"
        )
    conn.close()


if __name__ == "__main__":
    main()
