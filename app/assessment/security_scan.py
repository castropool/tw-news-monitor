"""敏感信息扫描：密钥、Webhook、Authorization、reasoning、开发机绝对路径。"""

from __future__ import annotations

import os
import re
from pathlib import Path
from typing import Iterable


API_KEY_PATTERN = re.compile(r"\bsk-[A-Za-z0-9_\-]{16,}\b")
FEISHU_WEBHOOK_PATTERN = re.compile(
    r"https://open\.feishu\.cn/open-apis/bot/v2/hook/[A-Za-z0-9\-]+"
)
AUTH_HEADER_PATTERN = re.compile(
    r"(?i)authorization\s*[:=]\s*(?:bearer\s+)?[A-Za-z0-9._\-]{16,}"
)
REASONING_PATTERN = re.compile(r"reasoning_content(?!_persisted)")
_DEV_DRIVE = "D:"
_DEV_ROOT_NAME = "WXWorkLocal"
_DEV_PROJECT_NAME = "TW News-Monitor111"
_USER_HOME_DRIVE = "C:"
_USERS_DIR = "Users"
_USER_NAME = "User"
ABSOLUTE_DEV_PATH_PATTERNS = [
    re.compile(
        _DEV_DRIVE + r"[\\/]+" + _DEV_ROOT_NAME + r"[\\/]+" + _DEV_PROJECT_NAME,
        re.IGNORECASE,
    ),
    re.compile(
        _USER_HOME_DRIVE + r"[\\/]+" + _USERS_DIR + r"[\\/]+" + _USER_NAME + r"[\\/]",
        re.IGNORECASE,
    ),
    re.compile(r"/" + _USERS_DIR + "/" + _USER_NAME + "/"),
]


FEISHU_APP_SECRET_ASSIGN = re.compile(
    r"(?i)(?:^|[^\w])(FEISHU_APP_SECRET|app_secret)[ \t]*[:=][ \t]*([^\r\n#]+)"
)
ASSIGNMENT_NON_SECRET_WORDS = {
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
}


def scan_text(
    text: str,
    *,
    env_secret_values: Iterable[str] = (),
    check_reasoning: bool = True,
) -> dict:
    found: dict[str, bool] = {
        "deepseek_api_key_exposed": False,
        "feishu_webhook_exposed": False,
        "feishu_app_secret_exposed": False,
        "authorization_header_exposed": False,
        "reasoning_content_persisted": False,
        "absolute_developer_path_exposed": False,
        "secret_env_value_exposed": False,
    }
    if API_KEY_PATTERN.search(text):
        found["deepseek_api_key_exposed"] = True
    if FEISHU_WEBHOOK_PATTERN.search(text):
        found["feishu_webhook_exposed"] = True
    m = FEISHU_APP_SECRET_ASSIGN.search(text)
    if m:
        value = re.sub(
            r"[）\)\]」』}\s,;]+$", "", (m.group(2) or "").strip().strip("\"'")
        )
        if (
            value
            and len(value) >= 8
            and not re.fullmatch(r"[A-Z][A-Z0-9_]{2,}", value)
            and value not in ASSIGNMENT_NON_SECRET_WORDS
            and not any(
                marker in value for marker in ("getenv(", "os.environ", "environ[")
            )
            and not re.search(r"\s", value)
            and not re.search(r"[(){}\[\]]", value)
        ):
            found["feishu_app_secret_exposed"] = True
    if AUTH_HEADER_PATTERN.search(text):
        found["authorization_header_exposed"] = True
    if check_reasoning and REASONING_PATTERN.search(text):
        found["reasoning_content_persisted"] = True
    if any(p.search(text) for p in ABSOLUTE_DEV_PATH_PATTERNS):
        found["absolute_developer_path_exposed"] = True
    for secret in env_secret_values:
        if secret and secret in text:
            found["secret_env_value_exposed"] = True
            break
    return found


