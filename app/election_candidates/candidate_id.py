"""Stable candidate event id generation.

The id depends only on the anchor article identity (source db marker, article
primary key and normalized URL) and a coarse date bucket, never on run time,
title edits, article order or summary text.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .candidate_models import NormalizedArticle


SOURCE_DB_MARKER = "news"


def anchor_key(article: NormalizedArticle) -> str:
    date_bucket = (article.published_at or "")[:10]
    return "|".join(
        [
            SOURCE_DB_MARKER,
            str(article.news_article_id),
            article.normalized_url or article.raw_url,
            date_bucket,
        ]
    )


def candidate_id_for_anchor(article: NormalizedArticle, prefix: str = "cand_tnn", hash_length: int = 10) -> str:
    raw = anchor_key(article)
    digest = hashlib.sha256(raw.encode("utf-8")).hexdigest()[:hash_length]
    return f"{prefix}_{digest}"


def choose_anchor(articles: list[NormalizedArticle]) -> NormalizedArticle | None:
    if not articles:
        return None
    return min(articles, key=lambda a: (a.published_at or "9999", str(a.news_article_id)))


def cluster_fingerprint(articles: list[NormalizedArticle], prefix: str = "cand_tnn", hash_length: int = 10) -> str:
    anchor = choose_anchor(articles)
    if anchor is None:
        return ""
    return candidate_id_for_anchor(anchor, prefix=prefix, hash_length=hash_length)


def write_candidate_id_strategy(output_root: str | Path, config) -> Path:
    root = Path(output_root)
    root.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": "1.0",
        "format": "cand_tnn_<sha256(anchor_key)[:10]>",
        "anchor_key_fields": [
            "source_db_marker=news",
            "news_article_id",
            "normalized_url",
            "date_bucket(YYYY-MM-DD)",
        ],
        "stability_rules": [
            "anchor is the earliest article by (published_at, news_article_id)",
            "anchor is kept unchanged once a candidate exists and an overlapping article is detected",
            "candidate id never contains run_id, current time, title or summary",
            "title changes do not change the id",
        ],
        "collision_handling": [
            "hash collision is mitigated by including the article primary key and normalized URL",
            "the repository enforces candidate_id PRIMARY KEY; a collision raises an error instead of silently merging",
            "run_idempotency.json records the exact anchor keys used",
        ],
        "notes": [
            "If the news primary key is unstable across databases, the source marker + normalized URL "
            "still make the key stable within this project.",
        ],
    }
    path = root / "candidate_id_strategy.json"
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
