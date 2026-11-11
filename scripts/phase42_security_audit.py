"""Exact-value credential scan for Phase 4.2 artifacts.

Only match counts and relative file paths are persisted. Secret values are never
printed or written to the audit artifact. The local .env credential source is
explicitly excluded from scan targets.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_ROOT = ROOT / "deployment" / "phase4" / "claim_evidence_remediation"
OUTPUT = AUDIT_ROOT / "credential_value_scan.json"


def load_env_values() -> dict[str, str]:
    values: dict[str, str] = {}
    env_path = ROOT / ".env"
    if not env_path.exists():
        return values
    for raw in env_path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        name, value = line.split("=", 1)
        values[name.strip()] = value.strip().strip('"').strip("'")
    return values


def iter_files(path: Path):
    if not path.exists():
        return
    if path.is_file():
        yield path
        return
    for file in path.rglob("*"):
        if not file.is_file():
            continue
        relative = file.relative_to(ROOT) if file.is_relative_to(ROOT) else file
        parts = {part.lower() for part in relative.parts}
        if ".git" in parts or file.name == ".env":
            continue
        yield file


def scan_scope(paths: list[Path], secrets: dict[str, str]) -> dict:
    files: dict[Path, None] = {}
    for path in paths:
        for file in iter_files(path) or []:
            files[file] = None
    deepseek_matches: set[str] = set()
    feishu_matches: set[str] = set()
    authorization_matches: set[str] = set()
    scanned = 0
    deepseek = secrets.get("DEEPSEEK_API_KEY", "")
    feishu_values = [
        secrets.get(name, "")
        for name in ("FEISHU_APP_SECRET", "FEISHU_WEBHOOK", "FEISHU_WEBHOOK_URL")
        if secrets.get(name, "")
    ]
    for file in sorted(files, key=lambda p: str(p).lower()):
        try:
            content = file.read_bytes()
        except OSError:
            continue
        scanned += 1
        label = (
            str(file.relative_to(ROOT)).replace("\\", "/")
            if file.is_relative_to(ROOT)
            else str(file)
        )
        if deepseek and deepseek.encode("utf-8") in content:
            deepseek_matches.add(label)
        if any(value.encode("utf-8") in content for value in feishu_values):
            feishu_matches.add(label)
        try:
            text = content.decode("utf-8")
        except UnicodeDecodeError:
            text = ""
        actual_values = [v for v in [deepseek, *feishu_values] if v]
        if any(
            re.search(
                r"Authorization\s*[:=]\s*(?:Bearer\s+)?" + re.escape(value),
                text,
                re.IGNORECASE,
            )
            for value in actual_values
        ):
            authorization_matches.add(label)
    return {
        "scanned_file_count": scanned,
        "deepseek_api_key_value_matches": len(deepseek_matches),
        "feishu_secret_value_matches": len(feishu_matches),
        "authorization_header_value_matches": len(authorization_matches),
        "matching_files": {
            "deepseek": sorted(deepseek_matches),
            "feishu": sorted(feishu_matches),
            "authorization": sorted(authorization_matches),
        },
    }


def main() -> int:
    secrets = load_env_values()
    source_paths = [
        ROOT / name
        for name in (
            "app",
            "config",
            "data",
            "docs",
            "prompts",
            "scripts",
            "tests",
        )
    ] + [ROOT / name for name in ("README.md", "README_DEPLOYMENT.md", "VERSION")]
    scopes = {
        "source_tree": source_paths,
        "rc4_bundle": [ROOT / "dist" / "tainan-assessment-production-rc4"],
        "rc4_archive": [
            ROOT / "dist" / "releases" / "tainan-assessment-production-rc4.zip"
        ],
        "rc4_extracted": [AUDIT_ROOT / "rc4_extract_validation"],
        "deployment_directory": [ROOT / "deployment"],
        "runtime_logs": [ROOT / "deployment" / "phase4" / "runtime", ROOT / "logs"],
        "llm_artifacts": [AUDIT_ROOT / "formal_live_attempt_01", AUDIT_ROOT / "formal_live_attempt_02"],
        "word_temp": [ROOT / "data" / "reports", ROOT / "deployment" / "phase4" / "pytest_tmp"],
    }
    results = {name: scan_scope(paths, secrets) for name, paths in scopes.items()}
    totals = {
        key: sum(result[key] for result in results.values())
        for key in (
            "deepseek_api_key_value_matches",
            "feishu_secret_value_matches",
            "authorization_header_value_matches",
        )
    }
    # Scopes overlap; success is based on every scope being zero, not the summed
    # number of unique files.
    payload = {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "credential_source_excluded": ".env",
        "credential_values_persisted": False,
        "deepseek_credential_present": bool(secrets.get("DEEPSEEK_API_KEY")),
        "feishu_secret_present": bool(
            secrets.get("FEISHU_APP_SECRET")
            or secrets.get("FEISHU_WEBHOOK")
            or secrets.get("FEISHU_WEBHOOK_URL")
        ),
        "scopes": results,
        "totals_across_overlapping_scopes": totals,
        "deepseek_api_key_value_matches": max(
            result["deepseek_api_key_value_matches"] for result in results.values()
        ),
        "feishu_secret_value_matches": max(
            result["feishu_secret_value_matches"] for result in results.values()
        ),
        "authorization_header_value_matches": max(
            result["authorization_header_value_matches"] for result in results.values()
        ),
    }
    payload["credential_scan_passed"] = all(
        payload[key] == 0
        for key in (
            "deepseek_api_key_value_matches",
            "feishu_secret_value_matches",
            "authorization_header_value_matches",
        )
    )
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "credential_scan_passed": payload["credential_scan_passed"],
                "deepseek_api_key_value_matches": payload[
                    "deepseek_api_key_value_matches"
                ],
                "feishu_secret_value_matches": payload[
                    "feishu_secret_value_matches"
                ],
                "authorization_header_value_matches": payload[
                    "authorization_header_value_matches"
                ],
            }
        )
    )
    return 0 if payload["credential_scan_passed"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
