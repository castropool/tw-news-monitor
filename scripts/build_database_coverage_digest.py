"""Generate a real-news Word digest with database source coverage.

The normal scheduler delivers fresh/catch-up articles.  This companion export
selects one latest usable article per source already present in news.db, then
adds all database international articles that pass the existing relevance and
event-dedup rules.  It uses the same summary, translation, filtering, and Word
builder functions as normal delivery; it never collects or sends notifications.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import datetime
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.database import Database
from app.international import (
    filter_international,
    is_international_media,
    load_international_config,
)
from app.international_events import cluster_international_articles
from app.main import (
    _build_international_translator,
    _precompute_international_translations,
    _translation_articles_for_delivery,
    enrich_summaries_safe,
    prepare_international_delivery,
)
from app.time_utils import TAIPEI
from app.word_digest import build_word_digest


DB_PATH = ROOT / "data" / "news.db"
INTERNATIONAL_CONFIG_PATH = ROOT / "config" / "international_media.yaml"
OUTPUT_DIR = ROOT / "data" / "reports"


def article_timestamp(article) -> float:
    value = getattr(article, "published_at", None) or getattr(article, "fetched_at", None)
    return value.timestamp() if value else 0.0


def select_source_representatives(articles: list) -> list:
    """Select one real, preferably summarized article for every DB source."""

    by_source: dict[str, list] = defaultdict(list)
    for article in articles:
        by_source[str(article.source_id)].append(article)
    selected = []
    for source_id, values in sorted(by_source.items()):
        values = sorted(values, key=article_timestamp, reverse=True)
        with_summary = [
            article
            for article in values
            if isinstance(article.summary, str) and article.summary.strip()
        ]
        selected.append((with_summary or values)[0])
    return selected


def select_relevant_international(articles: list, config: dict) -> tuple[list, dict]:
    """Select canonical relevant international stories from the full DB."""

    international = [
        article
        for article in articles
        if is_international_media(article.source_name, config)
    ]
    relevant, _excluded = filter_international(international, config)
    clusters, coverage = cluster_international_articles(relevant, config)
    return [cluster.canonical for cluster in clusters], coverage


def build_database_coverage_digest(output_path: Path | None = None) -> Path:
    db = Database(DB_PATH)
    db.connect()
    try:
        all_articles = db.get_articles_since(datetime(2000, 1, 1))
        config = load_international_config(INTERNATIONAL_CONFIG_PATH)
        representatives = select_source_representatives(all_articles)
        international_articles, _historical_coverage = select_relevant_international(
            all_articles, config
        )
        # Local/official representatives cover every source with DB rows;
        # international representatives are replaced by relevance-filtered
        # canonical stories so unrelated Reuters/FT items do not enter.
        local_representatives = [
            article
            for article in representatives
            if not is_international_media(article.source_name, config)
        ]
        selected = local_representatives + international_articles
        digest_articles, coverage = prepare_international_delivery(selected, config)
        enrich_summaries_safe(digest_articles, db)

        translator = _build_international_translator()
        translation_articles = _translation_articles_for_delivery(
            digest_articles, coverage
        )
        translations = _precompute_international_translations(
            translation_articles, config, translator=translator
        )
        output_path = output_path or (
            OUTPUT_DIR
            / f"台湾新闻监测_数据库覆盖_{datetime.now(TAIPEI):%Y-%m-%d_%H%M}.docx"
        )
        result = build_word_digest(
            digest_articles,
            output_path.parent,
            generated_at=datetime.now(TAIPEI),
            international_config=config,
            international_coverage=coverage,
            international_translations=translations,
        )
        print(f"数据库新闻总量：{len(all_articles)}")
        print(f"实际来源数量：{len({article.source_id for article in all_articles})}")
        print(f"覆盖来源代表新闻：{len(local_representatives)}")
        print(f"国际相关 canonical 新闻：{len(international_articles)}")
        print(f"Word 简报条目：{len(digest_articles)}")
        print(f"Word 简报已生成：{result}")
        print("本次只读取数据库并生成 Word，未采集、未写库、未发送通知。")
        return result
    finally:
        db.close()


if __name__ == "__main__":
    build_database_coverage_digest()
