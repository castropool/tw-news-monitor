from datetime import datetime

from .international import is_international_media
from .models import Article
from .special_topics import is_dedicated_topic_article
from .special_topics.render import special_topic_line


CATEGORY_NAMES = {
    "military": "军武",
    "religion": "宗教",
    "politics": "政治",
    "economy": "经济",
    "international": "国际",
    "poll": "民调",
    "betting": "地下赌盘",
}

CATEGORY_ORDER = ["politics", "economy", "poll", "betting", "military", "international", "religion"]

KOL_PLATFORM_LABELS = {
    "facebook": "Facebook", "threads": "Threads", "instagram": "Instagram",
    "youtube": "YouTube", "x": "X", "podcast": "Podcast", "blog": "Blog",
}


def build_digest(
    articles: list[Article],
    fetched_at: datetime,
    international_coverage: dict[str, list[Article]] | None = None,
    international_config: dict | None = None,
    *,
    include_international_media: bool = True,
    kol_items: list[dict] | None = None,
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
    - ``kol_items``：近期 KOL 社群动态（已经过时间窗与相关性筛选），在
      分类小节之后追加“KOL动态”小节；不传则行为不变。
    """
    if not articles:
        total = 0
        # Count source failures from context will be added by caller
        lines = [
            f"【台湾新闻监测｜{fetched_at.strftime('%Y-%m-%d %H:%M')}】",
            "",
            "本轮未发现新增新闻。",
        ]
        if kol_items:
            lines.append("")
            lines.extend(_kol_lines(kol_items))
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
            and not is_dedicated_topic_article(article)
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
            topic_line = special_topic_line(article)
            if topic_line:
                lines.append(f"   {topic_line}")
            if is_international_media(article.source_name, international_config):
                members = (international_coverage or {}).get(article.url) or []
                if len(members) > 1:
                    lines.append(f"   另有 {len(members) - 1} 家国际媒体报道同一事件")
            lines.append(f"   {article.source_name}｜{time_str}")
            lines.append(f"   {article.url}")
        lines.append("")

    if kol_items:
        lines.extend(_kol_lines(kol_items))
        lines.append("")

    return "\n".join(lines)


def _kol_lines(kol_items: list[dict]) -> list[str]:
    """Compact KOL block: who, platform, headline (title/first line), link."""
    lines = [f"KOL动态｜{len(kol_items)}条"]
    for idx, item in enumerate(kol_items, 1):
        name = str(item.get("person_display_name") or item.get("person_canonical_name") or item.get("person_id") or "")
        platform = KOL_PLATFORM_LABELS.get(str(item.get("platform")), str(item.get("platform") or ""))
        headline = str(item.get("title") or item.get("normalized_text") or item.get("text") or "").strip()
        headline = headline.splitlines()[0] if headline else ""
        if len(headline) > 80:
            headline = headline[:79] + "…"
        lines.append(f"{idx}. 【{name}｜{platform}】{headline}")
        if item.get("canonical_url"):
            lines.append(f"   {item['canonical_url']}")
    return lines


def count_new_by_category(articles: list[Article]) -> dict[str, int]:
    result: dict[str, int] = {}
    for a in articles:
        result[a.category] = result.get(a.category, 0) + 1
    return result
