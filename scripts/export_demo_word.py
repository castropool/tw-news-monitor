# -*- coding: utf-8 -*-
"""一次性演示脚本：导出含国际媒体栏目的 Word 简报（不发送飞书）。

从 news.db 取最新台湾媒体文章 + 最新 Reuters/FT 文章，
组装后调用 build_word_digest（启用 international_config），
使简报中出现「国际媒体」栏目，用于向用户展示国际媒体抓取能力。
只读数据库，不改任何生产逻辑。
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from app.database import Database
from app.international import is_international_media, load_international_config
from app.word_digest import build_word_digest
from app.time_utils import TAIPEI
from app.settings import get_settings, load_environment

DEMO_INTL_MAX = 14        # 国际媒体最多收录条数（Reuters+FT 合计）
DOMESTIC_MAX = 34         # 台湾媒体条数（保持简报主体以台湾为主）


def main() -> None:
    load_environment(PROJECT_ROOT)
    settings = get_settings(PROJECT_ROOT, load_env=False)
    db_path = settings.news_db_path
    db = Database(db_path)
    db.connect()

    international_config = load_international_config(
        PROJECT_ROOT / "config" / "international_media.yaml"
    )
    # 关闭相关性过滤不影响 build_word_digest；这里只是读取配置结构。
    # （build_word_digest 只看 enabled + tier1 列表，不做相关性过滤。）

    all_articles = db.get_articles_since(
        datetime(2000, 1, 1), eligible_only=True
    )
    # 按入库时间倒序（fetched_at 可能为 naive，统一视为台北时间比较）
    def _ts(a):
        dt = a.fetched_at or datetime.min
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=TAIPEI)
        return dt
    ordered = sorted(all_articles, key=_ts, reverse=True)

    intl_articles = [
        a for a in ordered
        if is_international_media(a.source_name, international_config)
    ][:DEMO_INTL_MAX]

    intl_urls = {a.url for a in intl_articles}
    domestic_articles = [
        a for a in ordered
        if a.url not in intl_urls
    ][:DOMESTIC_MAX]

    articles = domestic_articles + intl_articles
    print(f"台湾媒体：{len(domestic_articles)} 条 | 国际媒体：{len(intl_articles)} 条")

    now = datetime.now(TAIPEI)
    output_dir = PROJECT_ROOT / "data" / "reports"
    output_path = build_word_digest(
        articles,
        output_dir,
        generated_at=now,
        international_config=international_config,
    )
    print(f"演示简报已生成: {output_path}")
    db.close()


if __name__ == "__main__":
    main()
