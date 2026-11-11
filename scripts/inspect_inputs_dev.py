"""Read-only dev-time inspection of the three SQLite databases.

This script is a temporary development aid and performs no writes.
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def dump(db_path: Path, sample_tables: list[str] | None = None):
    print(f"=== {db_path.name} ===")
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = [
            r[0]
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
            ).fetchall()
        ]
        print("tables:", tables)
        for t in tables:
            cols = [
                (r[1], r[2], r[5])
                for r in conn.execute(f"PRAGMA table_info('{t}')").fetchall()
            ]
            count = conn.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
            print(f"  {t}: rows={count} cols={cols}")
            if sample_tables and t in sample_tables:
                rows = conn.execute(f"SELECT * FROM '{t}' LIMIT 3").fetchall()
                for row in rows:
                    print("    sample:", json.dumps(dict(row), ensure_ascii=False, default=str)[:500])
    finally:
        conn.close()


def main():
    dump(ROOT / "data" / "news.db", sample_tables=["articles", "article_matches"])
    dump(ROOT / "data" / "election_watch.db", sample_tables=["article_matches"])
    dump(ROOT / "data" / "election_context.db", sample_tables=["elections", "sources"])


if __name__ == "__main__":
    main()
