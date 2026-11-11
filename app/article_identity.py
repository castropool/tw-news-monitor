import re
from urllib.parse import urlsplit
from .collectors.base import BaseCollector

UDN_STORY_PATTERN = re.compile(r"^/news/story/[^/]+/(\d+)/?$")
CNA_ARTICLE_PATTERN = re.compile(r"^/news/([a-z]+)/(\d+)\.aspx$")
LTN_PATH_PATTERN = re.compile(r"^/news/[^/]+/[^/]+/\d+$")
NEWTALK_PATTERN = re.compile(r"^/news/view/\d{4}-\d{2}-\d{2}/(\d+)$")
STORM_PATTERN = re.compile(r"^/article/(\d+)")
EY_PATTERN = re.compile(r"^/page/[0-9a-f]+//([0-9a-f-]+)", re.IGNORECASE)

def article_identity_key(url: str) -> str:
    normalized = BaseCollector.normalize_url(url)
    parts = urlsplit(normalized)
    host = (parts.hostname or "").lower()
    if host.startswith("www."):
        host = host[4:]
    if host == "udn.com":
        match = UDN_STORY_PATTERN.fullmatch(parts.path)
        if match:
            return "udn:" + match.group(1)
    if host == "cna.com.tw":
        match = CNA_ARTICLE_PATTERN.fullmatch(parts.path)
        if match:
            return "cna:" + match.group(1) + ":" + match.group(2)
    if host == "news.ltn.com.tw":
        match = LTN_PATH_PATTERN.fullmatch(parts.path)
        if match:
            return "ltn:" + parts.path.lower().strip("/")
    if host in ("newtalk.tw", "www.newtalk.tw"):
        match = NEWTALK_PATTERN.fullmatch(parts.path)
        if match:
            return "newtalk:" + match.group(1)
    if host == "storm.mg":
        match = STORM_PATTERN.fullmatch(parts.path)
        if match:
            return "storm:" + match.group(1)
    if host == "ey.gov.tw":
        match = EY_PATTERN.fullmatch(parts.path)
        if match:
            return "ey:" + match.group(1)
    return "url:" + normalized

def _precision_rank(precision: str | None) -> int:
    """Rank metadata time precision: exact > date_only > unknown."""
    value = str(precision or "unknown").strip().lower()
    if value == "exact":
        return 3
    if value == "date_only":
        return 2
    return 1


def prefer_richer_article(a, b):
    """Return the article with richer metadata; keep ``a`` on ties.

    Used when duplicate candidates (same URL or same identity) must not be
    resolved by simple first-wins when a later banner/list item has better
    time metadata.
    """
    pa = _precision_rank(getattr(a, "published_at_precision", "exact"))
    pb = _precision_rank(getattr(b, "published_at_precision", "exact"))
    if pa != pb:
        return a if pa > pb else b

    a_pub = getattr(a, "published_at", None) is not None
    b_pub = getattr(b, "published_at", None) is not None
    if a_pub != b_pub:
        return a if a_pub else b

    a_summary = str(getattr(a, "summary", "") or "").strip()
    b_summary = str(getattr(b, "summary", "") or "").strip()
    if bool(a_summary) != bool(b_summary):
        return a if a_summary else b

    a_title = str(getattr(a, "title", "") or "").strip()
    b_title = str(getattr(b, "title", "") or "").strip()
    if bool(a_title) != bool(b_title):
        return a if a_title else b

    return a


def _deduplicate_keeping_richer(articles, key_func):
    seen = {}
    unique = []
    dups = []
    for article in articles:
        if not article.url:
            unique.append(article)
            continue
        key = key_func(article)
        if key in seen:
            existing = seen[key]
            winner = prefer_richer_article(existing, article)
            if winner is not existing:
                dups.append(existing)
                seen[key] = winner
                for i, item in enumerate(unique):
                    if item is existing:
                        unique[i] = winner
                        break
            else:
                dups.append(article)
        else:
            seen[key] = article
            unique.append(article)
    return unique, dups


def deduplicate_articles_by_identity(articles):
    """Deduplicate by identity while retaining the richest metadata copy."""
    return _deduplicate_keeping_richer(
        articles,
        lambda article: article_identity_key(article.url),
    )
