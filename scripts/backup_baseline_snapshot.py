"""Create a pre-development backup snapshot for the candidate pipeline phase.

Only creates new files under data/backups/candidate_pipeline_predevelopment_*/
and never copies secrets (the .env file is deliberately excluded).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def sha256_file(path: Path) -> str | None:
    if not path.exists():
        return None
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def dump_sqlite_schema(path: Path) -> dict | None:
    if not path.exists():
        return None
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        tables = {}
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        ).fetchall():
            columns = [
                dict(zip(("cid", "name", "type", "notnull", "default_value", "pk"), row))
                for row in conn.execute(f"PRAGMA table_info('{name}')").fetchall()
            ]
            row_count = conn.execute(f"SELECT COUNT(*) FROM '{name}'").fetchone()[0]
            tables[name] = {"columns": columns, "row_count": row_count}
        return {
            "path": str(path),
            "tables": tables,
            "sqlite_version": conn.execute("SELECT sqlite_version()").fetchone()[0],
        }
    finally:
        conn.close()


def main() -> None:
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_dir = ROOT / "data" / "backups" / f"candidate_pipeline_predevelopment_{ts}"
    out_dir.mkdir(parents=True, exist_ok=True)

    formal_seed_dir = ROOT / "data" / "election_seed" / "tainan_2026"
    release_zip = ROOT / "dist" / "releases" / "tainan-assessment-offline-rc1.zip"

    formal_seed_files = [
        "election.json",
        "actors.yaml",
        "events.jsonl",
        "sources.jsonl",
        "polls.jsonl",
        "poll_source_links.jsonl",
        "taxonomy.yaml",
        "initial_snapshot.json",
        "snapshot_history.jsonl",
    ]

    code_files = [
        "app/database.py",
        "app/models.py",
        "app/election_watch.py",
        "app/election_report.py",
        "app/election_event_merge.py",
        "app/election_fact_store.py",
        "app/election_classifier.py",
        "app/election_context/repository.py",
        "app/election_context/bootstrap.py",
        "app/election_context/importer.py",
        "app/main.py",
    ]

    config_files = [
        "config/election_watch.yaml",
        "config/election_assessment.yaml",
        "config/election_manual_facts.json",
        "config/sources.yaml",
    ]

    def hashes(paths: list[Path]) -> dict[str, str | None]:
        result = {}
        for p in paths:
            key = str(p.relative_to(ROOT)).replace("\\", "/")
            result[key] = sha256_file(p)
        return result

    db_schemas = {
        "news.db": dump_sqlite_schema(ROOT / "data" / "news.db"),
        "election_watch.db": dump_sqlite_schema(ROOT / "data" / "election_watch.db"),
        "election_context.db": dump_sqlite_schema(ROOT / "data" / "election_context.db"),
    }

    manifest = {
        "backup_name": f"candidate_pipeline_predevelopment_{ts}",
        "created_at": datetime.now().isoformat(),
        "trigger": "candidate_pipeline_phase1_predevelopment_backup",
        "git_repository": False,
        "formal_data_hashes": hashes([formal_seed_dir / f for f in formal_seed_files]),
        "database_hashes": {
            "data/news.db": sha256_file(ROOT / "data" / "news.db"),
            "data/election_watch.db": sha256_file(ROOT / "data" / "election_watch.db"),
            "data/election_context.db": sha256_file(ROOT / "data" / "election_context.db"),
        },
        "frozen_release_hashes": {
            "dist/releases/tainan-assessment-offline-rc1.zip": sha256_file(release_zip),
            "release/release_manifest.json": sha256_file(
                ROOT / "release" / "release_manifest.json"
            ),
            "release/frozen_formal_data_manifest.json": sha256_file(
                ROOT / "release" / "frozen_formal_data_manifest.json"
            ),
        },
        "key_code_hashes": hashes([ROOT / p for p in code_files]),
        "config_file_hashes": hashes([ROOT / p for p in config_files]),
        "database_schemas": db_schemas,
        "notes": [
            "The .env file is deliberately excluded; no real secrets are copied.",
            "Formal seed files are not modified by this backup.",
        ],
    }

    manifest_path = out_dir / "predevelopment_manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    for name, schema in db_schemas.items():
        if schema is not None:
            (out_dir / f"schema_{name.replace('.', '_')}.json").write_text(
                json.dumps(schema, ensure_ascii=False, indent=2), encoding="utf-8"
            )

    print(f"backup_dir={out_dir}")
    print(f"manifest={manifest_path}")
    print(f"news_db_sha256={manifest['database_hashes']['data/news.db']}")
    print(
        "frozen_release_sha256="
        f"{manifest['frozen_release_hashes']['dist/releases/tainan-assessment-offline-rc1.zip']}"
    )


if __name__ == "__main__":
    main()
