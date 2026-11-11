"""Deterministic, explainable social-post relevance gate for Word delivery."""

from __future__ import annotations

from typing import Any

from .models import WORD_ALLOWED_CATEGORIES
from .normalize import normalize_social_text


CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    "candidate_status": ("參選", "参选", "提名", "退選", "退选", "競選", "竞选", "登記", "登记"),
    "policy": ("政策", "法案", "改革", "預算", "预算", "補助", "补助", "稅", "税", "健保", "住宅"),
    "national_security": ("國家安全", "国家安全", "國安", "国安", "反滲透", "反渗透", "資安", "资安"),
    "defense": ("國防", "国防", "軍購", "军购", "軍演", "军演", "共軍", "共军", "兵役", "飛彈", "飞弹"),
    "cross_strait": ("兩岸", "两岸", "中國", "中国", "台海", "交流", "協商", "协商"),
    "foreign_affairs": ("外交", "邦交", "訪美", "访美", "國際", "国际", "盟友"),
    "party_strategy": ("民進黨", "民进党", "國民黨", "国民党", "民眾黨", "民众党", "黨團", "党团", "配票", "初選", "初选"),
    "legislature": ("立法院", "立法委員", "立委", "法案", "質詢", "质询", "委員會", "委员会"),
    "governance": ("行政院", "市府", "縣府", "县府", "治理", "施政", "政見", "政见"),
    "controversy_response": ("回應", "回应", "爭議", "争议", "駁斥", "驳斥", "道歉", "澄清"),
    "personnel": ("人事", "任命", "接任", "請辭", "请辞", "卸任", "內閣", "内阁"),
    "local_government": ("台南", "臺南", "新北", "台北", "臺北", "桃園", "桃园", "台中", "高雄", "縣市", "县市"),
    "campaign_activity": ("造勢", "造势", "掃街", "扫街", "拜票", "競選活動", "竞选活动"),
    "routine_activity": ("出席", "視察", "视察", "參訪", "参访", "致詞", "致词", "合照", "座談", "座谈"),
    "personal": ("生日", "家人", "私人", "生活", "休閒", "休闲", "旅遊", "旅游"),
    "ceremonial": ("祝賀", "祝贺", "恭喜", "感謝", "感谢", "問候", "问候", "紀念", "纪念"),
}
SCORES = {
    "candidate_status": 40, "policy": 30, "national_security": 30, "defense": 30,
    "cross_strait": 30, "personnel": 25, "controversy_response": 20,
    "party_strategy": 20, "local_government": 15, "election": 25,
    "routine_activity": -30, "personal": -50, "ceremonial": -30,
}


def classify_social_relevance(text: str | None, title: str | None = None) -> dict[str, Any]:
    haystack = normalize_social_text(f"{title or ''}\n{text or ''}")
    matched: list[str] = []
    categories: list[str] = []
    for category, keywords in CATEGORY_KEYWORDS.items():
        if any(keyword in haystack for keyword in keywords):
            categories.append(category)
    if not categories:
        categories = ["other"]
    # choose the category with highest score, stable by declaration order
    primary = max(categories, key=lambda c: (SCORES.get(c, 0), -list(CATEGORY_KEYWORDS).index(c) if c in CATEGORY_KEYWORDS else -999))
    score = SCORES.get(primary, 0)
    for category in categories:
        if category != primary:
            score += max(0, SCORES.get(category, 0) // 4)
    allowed = primary in WORD_ALLOWED_CATEGORIES
    event_candidate = allowed and primary not in {"routine_activity", "personal", "ceremonial"}
    return {
        "category": primary,
        "categories": categories,
        "score": score,
        "word_allowed": allowed,
        "event_candidate": event_candidate,
        "matched_keywords": [kw for kw in sum(CATEGORY_KEYWORDS.values(), ()) if kw in haystack],
    }


def enrich_posts_with_relevance(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for post in posts:
        item = dict(post)
        result = classify_social_relevance(item.get("text"), item.get("title"))
        item["relevance_category"] = result["category"]
        item["relevance_score"] = result["score"]
        item["word_allowed"] = result["word_allowed"]
        item["event_candidate"] = result["event_candidate"]
        output.append(item)
    return output


def select_word_posts(posts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [post for post in enrich_posts_with_relevance(posts) if post["word_allowed"]]
