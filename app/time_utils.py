from datetime import datetime
from zoneinfo import ZoneInfo

TAIPEI = ZoneInfo("Asia/Taipei")


def normalize_published_at(
    value: datetime | None,
    *,
    assumed_timezone: ZoneInfo | None = None,
) -> datetime | None:
    """Normalize published_at to timezone-aware Asia/Taipei.

    - None returns None
    - Aware converts via astimezone(Asia/Taipei)
    - Naive with assumed_timezone applies replace(tzinfo)
    - Naive without assumed_timezone returns None
    - Does not modify the input object
    """
    if value is None:
        return None
    if value.tzinfo is not None:
        return value.astimezone(TAIPEI)
    if assumed_timezone is not None:
        return value.replace(tzinfo=assumed_timezone)
    return None


def format_article_publish_time(article) -> str | None:
    """Return the Word-visible publish time string for one article.

    ``date_only`` articles deliberately do not display a fabricated 00:00.
    Unknown/missing time returns None so callers can omit the line entirely.
    """
    published_at = getattr(article, "published_at", None)
    precision = getattr(article, "published_at_precision", "exact") or "exact"
    if published_at is None or precision == "unknown":
        return None
    normalized = published_at
    if normalized.tzinfo is None:
        normalized = normalized.replace(tzinfo=TAIPEI)
    else:
        normalized = normalized.astimezone(TAIPEI)
    if precision == "date_only":
        return f"发布日期：{normalized.date().isoformat()}"
    return f"发布时间：{normalized.strftime('%Y-%m-%d %H:%M')}"
