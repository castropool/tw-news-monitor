"""Deterministic content classification for sources without fixed categories.

Category remains independent from source identity. A generic source can route
articles into any existing section (politics, economy, military,
international, religion) without being locked to a single source-level
category.
"""

from .models import Article


VALID_CATEGORIES = frozenset({
    "politics",
    "economy",
    "military",
    "international",
    "religion",
})

_RELIGION_KEYWORDS = (
    "藏传佛教",
    "藏傳佛教",
    "佛教",
    "宗教自由",
    "宗教信仰",
    "寺院",
    "寺庙",
    "寺廟",
    "僧侣",
    "僧侶",
    "尼姑",
    "法会",
    "法會",
    "宗教仪式",
    "宗教儀式",
    "宗教教育",
    "宗教活动",
    "宗教活動",
    "寺院管理",
    "寺廟管理",
    "宗教政策",
    "活佛转世",
    "活佛轉世",
    "转世制度",
    "轉世制度",
    "宗教迫害",
    "宗教管制",
    "佛教会议",
    "佛教會議",
    "宗教领袖",
    "宗教領袖",
    "宗教团体",
    "宗教團體",
)

_MILITARY_KEYWORDS = (
    "解放军",
    "解放軍",
    "军队",
    "軍隊",
    "部队",
    "部隊",
    "军事",
    "軍事",
    "国防",
    "國防",
    "边境",
    "邊境",
    "中印",
    "军事部署",
    "軍事部署",
    "军事演习",
    "军事演练",
    "軍事演習",
    "軍演",
    "军事设施",
    "軍事設施",
    "军队调动",
    "部隊調動",
    "武器",
    "装备",
    "裝備",
    "战机",
    "戰機",
    "军舰",
    "軍艦",
    "导弹",
    "導彈",
    "台军",
    "臺軍",
)

_INTERNATIONAL_KEYWORDS = (
    "美国",
    "美國",
    "联合国",
    "聯合國",
    "欧洲议会",
    "歐洲議會",
    "欧盟",
    "歐盟",
    "印度",
    "外国政府",
    "外國政府",
    "国际组织",
    "國際組織",
    "国会",
    "國會",
    "议员",
    "議員",
    "法案",
)

_ECONOMY_KEYWORDS = (
    "经济",
    "經濟",
    "经贸",
    "經貿",
    "贸易",
    "貿易",
    "市场",
    "市場",
    "投资",
    "投資",
    "汇率",
    "匯率",
    "企业",
    "企業",
)

_POLITICS_KEYWORDS = (
    "政治",
    "政策",
    "政府",
    "治理",
    "法规",
    "法規",
    "流亡",
    "中共",
    "选举",
    "選舉",
    "人权",
    "人權",
    "会议",
    "會議",
    "示威",
    "抗议",
    "抗議",
)


def classify_content_category(
    title: str,
    summary: str = "",
    fallback: str = "politics",
) -> str:
    """Classify a title/summary into an existing category with high precision."""
    text = " ".join(part for part in (title or "", summary or "") if part).lower()
    if not text:
        return fallback

    for keywords, category in (
        (_RELIGION_KEYWORDS, "religion"),
        (_MILITARY_KEYWORDS, "military"),
        (_INTERNATIONAL_KEYWORDS, "international"),
        (_ECONOMY_KEYWORDS, "economy"),
        (_POLITICS_KEYWORDS, "politics"),
    ):
        if any(keyword.lower() in text for keyword in keywords):
            return category
    return fallback


def apply_content_classification(
    articles: list[Article],
    source: dict,
) -> list[Article]:
    """Classify articles when the source has no fixed category/default.

    Sources with ``category`` or ``default_category`` keep the existing
    source-level behavior. A generic source without either uses content.
    """
    if source.get("category") or source.get("default_category"):
        return articles
    for article in articles:
        article.category = classify_content_category(
            article.title,
            article.summary or "",
        )
    return articles
