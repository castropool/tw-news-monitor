from datetime import datetime

from .international import is_international_media
from .models import Article


CATEGORY_NAMES = {
    "military": "军武",
    "religion": "宗教",
    "politics": "政治",
    "economy": "经济",
    "international": "国际",
}

CATEGORY_ORDER = ["politics", "economy", "military", "international", "religion"]


def build_digest(
    articles: list[Article],
    fetched_at: datetime,
    international_coverage: dict[str, list[Article]] | None = None,
    international_config: dict | None = None,
    *,
    include_international_media: bool = True,
) -> str:
    """Build a digest text from newly fetched articles.

    Articles are grouped by category, sorted by (position, published_at, source_name).

    国际媒体层 Phase I（可选参数，不传则行为与旧版完全一致）：
    - ``international_config``：用于识别国际媒体来源；
    - ``international_coverage``：以 canonical.url 为键的事件成员表；
      canonical 国际媒体文章标题后追加“另有 N 家国际媒体报道同一事件”
      （仅当该事件 coverage 非空）。
    - ``include_international_media``：delivery 层可设为 False，让普通国际
      相关稿件只进入 Word；默认 True 保持旧的直接调用行为。
    """
    if not articles:
        total = 0
        # Count source failures from context will be added by caller
        lines = [
            f"【台湾新闻监测｜{fetched_at.strftime('%Y-%m-%d %H:%M')}】",
            "",
            "本轮未发现新增新闻。",
        ]
        return "\n".join(lines)

    # Group by category
    grouped: dict[str, list[Article]] = {}
    for cat in CATEGORY_ORDER:
        grouped[cat] = []
    for article in articles:
        if (
            not include_international_media
            and international_config
            and international_config.get("enabled", False)
            and is_international_media(article.source_name, international_config)
        ):
            continue
        cat = article.category
        if cat in grouped:
            grouped[cat].append(article)

    lines: list[str] = []
    lines.append(f"【台湾新闻监测｜{fetched_at.strftime('%Y-%m-%d %H:%M')}】")
    lines.append("")

    for cat in CATEGORY_ORDER:
        cat_articles = grouped.get(cat, [])
        if not cat_articles:
            continue
        cat_name = CATEGORY_NAMES.get(cat, cat)
        # Sort by position, then published_at (desc), then source_name
        cat_articles.sort(key=lambda a: (
            a.position,
            -(a.published_at.timestamp() if a.published_at else 0),
            a.source_name,
        ))
        lines.append(f"{cat_name}｜新增{len(cat_articles)}条")
        for idx, article in enumerate(cat_articles, 1):
            time_str = ""
            if article.published_at:
                time_str = article.published_at.strftime("%H:%M")
            lines.append(f"{idx}. {article.title}")
            if is_international_media(article.source_name, international_config):
                members = (international_coverage or {}).get(article.url) or []
                if len(members) > 1:
                    lines.append(f"   另有 {len(members) - 1} 家国际媒体报道同一事件")
            lines.append(f"   {article.source_name}｜{time_str}")
            lines.append(f"   {article.url}")
        lines.append("")

    return "\n".join(lines)


def count_new_by_category(articles: list[Article]) -> dict[str, int]:
    result: dict[str, int] = {}
    for a in articles:
        result[a.category] = result.get(a.category, 0) + 1
    return result