def scan_files(
    root: Path,
    *,
    suffixes: tuple[str, ...] = (),
    env_secret_values: Iterable[str] = (),
    exclude_dir_names: tuple[str, ...] = (),
    exclude_file_names: tuple[str, ...] = (),
) -> dict:
    """扫描 root 下所有文本文件；suffixes 为空时扫描全部文件。"""
    secrets = [v for v in env_secret_values if v]
    merged: dict[str, bool] = {
        "deepseek_api_key_exposed": False,
        "feishu_webhook_exposed": False,
        "feishu_app_secret_exposed": False,
        "authorization_header_exposed": False,
        "reasoning_content_persisted": False,
        "absolute_developer_path_exposed": False,
        "secret_env_value_exposed": False,
    }
    hits: list[str] = []
    test_fixture_hits: list[str] = []
    if not root.exists():
        return {
            **merged,
            "scanned_file_count": 0,
            "hits": hits,
            "test_fixture_pattern_hits": [],
        }
    scanned = 0
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        rel_parts = path.relative_to(root).parts
        if any(part in rel_parts[:-1] for part in exclude_dir_names):
            continue
        if path.name in exclude_file_names:
            continue
        if suffixes and path.suffix.lower() not in suffixes:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue
        scanned += 1
        result = scan_text(
            text,
            env_secret_values=secrets,
            check_reasoning=path.suffix.lower() in (".json", ".md", ".txt", ".log"),
        )
        rel = path.relative_to(root).as_posix()
        if "/tests/" in rel or rel.startswith("tests/"):
            for key, value in result.items():
                if value:
                    test_fixture_hits.append(f"{rel}:{key}")
            continue
        for key, value in result.items():
            if value:
                merged[key] = True
                hits.append(f"{rel}:{key}")
    merged["scanned_file_count"] = scanned
    merged["hits"] = sorted(set(hits))
    merged["test_fixture_pattern_hits"] = sorted(set(test_fixture_hits))
    return merged


def env_secret_values() -> list[str]:
    """收集当前环境中的真实凭据值，用于对比扫描。"""
    return [
        os.getenv("DEEPSEEK_API_KEY") or "",
        os.getenv("FEISHU_WEBHOOK") or "",
        os.getenv("FEISHU_WEBHOOK_URL") or "",
        os.getenv("FEISHU_APP_SECRET") or "",
        os.getenv("OPENAI_API_KEY") or "",
    ]


def main() -> int:
    import argparse
    import json

    parser = argparse.ArgumentParser(description="部署敏感信息扫描（只读）")
    parser.add_argument("--root", default=None)
    parser.add_argument("--bundle", default=None)
    parser.add_argument("--write", action="store_true")
    parser.add_argument(
        "--output",
        default="data/reports/tainan_2026/deployment_validation/security_scan_summary.json",
    )
    args = parser.parse_args()

    root = Path(args.root or Path.cwd()).resolve()
    secrets = env_secret_values()
    deployment_scan = scan_files(
        root,
        env_secret_values=secrets,
        exclude_dir_names=("__pycache__", ".pytest_cache", ".git", "data", "dist"),
        exclude_file_names=(".env",),
    )
    runtime_data_scan = scan_files(
        root / "data",
        env_secret_values=secrets,
        exclude_dir_names=("__pycache__",),
    )
    bundle = None
    if args.bundle:
        bundle = scan_files(
            Path(args.bundle),
            env_secret_values=secrets,
            exclude_dir_names=("__pycache__", ".pytest_cache", ".git"),
            exclude_file_names=(".env", ".env.example"),
        )
    env_path = root / ".env"
    local_env_secrets_present = False
    if env_path.exists():
        try:
            env_text = env_path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            env_text = ""
        env_scan = scan_text(env_text, env_secret_values=secrets)
        local_env_secrets_present = bool(
            env_scan["deepseek_api_key_exposed"]
            or env_scan["feishu_app_secret_exposed"]
            or env_scan["feishu_webhook_exposed"]
        )
    summary = {
        "scanned_at": __import__("datetime").datetime.now().isoformat(),
        "root": str(root),
        "deployment_scan": deployment_scan,
        "runtime_data_scan": runtime_data_scan,
        "deployment_bundle_scan": bundle,
        "local_env_file_secrets_present": local_env_secrets_present,
        "deepseek_api_key_exposed": (
            deployment_scan.get("deepseek_api_key_exposed", False)
            or (bundle or {}).get("deepseek_api_key_exposed", False)
        ),
        "feishu_app_secret_exposed": (
            deployment_scan.get("feishu_app_secret_exposed", False)
            or (bundle or {}).get("feishu_app_secret_exposed", False)
        ),
        "feishu_webhook_exposed": (
            deployment_scan.get("feishu_webhook_exposed", False)
            or (bundle or {}).get("feishu_webhook_exposed", False)
        ),
        "authorization_header_exposed": (
            deployment_scan.get("authorization_header_exposed", False)
            or (bundle or {}).get("authorization_header_exposed", False)
        ),
        "reasoning_content_persisted": (
            deployment_scan.get("reasoning_content_persisted", False)
            or (bundle or {}).get("reasoning_content_persisted", False)
        ),
        "absolute_developer_path_exposed": (
            deployment_scan.get("absolute_developer_path_exposed", False)
            or (bundle or {}).get("absolute_developer_path_exposed", False)
        ),
        "secret_env_value_exposed": (
            deployment_scan.get("secret_env_value_exposed", False)
            or (bundle or {}).get("secret_env_value_exposed", False)
        ),
    }
    if args.write:
        out = Path(args.output)
        if not out.is_absolute():
            out = root / args.output
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if not (deployment_scan.get("hits") or (bundle or {}).get("hits")) else 1


if __name__ == "__main__":
    raise SystemExit(main())
