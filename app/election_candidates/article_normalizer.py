"""Deterministic article normalization without modifying source rows."""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

from .candidate_models import MatchInfo, NormalizedArticle


TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "from",
    "ref",
    "source",
}

TITLE_PREFIXES = ["快訊", "即時", "獨家", "更新", "影", "Live", "LIVE"]


def normalize_url(raw_url: str) -> str:
    if not raw_url:
        return ""
    raw = raw_url.strip()
    if not raw:
        return ""
    try:
        parsed = urlparse(raw)
        scheme = (parsed.scheme or "https").lower()
        host = (parsed.hostname or "").lower()
        path = (parsed.path.rstrip("/") if parsed.path else "").lower()
        if not path:
            path = "/"
        query_pairs = [
            (k, v)
            for k, v in parse_qsl(parsed.query, keep_blank_values=True)
            if k.lower() not in TRACKING_PARAMS
        ]
        query = urlencode(sorted(query_pairs))
        return urlunparse((scheme, host, path, "", query, ""))
    except ValueError:
        return raw


def normalize_domain(raw_url: str) -> str:
    if not raw_url:
        return ""
    try:
        host = (urlparse(raw_url).hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        return host
    except ValueError:
        return ""


def normalize_source_name(raw: str) -> str:
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw).strip()
    text = re.sub(r"\s+", " ", text)
    return text


def normalize_title(raw: str) -> str:
    if not raw:
        return ""
    text = unicodedata.normalize("NFKC", raw).strip()
    text = re.sub(r"\s+", " ", text)
    for prefix in TITLE_PREFIXES:
        if text.startswith(prefix) and len(text) > len(prefix):
            rest = text[len(prefix):].lstrip("：:｜| 　")
            if rest:
                text = rest
                break
    return text


def parse_date(value: Any) -> str:
    if value is None:
        return ""
    text = str(value).strip()
    if not text:
        return ""
    for fmt in (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%d %H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d",
        "%Y/%m/%d %H:%M:%S",
        "%Y/%m/%d",
        "%Y年%m月%d日",
    ):
        try:
            return datetime.strptime(text, fmt).isoformat()
        except ValueError:
            continue
    m = re.match(r"^(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})日?", text)
    if m:
        y, mo, d = (int(v) for v in m.groups())
        return datetime(y, mo, d).isoformat()
    return ""


def normalize_article(row: dict[str, Any], config) -> NormalizedArticle:
    def col(name: str) -> Any:
        return row.get(name, "")

    raw_title = str(col(config.get("news_reader.title_column", "title")) or "")
    raw_url = str(col(config.get("news_reader.url_column", "url")) or "")
    source_name = str(col(config.get("news_reader.source_name_column", "source_name")) or "")
    return NormalizedArticle(
        news_article_id=str(col(config.get("news_reader.id_column", "id"))),
        raw_title=raw_title,
        normalized_title=normalize_title(raw_title),
        raw_url=raw_url,
        normalized_url=normalize_url(raw_url),
        source_name=source_name,
        normalized_source_name=normalize_source_name(source_name),
        normalized_domain=normalize_domain(raw_url),
        category=str(col(config.get("news_reader.category_column", "category")) or ""),
        summary=str(col(config.get("news_reader.summary_column", "summary")) or ""),
        published_at=parse_date(col(config.get("news_reader.published_at_column", "published_at"))),
        collected_at=parse_date(col(config.get("news_reader.fetched_at_column", "fetched_at"))),
        match=MatchInfo(),
    )
