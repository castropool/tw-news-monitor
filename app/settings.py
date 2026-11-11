"""Small, shared environment configuration for production and CLI entrypoints."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv


def load_environment(project_root: str | Path | None = None) -> Path:
    """Load the project ``.env`` once at the program boundary.

    ``override=False`` preserves explicit process environment values, which
    is important for scheduled tasks and isolated tests.
    """
    root = Path(project_root or Path(__file__).resolve().parent.parent).resolve()
    load_dotenv(root / ".env", override=False)
    return root


def _bool_env(name: str, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def _path_from_env(project_root: Path, name: str, default: str) -> Path:
    value = os.getenv(name, default).strip() or default
    path = Path(value)
    return path if path.is_absolute() else project_root / path


@dataclass(frozen=True, slots=True)
class Settings:
    project_root: Path
    news_db_path: Path
    sources_config_path: Path
    log_level: str
    news_catchup_enabled: bool
    news_catchup_max_minutes: int
    date_only_recovery_max_gap_hours: int
    disable_feishu_send: bool
    cmd_gateway_host: str
    cmd_gateway_port: int
    cmd_gateway_allow_non_loopback: bool
    cmd_gateway_max_concurrency: int
    cmd_gateway_max_body_bytes: int
    summarizer_base_url: str
    summarizer_model: str

    @property
    def database_path(self) -> Path:
        """Legacy alias retained for older callers."""
        return self.news_db_path


def get_settings(
    project_root: str | Path | None = None, *, load_env: bool = True
) -> Settings:
    root = Path(project_root or Path(__file__).resolve().parent.parent).resolve()
    if load_env:
        load_environment(root)
    news_db_value = os.getenv("NEWS_DB_PATH") or os.getenv("DATABASE_PATH") or "data/news.db"
    db_path = Path(news_db_value)
    if not db_path.is_absolute():
        db_path = root / db_path
    return Settings(
        project_root=root,
        news_db_path=db_path,
        sources_config_path=_path_from_env(root, "SOURCES_CONFIG_PATH", "config/sources.yaml"),
        log_level=os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO",
        news_catchup_enabled=_bool_env("NEWS_CATCHUP_ENABLED", False),
        news_catchup_max_minutes=max(1, _int_env("NEWS_CATCHUP_MAX_MINUTES", 720)),
        date_only_recovery_max_gap_hours=max(
            1, _int_env("DATE_ONLY_RECOVERY_MAX_GAP_HOURS", 3)
        ),
        disable_feishu_send=_bool_env("DISABLE_FEISHU_SEND", False),
        cmd_gateway_host=os.getenv("CMD_GATEWAY_HOST", "127.0.0.1").strip() or "127.0.0.1",
        cmd_gateway_port=max(1, _int_env("CMD_GATEWAY_PORT", 8765)),
        cmd_gateway_allow_non_loopback=_bool_env("CMD_GATEWAY_ALLOW_NON_LOOPBACK", False),
        cmd_gateway_max_concurrency=max(1, min(8, _int_env("CMD_GATEWAY_MAX_CONCURRENCY", 1))),
        cmd_gateway_max_body_bytes=max(1024, min(2 * 1024 * 1024, _int_env("CMD_GATEWAY_MAX_BODY_BYTES", 1 * 1024 * 1024))),
        summarizer_base_url=os.getenv("SUMMARIZER_BASE_URL", "http://127.0.0.1:8765/v1").strip(),
        summarizer_model=os.getenv("SUMMARIZER_MODEL", "deepseek/deepseek-v4-flash").strip(),
    )
