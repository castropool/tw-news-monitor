"""Military-topic configuration and conservative noise filtering.

Only sources explicitly marked as military channels are eligible.  This
module never attempts to discover military news in general politics feeds.
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from pathlib import Path

import yaml


logger = logging.getLogger(__name__)

DEFAULT_CONFIG_PATH = "config/military.yaml"
MILITARY_TOPIC = "military"
MILITARY_SOURCE_TYPES = frozenset(
    {"commercial_military", "official_military"}
)


def load_military_config(config_path: str | Path | None = None) -> dict:
    """Load validated military filtering configuration.

    Missing or malformed configuration disables the feature so a military
    configuration problem cannot stop the existing monitor.
    """

    if config_path is None:
        config_path = Path(__file__).resolve().parents[2] / DEFAULT_CONFIG_PATH
    path = Path(config_path)
    disabled = {
        "enabled": False,
        "keep_keywords": [],
        "drop_phrases": [],
        "noise_mode": "drop_before_save",
    }
    if not path.exists():
        return disabled
    try:
        with path.open(encoding="utf-8") as stream:
            config = yaml.safe_load(stream)
    except Exception as exc:  # noqa: BLE001 - configuration must fail closed
        logger.warning("Failed to load military config %s: %s", path, exc)
        return disabled
    if not isinstance(config, dict):
        return disabled
    keep_keywords = config.get("keep_keywords")
    drop_phrases = config.get("drop_phrases")
    if not isinstance(keep_keywords, list) or not isinstance(drop_phrases, list):
        logger.warning("Invalid military config %s: keyword lists are required", path)
        return disabled
    return {
        "enabled": bool(config.get("enabled", False)),
        "keep_keywords": _clean_terms(keep_keywords),
        "drop_phrases": _clean_terms(drop_phrases),
        "noise_mode": (
            "exclude_from_delivery"
            if str(config.get("noise_mode", "drop_before_save")).strip().lower()
            == "exclude_from_delivery"
            else "drop_before_save"
        ),
    }


def _clean_terms(values: list) -> list[str]:
    return [str(value).strip() for value in values if str(value).strip()]


def is_military_source(source: dict) -> bool:
    """Return True only for an explicitly configured military channel."""

    return (
        source.get("topic") == MILITARY_TOPIC
        and source.get("military_source_type") in MILITARY_SOURCE_TYPES
    )


def _sources_config_path(sources_config_path: str | Path | None = None) -> Path:
    if sources_config_path is not None:
        path = Path(sources_config_path)
    else:
        configured = os.getenv("SOURCES_CONFIG_PATH", "").strip()
        path = Path(configured) if configured else Path(__file__).resolve().parents[2] / "config/sources.yaml"
    if not path.is_absolute():
        path = Path(__file__).resolve().parents[2] / path
    return path.resolve()


@lru_cache(maxsize=8)
def _military_source_ids_cached(path_str: str) -> frozenset[str]:
    path = Path(path_str)
    try:
        with path.open(encoding="utf-8-sig") as stream:
            data = yaml.safe_load(stream)
        sources = data.get("sources") if isinstance(data, dict) else None
        if not isinstance(sources, list):
            raise ValueError("sources.yaml must contain a sources list")
        return frozenset(
            entry["id"]
            for entry in sources
            if isinstance(entry, dict)
            and entry.get("topic") == MILITARY_TOPIC
            and entry.get("enabled", True) is not False
            and entry.get("id")
        )
    except Exception as exc:  # noqa: BLE001 - whitelist must fail closed
        logger.warning("Military source id list unavailable: %s", exc)
        return frozenset()


def clear_military_source_cache() -> None:
    _military_source_ids_cached.cache_clear()


def military_source_ids(
    sources_config_path: str | Path | None = None,
) -> frozenset[str]:
    """Return the configured military-channel source ids (sources.yaml topic=military).

    The whitelist is the single fact source for the Word "军武动态" section:
    only articles from these ids may enter it.  Missing/unreadable config
    fails closed to an empty set so the section cannot ingest foreign news.
    """

    return _military_source_ids_cached(str(_sources_config_path(sources_config_path)))


def is_military_source_article(
    article,
    source_ids: frozenset[str] | None = None,
    sources_config_path: str | Path | None = None,
) -> bool:
    """Return True only when an article came from a configured military channel."""

    allowed = source_ids if source_ids is not None else military_source_ids(sources_config_path)
    return getattr(article, "source_id", None) in allowed


def _contains_any(text: str, terms: list[str]) -> bool:
    folded = text.casefold()
    return any(term.casefold() in folded for term in terms)


def filter_military_articles(articles: list, config: dict | None = None) -> tuple[list, list]:
    """Split military-channel articles into kept and obvious noise.

    A military protection term always wins over a noise phrase.  Articles
    matching neither side are retained, deliberately favouring recall.
    """

    if not config or not config.get("enabled", False):
        return list(articles), []

    keep_keywords = list(config.get("keep_keywords") or [])
    drop_phrases = list(config.get("drop_phrases") or [])
    kept: list = []
    blocked: list = []
    for article in articles:
        title = getattr(article, "title", "") or ""
        summary = getattr(article, "summary", "") or ""
        text = f"{title} {summary}"
        protected = _contains_any(text, keep_keywords)
        noisy = _contains_any(text, drop_phrases)
        if noisy and not protected:
            blocked.append(article)
        else:
            kept.append(article)
    return kept, blocked
