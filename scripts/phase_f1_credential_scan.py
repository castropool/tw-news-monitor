"""Minimal Phase F1 credential scan (secrets only, no secret values printed)."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.assessment.security_scan import scan_text


OUT = ROOT / "data/election_candidates/tainan_2026/phase_f1"

# F1 ops scripts intentionally reference production/workspace absolute paths.
F1_OPS_FILES = {
    "scripts/deploy_candidate_production.ps1",
    "scripts/rollback_candidate_deployment.ps1",
    "scripts/phase_f1_baseline.py",
    "run_candidate_monitor.bat",
}
SOURCE_ROOTS = ("app/", "config/", "scripts/", "docs/", "prompts/")


def main() -> None:
    scanned = 0
    secret_hits: list[str] = []
    test_fixture_secret_hits: list[str] = []
    dev_path_hits: list[str] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(ROOT).as_posix()
        parts = path.relative_to(ROOT).parts
        if any(p in parts[:-1] for p in (
            "__pycache__", ".pytest_cache", "node_modules", ".git", ".venv",
            "candidate_deployment_backups", "fact_maintenance_audit",
        )):
            continue
        if path.name in (".env", ".env.example"):
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        if path.suffix.lower() in (".pyc", ".db", ".png", ".docx", ".zip"):
            continue
        scanned += 1
        result = scan_text(
            text,
            env_secret_values=(),
            check_reasoning=path.suffix.lower() in (".json", ".md", ".txt", ".log"),
        )
        secret_keys = {
            k: v for k, v in result.items()
            if k != "absolute_developer_path_exposed"
        }
        if any(secret_keys.values()):
            if rel.startswith("tests/") or "/tests/" in rel:
                test_fixture_secret_hits.append(rel)
            else:
                secret_hits.append(rel)
        if (
            result["absolute_developer_path_exposed"]
            and rel not in F1_OPS_FILES
            and rel.startswith(SOURCE_ROOTS)
        ):
            dev_path_hits.append(rel)

    payload = {
        "schema_version": "phase-f1.credential-scan.v1",
        "scanned_file_count": scanned,
        "secret_hits": secret_hits,
        "test_fixture_secret_hits": test_fixture_secret_hits,
        "dev_path_hits_in_source_outside_f1_ops": dev_path_hits,
        "f1_ops_files_with_expected_dev_paths": sorted(F1_OPS_FILES),
        "credential_scan_pass": not secret_hits and not dev_path_hits,
        "notes": [
            ".env / .env.example excluded from scanning; no secret values are printed.",
            "F1 ops scripts intentionally embed the production directory path for deployment/rollback.",
            "Test files containing fake secret patterns are recorded but do not fail the scan.",
            "Pre-existing data/ and deployment/ artifacts with developer paths are out of scope.",
        ],
    }
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "credential_scan.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    if not payload["credential_scan_pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
