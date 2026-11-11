"""Find every SQLite database containing an article_matches table."""

from __future__ import annotations

import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def main():
    hits = []
    for path in ROOT.rglob("*.db"):
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            try:
                row = conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN "
                    "('article_matches','articles','scan_state') ORDER BY name"
                ).fetchall()
                if row:
                    info = {}
                    for (t,) in row:
                        info[t] = conn.execute(f"SELECT COUNT(*) FROM '{t}'").fetchone()[0]
                    hits.append((str(path.relative_to(ROOT)), info))
            finally:
                conn.close()
        except Exception:
            pass
    for p, info in hits:
        print(p, info)
    print("total_db_files_with_matches:", len(hits))


if __name__ == "__main__":
    main()
