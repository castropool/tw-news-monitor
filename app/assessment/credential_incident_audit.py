"""飞书凭据泄露事件审计（只读；不回显 Secret，不修改 Git 历史）。"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import datetime
from pathlib import Path
from typing import Iterable


INCIDENT_RECORDS = [
    {
        "incident_id": "feishu_secret_in_env_example_20260721",
        "location": ".env.example",
        "finding": "真实 FEISHU_APP_ID/FEISHU_APP_SECRET 曾写入示例配置（已在本轮前的工作中清除）",
        "remediated_in_worktree": True,
        "rotation_required": True,
        "detail_redacted": True,
    }
]

WEBHOOK_RE = re.compile(
    r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9\-]+"
)
DEEPSEEK_KEY_RE = re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")
APP_SECRET_ASSIGN_RE = re.compile(
    r"(?i)(?:^|[^\w])(FEISHU_APP_SECRET|app_secret)[ \t]*[:=][ \t]*([^\r\n#]+)"
)
WEBHOOK_ASSIGN_RE = re.compile(
    r"(?i)(?:^|[^\w])(FEISHU_WEBHOOK(?:_URL)?|webhook)[ \t]*[:=][ \t]*([^\r\n#]+)"
)
TOKEN_ASSIGN_RE = re.compile(
    r"(?i)(?:^|[^\w])(tenant_access_token|app_access_token)[ \t]*[:=][ \t]*[\"']?([A-Za-z0-9._\-]{8,})"
)
AUTH_HEADER_RE = re.compile(
    r"(?i)(?:^|[^\w])authorization[ \t]*[:=][ \t]*bearer[ \t]+[A-Za-z0-9._\-]{8,}"
)


def _assignment_hit(text: str, pattern: re.Pattern) -> bool:
    match = pattern.search(text)
    if not match:
        return False
    value = _clean_assignment_value(match.group(2) or "")
    if not value:
        return False
    # 环境变量名/参数名/类型标注不是 Secret 值
    if re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", value):
        return False
    if value in (
        "str",
        "int",
        "bool",
        "None",
        "text",
        "value",
        "key",
        "token",
        "app_secret",
        "app_id",
        "chat_id",
        "webhook",
        "webhook_url",
    ):
        return False
    if any(marker in value for marker in ("getenv(", "os.environ", "environ[")):
        return False
    # Secret 值不允许包含空白/括号/控制流关键字
    if re.search(r"\s", value) or re.search(r"[(){}\[\]]", value):
        return False
    if pattern is WEBHOOK_ASSIGN_RE:
        return value.startswith("http") or (
            len(value) >= 20 and not re.search(r"\s", value)
        )
    if pattern is APP_SECRET_ASSIGN_RE:
        return len(value) >= 8
    if pattern is TOKEN_ASSIGN_RE:
        return len(value) >= 8
    return True


def _clean_assignment_value(raw: str) -> str:
    value = raw.strip().strip("\"'")
    value = re.sub(r"[）\)\]」』}\s,;]+$", "", value)
    return value


def _is_test_path(path: Path, root: Path) -> bool:
    try:
        rel = path.relative_to(root).as_posix()
    except ValueError:
        return False
    return "/tests/" in rel or rel.startswith("tests/")


def _redact(line: str) -> str:
    return "<redacted line>"


def scan_text(text: str, *, known_secret_values: Iterable[str] = ()) -> list[str]:
    """返回命中的凭据类别（不返回值）。"""
    hits: list[str] = []
    if WEBHOOK_RE.search(text):
        hits.append("feishu_webhook_url")
    if DEEPSEEK_KEY_RE.search(text):
        hits.append("deepseek_api_key")
    if _assignment_hit(text, APP_SECRET_ASSIGN_RE):
        hits.append("feishu_app_secret_assignment")
    if _assignment_hit(text, WEBHOOK_ASSIGN_RE):
        hits.append("feishu_webhook_assignment")
    if _assignment_hit(text, TOKEN_ASSIGN_RE):
        hits.append("feishu_token_assignment")
    if AUTH_HEADER_RE.search(text):
        hits.append("authorization_header")
    for value in known_secret_values:
        if value and value in text:
            hits.append("known_secret_value")
            break
    return sorted(set(hits))


def scan_path(path: Path, *, known_secret_values: Iterable[str] = ()) -> list[str]:
    """扫描单个文本文件，返回脱敏位置列表。"""
    locations: list[str] = []
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return locations
    hits = scan_text(text, known_secret_values=known_secret_values)
    if not hits:
        return locations
    for i, line in enumerate(text.splitlines(), 1):
        if scan_text(line, known_secret_values=known_secret_values):
            locations.append(f"{path.as_posix()}:{i}:{_redact(line)}")
    return locations


def _walk_files(root: Path, *, suffixes: tuple[str, ...] = ()) -> list[Path]:
    out: list[Path] = []
    if not root.exists():
        return out
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if any(part in rel.split("/") for part in (".git", "__pycache__", ".pytest_cache", ".venv")):
            continue
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        out.append(path)
    return out


def _git_available(project_root: Path) -> bool:
    if not (project_root / ".git").exists():
        return False
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "rev-parse", "--is-inside-work-tree"],
            capture_output=True,
            text=True,
            timeout=30,
        )
    except Exception:  # noqa: BLE001
        return False
    return result.returncode == 0


def _git_tracked_files(project_root: Path) -> list[str]:
    if not (project_root / ".git").exists():
        return []
    try:
        result = subprocess.run(
            ["git", "-C", str(project_root), "ls-files"],
            capture_output=True,
            text=True,
            timeout=60,
        )
    except Exception:  # noqa: BLE001
        return []
    if result.returncode != 0:
        return []
    return [line for line in result.stdout.splitlines() if line]


def _git_history_text(project_root: Path) -> str:
    if not (project_root / ".git").exists():
        return ""
    try:
        result = subprocess.run(
            [
                "git",
                "-C",
                str(project_root),
                "log",
                "--all",
                "--patch",
                "--full-index",
                "-U0",
            ],
            capture_output=True,
            text=True,
            timeout=90,
        )
    except Exception:  # noqa: BLE001
        return ""
    return result.stdout if result.returncode == 0 else ""


def run_incident_audit(
    project_root: Path,
    *,
    bundle_root: Path | None = None,
    extra_scan_paths: Iterable[Path] = (),
    known_secret_values: Iterable[str] = (),
    rotation_acknowledged: bool = False,
) -> dict:
    secrets = [v for v in known_secret_values if v]
    matched: list[str] = []
    test_fixture_hits: list[str] = []
    local_env_secrets_present = False
    env_path = project_root / ".env"
    if env_path.exists():
        try:
            env_text = env_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            env_text = ""
        local_env_secrets_present = bool(scan_text(env_text, known_secret_values=secrets))

    worktree_files = _walk_files(
        project_root,
        suffixes=(
            ".py",
            ".json",
            ".jsonl",
            ".md",
            ".txt",
            ".yaml",
            ".yml",
            ".ps1",
            ".bat",
            ".toml",
            ".log",
            ".env",
            ".example",
        ),
    )
    for path in worktree_files:
        if path.name == ".env":
            continue  # 本地密钥文件由 local_env_secrets_present 单独记录，不计入泄露
        locations = scan_path(path, known_secret_values=secrets)
        if _is_test_path(path, project_root):
            test_fixture_hits.extend(locations)
        else:
            matched.extend(locations)

    generated_exposure = any(
        "data/reports/tainan_2026" in loc or "generated_reports" in loc or "pipeline_runs" in loc
        for loc in matched
    )

    bundle_locations: list[str] = []
    if bundle_root and bundle_root.exists():
        for path in _walk_files(bundle_root):
            locations = scan_path(path, known_secret_values=secrets)
            if _is_test_path(path, bundle_root):
                test_fixture_hits.extend(locations)
            else:
                bundle_locations.extend(locations)
    matched.extend(bundle_locations)

    for extra in extra_scan_paths:
        if extra.exists():
            for path in _walk_files(extra):
                locations = scan_path(path, known_secret_values=secrets)
                if _is_test_path(path, extra):
                    test_fixture_hits.extend(locations)
                else:
                    matched.extend(locations)

    tracked_files = _git_tracked_files(project_root)
    tracked_locations: list[str] = []
    for rel in tracked_files:
        path = project_root / rel
        if path.exists():
            tracked_locations.extend(scan_path(path, known_secret_values=secrets))
    git_history_locations: list[str] = []
    history_text = _git_history_text(project_root)
    if history_text and scan_text(history_text, known_secret_values=secrets):
        git_history_locations.append("git_history:<redacted commit/file>")

    git_repo_present = (project_root / ".git").exists()
    git_scan_status = (
        "scanned"
        if _git_available(project_root)
        else ("repository_missing" if not git_repo_present else "git_executable_unavailable")
    )
    current_worktree_exposure = bool(matched)
    git_tracked_exposure = bool(tracked_locations)
    git_history_exposure = bool(git_history_locations)
    deployment_bundle_exposure = bool(bundle_locations)
    incident_detected = True  # 已知事件记录（.env.example 曾含真实 Secret）
    rotation_required = incident_detected
    blocked = incident_detected and not rotation_acknowledged

    return {
        "audit_id": "feishu_credential_incident_audit",
        "audited_at": datetime.now().isoformat(),
        "incident_detected": incident_detected,
        "incident_records_redacted": [
            {k: v for k, v in record.items() if k != "finding"} | {"finding": "已脱敏"}
            for record in INCIDENT_RECORDS
        ],
        "current_worktree_exposure": current_worktree_exposure,
        "git_tracked_exposure": git_tracked_exposure,
        "git_history_exposure": git_history_exposure,
        "deployment_bundle_exposure": deployment_bundle_exposure,
        "generated_output_exposure": generated_exposure,
        "rotation_required": rotation_required,
        "rotation_acknowledged": rotation_acknowledged,
        "git_history_cleanup_recommended": git_history_exposure,
        "git_repository_present": git_repo_present,
        "git_scan_status": git_scan_status,
        "local_env_file_secrets_present": local_env_secrets_present,
        "production_delivery_blocked_until_rotation_acknowledged": blocked,
        "matched_locations_redacted": sorted(set(matched)),
        "test_fixture_pattern_matches_redacted": sorted(set(test_fixture_hits)),
        "secret_values_redacted": True,
    }


def write_audit(project_root: Path, audit: dict) -> Path:
    out = (
        project_root
        / "data"
        / "reports"
        / "tainan_2026"
        / "deployment_validation"
        / "feishu_credential_incident_audit.json"
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(audit, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return out


def main() -> int:
    import argparse
    import sys

    from .evidence_pack_builder import load_yaml

    parser = argparse.ArgumentParser(description="飞书凭据泄露事件审计（只读）")
    parser.add_argument("--config", default="config/election_assessment.yaml")
    parser.add_argument("--bundle", default=None)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()

    root = Path(args.config).resolve().parent.parent
    config = load_yaml(Path(args.config))
    rotated = (config.get("security") or {}).get(
        "feishu_credentials_rotated_after_incident"
    ) is True
    audit = run_incident_audit(
        root,
        bundle_root=Path(args.bundle) if args.bundle else None,
        rotation_acknowledged=rotated,
    )
    if args.write:
        write_audit(root, audit)
    print(json.dumps(audit, ensure_ascii=False, indent=2))
    return 0 if not audit["production_delivery_blocked_until_rotation_acknowledged"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
