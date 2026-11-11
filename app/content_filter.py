"""Content filtering for news outside the user's focus area.

The collector layer intentionally does NOT filter by keywords, so this
module is applied as an explicit delivery/ingest gate configured in
``config/content_filter.yaml``. It exists because the user asked to
exclude social trivia that frequently appears in economy feeds.
"""

import logging
from dataclasses import dataclass
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

DEFAULT_FILTER_PATH = "config/content_filter.yaml"
DEFAULT_CATEGORY_KEY = "_default"
FILTER_MODES = frozenset({"drop_before_save", "exclude_from_delivery"})


def load_content_filter(config_path: str | Path | None = None) -> dict:
    """Load the content filter config.

    Missing or invalid files yield a disabled config so collection
    never breaks because of a filter file.
    """
    if config_path is None:
        config_path = Path(__file__).resolve().parent.parent / DEFAULT_FILTER_PATH
    path = Path(config_path)
    if not path.exists():
        return {"enabled": False, "mode": "exclude_from_delivery", "categories": {}}
    try:
        with open(path, encoding="utf-8") as f:
            config = yaml.safe_load(f)
    except Exception as exc:
        logger.warning("Failed to load content filter %s: %s", path, exc)
        return {"enabled": False, "mode": "exclude_from_delivery", "categories": {}}
    if not isinstance(config, dict):
        config = {}
    config.setdefault("enabled", False)
    config.setdefault("mode", "exclude_from_delivery")
    config.setdefault("categories", {})
    return config


def _match_keywords(text: str, keywords: list) -> list[str]:
    """Return the subset of keywords found in text (case-insensitive)."""
    text_lower = text.lower()
    matched = []
    for kw in keywords or []:
        kw_str = str(kw).strip()
        if kw_str and kw_str.lower() in text_lower:
            matched.append(kw_str)
    return matched


def blocked_keywords(
    title: str, summary: str | None, category: str, config: dict
) -> list[str]:
    """Return keywords from config that block this article."""
    categories = config.get("categories", {}) or {}
    keywords = list(categories.get(DEFAULT_CATEGORY_KEY, []) or [])
    keywords += list(categories.get(category, []) or [])
    text = f"{title} {summary}"
    return _match_keywords(text, keywords)


@dataclass(slots=True)
class ContentFilterResult:
    """Structured result for the two content-filter modes.

    ``blocked`` contains matching articles regardless of mode.  The caller
    decides whether those articles are removed before persistence or only
    from the current delivery set.
    """

    kept: list
    blocked: list
    mode: str

    @property
    def filtered_before_save(self) -> list:
        return self.blocked if self.mode == "drop_before_save" else []

    @property
    def filtered_from_delivery(self) -> list:
        return self.blocked if self.mode == "exclude_from_delivery" else []


def filter_mode(config: dict | None) -> str:
    """Return a validated filter mode, failing closed to pre-save removal."""

    if not config or not config.get("enabled", False):
        return "disabled"
    mode = str(config.get("mode") or "drop_before_save").strip().lower()
    if mode not in FILTER_MODES:
        logger.warning(
            "Unknown content filter mode %r; using safe fallback exclude_from_delivery",
            mode,
        )
        return "exclude_from_delivery"
    return mode


def apply_content_filter(
    articles: list, config: dict | None = None
) -> ContentFilterResult:
    """Classify articles without silently conflating persistence and delivery.

    Matching is a case-insensitive substring check against the title and
    RSS summary. A disabled or empty config keeps everything.
    """
    mode = filter_mode(config)
    if mode == "disabled":
        return ContentFilterResult(list(articles), [], mode)

    kept: list = []
    blocked: list = []
    for article in articles:
        title = getattr(article, "title", "") or ""
        summary = getattr(article, "summary", "") or ""
        category = getattr(article, "category", "") or ""
        matched = blocked_keywords(title, summary, category, config)
        if matched:
            blocked.append(article)
        else:
            kept.append(article)
    return ContentFilterResult(kept, blocked, mode)


def filter_articles(articles: list, config: dict | None = None) -> tuple[list, list]:
    """Backward-compatible ``(kept, blocked)`` view of the filter result."""

    result = apply_content_filter(articles, config)
    return result.kept, result.blocked
