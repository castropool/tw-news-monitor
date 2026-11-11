"""国际媒体免费监测层 Phase I — 隔离真实采集验收脚本（仅验收用，未接入 Scheduler）。

本脚本是 Phase I 唯一允许访问真实网络的步骤：
  - Reuters 官方 Google News news-sitemap（仅元数据：标题/URL/时间，无正文）
  - FT Alphaville 官方 RSS（公开 teaser，无全文）
两者均为已确认的官方公开元数据入口；本脚本绝不访问文章页、绝不抓正文。

安全边界（与任务书一致）：
  - 独立临时 DB：data/validation_international.db（绝不触碰 data/news.db）；
  - 独立报告目录：data/validation_reports/；
  - notifier 强制 console（仅实例化，绝不调用 send）；不调用 enrich_summaries /
    LLM / Feishu；不改 Scheduler、不改生产 config/sources.yaml、不改 .env；
  - 流程复用 app.main.collect_all（真实去重/save）、app.freshness、
    app.importance、app.international、app.digest、app.word_digest 现有函数，
    不重新实现任何去重/freshness/importance 逻辑。

Word 产出分三类（全部经同一 build_word_digest 渲染路径）：
  1) 真实交付 Word：仅当本轮有 fresh 且 relevant 的真实文章时生成
     （与生产 main 的交付语义一致；本轮若无，如实记录 word_generated=false）；
  2) Word 结构验收探针（--probe-word，默认开）：固定 fixture（官方信源 + 五个
     国内分类小节 + 路透社/金融时报同事件对），固定 generated_at，两遍运行
     字节级一致，用于验收栏目结构/中文名/URL/coverage 去重/不重复刷屏；
  3) 真实样本 Word（--real-sample，默认开）：本轮真实采集的全部国际文章
     直接进“国际媒体”栏目（显示层，绕过相关度过滤，报告内如实标注），
     用于目视确认真实英文标题/真实 URL 渲染正常。

用法：
  python scripts/run_international_validation.py [--run N] [--reset-db]
      [--no-probe-word] [--no-real-sample]

  默认保留验证 DB：第一遍 --run 1（全新 DB）后，第二遍 --run 2 复用同一 DB，
  验证幂等（inserted=0）；如需从头开始可显式 --reset-db。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.collectors import FTAlphavilleCollector, ReutersCollector  # noqa: E402
from app.content_filter import load_content_filter  # noqa: E402
from app.database import Database  # noqa: E402
from app.digest import build_digest  # noqa: E402
from app.freshness import filter_fresh_articles  # noqa: E402
from app.importance import (  # noqa: E402
    classify_articles,
    finalize_importance,
    importance_summary,
    load_rules,
    validate_rules_config,
)
from app.international import (  # noqa: E402
    classify_international,
    dedupe_international_for_digest,
    filter_international,
    is_international_media,
    load_international_config,
)
from app.main import COLLECTOR_MAP, collect_all, load_sources, validate_sources_config  # noqa: E402
from app.models import Article  # noqa: E402
from app.notifier import ConsoleNotifier  # noqa: E402
from app.time_utils import TAIPEI  # noqa: E402
from app.word_digest import build_word_digest  # noqa: E402

VALIDATION_CONFIG = ROOT / "config" / "sources.validation.yaml"
PRODUCTION_CONFIG = ROOT / "config" / "sources.yaml"
VALIDATION_DB = ROOT / "data" / "validation_international.db"
PRODUCTION_DB = ROOT / "data" / "news.db"
REPORT_DIR = ROOT / "data" / "validation_reports"

# 本脚本允许访问的唯二网络端点（硬编码，与验证配置一致，双保险）
ALLOWED_ENDPOINTS = {
    "https://www.reuters.com/arc/outboundfeeds/news-sitemap-index/?outputType=xml",
    "https://ftalphaville.ft.com/feed/",
}

# Word 探针固定生成时间（两遍运行字节级一致，便于幂等对比）
PROBE_GENERATED_AT = datetime(2026, 8, 13, 20, 0, tzinfo=TAIPEI)
# 探针同事件对（与 config/international_media.yaml 注释中的定稿用例一致）
PROBE_BASE = datetime(2026, 8, 13, 12, 0, tzinfo=timezone.utc)


def sha256_file(path: Path) -> str:
    if not path.exists():
        return "missing"
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprint(path: Path) -> dict:
    if not path.exists():
        return {"exists": False}
    st = path.stat()
    return {
        "exists": True,
        "size": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime).astimezone(TAIPEI).isoformat(),
        "sha256": sha256_file(path),
    }


def _iso(dt) -> str | None:
    return dt.isoformat() if dt else None


def _patch_collectors(stats: dict):
    """临时包装 collect() 以逐源记录 fetched/parsed/errors（只读统计，不改变逻辑）。

    仅在本进程内存中生效，collect_all 结束后立即恢复原方法；不改任何文件。
    """
    patches = []

    def make_wrapper(sid, orig):
        def wrapper(self):
            try:
                arts = orig(self)
                stats[sid] = {
                    "fetched": len(arts),
                    "parsed": len([a for a in arts if a.title and a.url]),
                    "error": None,
                }
                return arts
            except Exception as exc:  # noqa: BLE001 - 逐源容错与 collect_all 一致
                stats[sid] = {
                    "fetched": 0,
                    "parsed": 0,
                    "error": f"{type(exc).__name__}: {exc}",
                }
                raise
        return wrapper

    for sid, cls in (
        ("reuters_international", ReutersCollector),
        ("ft_alphaville", FTAlphavilleCollector),
    ):
        orig = cls.collect
        cls.collect = make_wrapper(sid, orig)
        patches.append((cls, orig))
    return patches


def _restore_collectors(patches) -> None:
    for cls, orig in patches:
        cls.collect = orig


def _docx_texts(path: Path) -> list[str]:
    """提取 docx 全部段落文本（含超链接 run 的文本）。"""
    from docx import Document

    doc = Document(str(path))
    return ["".join(p._p.itertext()) for p in doc.paragraphs]


def _probe_article(
    title: str,
    source_name: str,
    source_id: str,
    url: str,
    category: str,
    published_at: datetime,
    position: int,
) -> Article:
    return Article(
        source_id=source_id,
        source_name=source_name,
        category=category,
        title=title,
        url=url,
        published_at=published_at,
        fetched_at=datetime.now(),
        position=position,
        summary=None,
        section=None,
        language="en",
        access_level="public",
    )


def _build_probe_word(cfg: dict, run_number: int) -> dict:
    """Word 结构验收探针（fixture，确定性；两遍运行字节级一致）。

    覆盖任务书 Word 验收项：官方信源/新闻媒体一级栏目、军武/宗教等分类小节、
    国际媒体二级栏目、英文标题、中文来源名、URL、同事件 coverage 只列 canonical。
    """
    articles = [
        # 官方信源 -> 一、官方信源
        _probe_article(
            "总统府：召开因应国际情势安全会议", "总统府", "president_press",
            "https://www.president.gov.tw/news/probe-official-001",
            "politics", PROBE_BASE, 1,
        ),
        # 国内五个分类小节 -> （一）~（五）
        _probe_article(
            "行政院通过产业升级条例修正草案", "中央社", "cna_probe_politics",
            "https://www.cna.com.tw/news/probe-politics-001",
            "politics", PROBE_BASE, 2,
        ),
        _probe_article(
            "台股收红 半导体类股领涨", "中央社", "cna_probe_economy",
            "https://www.cna.com.tw/news/probe-economy-001",
            "economy", PROBE_BASE, 3,
        ),
        _probe_article(
            "国军年度汉光演习验证联合作战能力", "自由时报·军武", "ltn_probe_military",
            "https://news.ltn.com.tw/news/probe-military-001",
            "military", PROBE_BASE, 4,
        ),
        _probe_article(
            "教宗呼吁全球宗教领袖对话促进和平", "中央社", "cna_probe_religion",
            "https://www.cna.com.tw/news/probe-religion-001",
            "religion", PROBE_BASE, 5,
        ),
        _probe_article(
            "美日领袖会谈聚焦印太安全合作", "中央社", "cna_probe_intl",
            "https://www.cna.com.tw/news/probe-intl-001",
            "international", PROBE_BASE, 6,
        ),
        # 同事件对（Reuters canonical + FT 成员）-> 国际媒体栏目只列 canonical
        _probe_article(
            "China launches drills near Taiwan", "Reuters", "reuters_international",
            "https://www.reuters.com/world/asia-pacific/probe-china-drills-taiwan",
            "international", PROBE_BASE, 7,
        ),
        _probe_article(
            "China begins military exercises around Taiwan", "Financial Times",
            "ft_alphaville",
            "https://ftalphaville.ft.com/probe-china-exercises-taiwan",
            "international", PROBE_BASE.replace(hour=13), 8,
        ),
    ]
    canonical, coverage = dedupe_international_for_digest(articles, cfg)
    path = build_word_digest(
        canonical, REPORT_DIR, generated_at=PROBE_GENERATED_AT,
        catch_up_urls=set(), importance_results=[],
        international_config=cfg, international_coverage=coverage,
    )
    stable = REPORT_DIR / f"validation_word_probe_run{run_number}.docx"
    latest = REPORT_DIR / "validation_word_probe.docx"
    path.rename(stable)
    import shutil
    shutil.copy2(stable, latest)
    texts = _docx_texts(stable)
    joined = "\n".join(texts)
    ft_member_title = "China begins military exercises around Taiwan"
    checks = {
        "official_section_heading": any("官方信源" in t for t in texts),
        "news_media_section_heading": any("新闻媒体" in t for t in texts),
        "intl_section_heading": any("国际媒体" in t for t in texts),
        "military_subsection": any("军武" in t for t in texts),
        "religion_subsection": any("宗教" in t for t in texts),
        "chinese_display_name_reuters": "路透社" in joined,
        "chinese_display_name_ft": "金融时报" in joined,
        "english_title_preserved": "China launches drills near Taiwan" in joined,
        "url_rendered": "https://www.reuters.com/world/asia-pacific/probe-china-drills-taiwan" in joined,
        "coverage_note_present": "另据金融时报报道同一事件" in joined,
        "canonical_only_no_member_title": ft_member_title not in joined,
        "no_duplicate_article_lines": len(joined.split(ft_member_title)) == 1,
    }
    return {
        "path": str(stable),
        "latest": str(latest),
        "sha256": sha256_file(stable),
        "text_hash": hashlib.sha256("\n".join(texts).encode("utf-8")).hexdigest(),
        "article_count_input": len(articles),
        "canonical_count": len(canonical),
        "coverage_groups": {
            c: [m.title for m in members] for c, members in coverage.items()
        },
        "checks": checks,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=int, default=1, help="run number (default 1)")
    parser.add_argument("--reset-db", action="store_true",
                        help="delete and recreate the validation DB before this run")
    parser.add_argument("--no-probe-word", action="store_true",
                        help="skip deterministic Word structure probe")
    parser.add_argument("--no-real-sample", action="store_true",
                        help="skip real-article sample Word")
    args = parser.parse_args()
    run_number = args.run

    started = datetime.now(TAIPEI)

    # ── 安全检查 ──────────────────────────────────────────────────────
    prod_before = fingerprint(PRODUCTION_DB)
    assert VALIDATION_DB.resolve() != PRODUCTION_DB.resolve(), (
        "validation DB must not be production news.db"
    )
    assert VALIDATION_CONFIG.resolve() != PRODUCTION_CONFIG.resolve(), (
        "validation config must not be production sources.yaml"
    )
    print("=" * 72)
    print("国际媒体免费监测层 Phase I — 隔离真实采集验收")
    print(f"  验证配置 : {VALIDATION_CONFIG}")
    print(f"  验证 DB  : {VALIDATION_DB} (绝不触碰 {PRODUCTION_DB})")
    print(f"  报告目录 : {REPORT_DIR}")
    print(f"  网络端点 : 仅 Reuters news-sitemap + FT Alphaville RSS")
    print(f"  notifier : console（本脚本不会调用 send）| LLM 摘要 : 不调用")
    print("=" * 72)

    # ── 配置加载（与生产同路径同函数）────────────────────────────────
    sources = load_sources(VALIDATION_CONFIG)
    validate_sources_config(sources, COLLECTOR_MAP)  # 校验失败会 exit(1)
    content_filter_config = load_content_filter(ROOT / "config" / "content_filter.yaml")
    importance_rules_config = load_rules(ROOT / "config" / "importance_rules.yaml")
    ie = validate_rules_config(importance_rules_config)
    if ie:
        print(f"FATAL: importance rules invalid: {ie}")
        return 2
    international_config = load_international_config(
        ROOT / "config" / "international_media.yaml"
    )
    print(
        f"国际媒体层 enabled={international_config.get('enabled')} | "
        f"content_filter enabled={content_filter_config.get('enabled')} | "
        f"importance enabled={importance_rules_config.get('enabled')}"
    )

    # ── 独立临时 DB（默认保留：第二遍运行复用同一 DB 以验证幂等）─────
    if args.reset_db and VALIDATION_DB.exists():
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(VALIDATION_DB) + suffix)
            if p.exists():
                p.unlink()
    db = Database(VALIDATION_DB)
    db.connect()
    db.create_tables()
    rows_before = db.count_articles()

    # ── 真实采集（复用 collect_all：逐源 try/except + URL/identity 去重 +
    #    content filter + save；逐源统计用临时包装记录，不改变逻辑）────
    per_source_stats: dict = {}
    patches = _patch_collectors(per_source_stats)
    ConsoleNotifier()  # 强制 console 通道实例化一次；本脚本绝无 send
    try:
        inserted, total_fetched, dup_count, failed, run_removed, hist_id_dups, filtered_count = (
            collect_all(sources, db, content_filter_config)
        )
    finally:
        _restore_collectors(patches)

    rows_after = db.count_articles()
    by_source_db = db.count_by_source()
    inserted_by_source: dict[str, int] = {}
    for a in inserted:
        inserted_by_source[a.source_id] = inserted_by_source.get(a.source_id, 0) + 1

    # ── freshness（复用 app.freshness；与生产默认一致：catch_up 关闭）──
    now = datetime.now(TAIPEI)
    fr = filter_fresh_articles(inserted, now, catch_up_enabled=False)
    fresh = fr.fresh_articles
    delivery = list(fresh)  # 生产默认 delivery = fresh（无 catch-up）

    freshness_class = {a.url: "fresh" for a in fr.fresh_articles}
    freshness_class.update({a.url: "stale" for a in fr.stale_articles})
    freshness_class.update({a.url: "unknown" for a in fr.unknown_time_articles})
    freshness_class.update({a.url: "future" for a in fr.future_time_articles})

    # ── importance（复用 app.importance 全链路）───────────────────────
    importance_results = classify_articles(
        delivery,
        importance_rules_config,
        international_config=international_config,
    )
    pre_summary = importance_summary(importance_results)
    importance_results = finalize_importance(
        importance_results, importance_rules_config
    )
    post_summary = importance_summary(importance_results)
    importance_by_url = {
        a.url: {"level": r.level, "score": r.score, "track": r.track}
        for a, r in importance_results
    }
    important_urls = [
        a.url for a, r in importance_results if r.level in ("important", "critical")
    ]

    # ── 国际媒体层（复用 app.international；作用于 delivery）──────────
    included, excluded = filter_international(delivery, international_config)
    canonical, coverage = dedupe_international_for_digest(included, international_config)
    merged_events = sum(1 for m in coverage.values() if len(m) > 1)
    relevant_by_url = {a.url: True for a in included}
    relevant_by_url.update({a.url: False for a in excluded})
    tier_by_url: dict[str, str] = {}
    for a in delivery:
        if is_international_media(a.source_name, international_config):
            tier_by_url[a.url] = classify_international(
                a.title, a.summary, a.source_name, international_config
            ).tier

    # 附加：对“全部本轮入库国际文章”做一次层计算（纯函数，用于跨轮
    # coverage 组对比；与生产 delivery 结果独立记录）
    all_incl, all_excl = filter_international(inserted, international_config)
    all_canon, all_cov = dedupe_international_for_digest(all_incl, international_config)
    all_merged = sum(1 for m in all_cov.values() if len(m) > 1)

    # 附加：对验证 DB 全量文章做一次层计算（两遍运行间 DB 不变 -> 结果应
    # 完全一致，是幂等性的强证据：coverage 组跨轮一致）
    db_all_articles = db.get_articles_since(datetime(2000, 1, 1))
    db_incl, db_excl = filter_international(db_all_articles, international_config)
    db_canon, db_cov = dedupe_international_for_digest(db_incl, international_config)
    db_merged = sum(1 for m in db_cov.values() if len(m) > 1)

    # ── digest / 真实交付 Word（复用 build_digest / build_word_digest）─
    digest = build_digest(
        canonical, now,
        international_coverage=coverage,
        international_config=international_config,
    )

    word_articles = list(canonical)
    word_fallback = None
    if not word_articles and inserted:
        # 若 freshness 恰为空（无 90 分钟内新文），改用本轮入库国际文章的
        # canonical 集合做结构验收（同一渲染代码路径），并如实标记 fallback。
        word_articles = list(all_canon)
        word_fallback = "freshness_empty_used_all_inserted_international_relevant"
        digest_fallback = build_digest(
            word_articles, now,
            international_coverage=all_cov,
            international_config=international_config,
        )
    else:
        digest_fallback = None

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    word_result = {"generated": False}
    if word_articles:
        word_path = build_word_digest(
            word_articles, REPORT_DIR, generated_at=now,
            catch_up_urls=set(),
            importance_results=importance_results,
            international_config=international_config,
            international_coverage=(all_cov if word_fallback else coverage),
        )
        stable_copy = REPORT_DIR / f"validation_word_run{run_number}.docx"
        if stable_copy.exists():
            stable_copy.unlink()
        word_path.rename(stable_copy)
        word_result = {
            "generated": True,
            "path": str(word_path),
            "stable_copy": str(stable_copy),
            "filename": stable_copy.name,
            "article_count": len(word_articles),
            "fallback": word_fallback,
        }

    # ── Word 结构验收探针（确定性 fixture，两遍运行字节级一致）────────
    word_probe = None
    if not args.no_probe_word:
        word_probe = _build_probe_word(international_config, run_number)
        print(
            f"  Word 探针: sha={word_probe['sha256'][:16]}… "
            f"text_hash={word_probe['text_hash'][:16]}… "
            f"checks={sum(word_probe['checks'].values())}/{len(word_probe['checks'])}"
        )

    # ── 真实样本 Word（本轮真实国际文章进国际媒体栏目，目视验收用）────
    word_real_sample = None
    if not args.no_real_sample:
        word_real_sample = _build_real_sample_word(inserted, international_config)
        if word_real_sample.get("generated"):
            print(
                f"  Word 真实样本: {Path(word_real_sample['path']).name} "
                f"(articles={word_real_sample['article_count']})"
            )

    # ── 逐源统计汇总 ──────────────────────────────────────────────────
    for s in sources:
        sid = s["id"]
        st = per_source_stats.get(sid, {"fetched": 0, "parsed": 0, "error": "not executed"})
        st["source_name"] = s["name"]
        st["type"] = s["type"]
        st["url"] = s["url"]
        st["inserted"] = inserted_by_source.get(sid, 0)
        st["fresh"] = sum(1 for a in delivery if a.source_id == sid)
        st["relevant"] = sum(1 for a in included if a.source_id == sid)
        st["excluded"] = sum(1 for a in excluded if a.source_id == sid)
        st["important"] = sum(
            1 for a in delivery if a.source_id == sid and a.url in important_urls
        )

    # ── 文章明细 ──────────────────────────────────────────────────────
    article_rows = []
    for a in inserted:
        article_rows.append({
            "url": a.url,
            "title": a.title,
            "source_id": a.source_id,
            "source_name": a.source_name,
            "category": a.category,
            "section": a.section,
            "language": a.language,
            "access_level": a.access_level,
            "published_at": _iso(a.published_at),
            "position": a.position,
            "freshness": freshness_class.get(a.url, "n/a"),
            "relevant": relevant_by_url.get(a.url),
            "tier": tier_by_url.get(a.url),
            "importance": importance_by_url.get(a.url, {"level": "n/a"}).get("level"),
        })

    # ── 产出报告 ──────────────────────────────────────────────────────
    ended = datetime.now(TAIPEI)
    report = {
        "phase": "international_media_phase1_isolated_collection_acceptance",
        "run_number": run_number,
        "started_at": _iso(started),
        "ended_at": _iso(ended),
        "duration_seconds": round((ended - started).total_seconds(), 2),
        "python": sys.version.split()[0],
        "config_files": {
            "sources": str(VALIDATION_CONFIG),
            "content_filter": str(ROOT / "config" / "content_filter.yaml"),
            "importance_rules": str(ROOT / "config" / "importance_rules.yaml"),
            "international_media": str(ROOT / "config" / "international_media.yaml"),
        },
        "database": {
            "validation_db": str(VALIDATION_DB),
            "rows_before": rows_before,
            "rows_after": rows_after,
            "rows_inserted": len(inserted),
            "by_source": by_source_db,
        },
        "network": {
            "endpoints_touched": sorted(ALLOWED_ENDPOINTS),
            "notes": "仅官方 news-sitemap / RSS 元数据端点；未访问任何文章页",
        },
        "safety": {
            "production_news_db_before": prod_before,
            "production_news_db_after": fingerprint(PRODUCTION_DB),
            "production_news_db_unchanged": (
                prod_before == fingerprint(PRODUCTION_DB)
            ),
            "notifier": "console (instantiated, send() never called)",
            "llm_summaries_called": False,
            "feishu_send_called": False,
        },
        "sources": per_source_stats,
        "totals": {
            "fetched": total_fetched,
            "parsed": sum(
                s.get("parsed", 0) for s in per_source_stats.values()
            ),
            "run_url_identity_removed": run_removed,
            "historical_dups": max(0, dup_count - run_removed),
            "content_filtered": filtered_count,
            "inserted": len(inserted),
            "failed_sources": failed,
        },
        "freshness": {
            "fresh": len(fr.fresh_articles),
            "stale": len(fr.stale_articles),
            "unknown_time": len(fr.unknown_time_articles),
            "future_time": len(fr.future_time_articles),
            "delivery_count": len(delivery),
            "catch_up_enabled": False,
        },
        "importance": {
            "before_finalize": pre_summary,
            "after_finalize": post_summary,
            "important_urls": important_urls,
        },
        "international_delivery": {
            "included_relevant": len(included),
            "excluded_irrelevant": len(excluded),
            "canonical_after_dedup": len(canonical),
            "merged_events": merged_events,
            "coverage_groups": [
                {
                    "canonical_url": c,
                    "members": [
                        {"url": m.url, "title": m.title, "source_name": m.source_name}
                        for m in members
                    ],
                }
                for c, members in coverage.items()
            ],
        },
        "international_on_all_collected": {
            "included_relevant": len(all_incl),
            "excluded_irrelevant": len(all_excl),
            "canonical_after_dedup": len(all_canon),
            "merged_events": all_merged,
            "coverage_groups": [
                {
                    "canonical_url": c,
                    "members": [
                        {"url": m.url, "title": m.title, "source_name": m.source_name}
                        for m in members
                    ],
                }
                for c, members in all_cov.items()
            ],
        },
        "international_on_db_all": {
            "db_rows": len(db_all_articles),
            "included_relevant": len(db_incl),
            "excluded_irrelevant": len(db_excl),
            "canonical_after_dedup": len(db_canon),
            "merged_events": db_merged,
            "coverage_groups": [
                {
                    "canonical_url": c,
                    "members": [
                        {"url": m.url, "title": m.title, "source_name": m.source_name}
                        for m in members
                    ],
                }
                for c, members in db_cov.items()
            ],
        },
        "digest": {
            "text": digest,
            "fallback_digest": digest_fallback,
        },
        "word": word_result,
        "word_probe": word_probe,
        "word_real_sample": word_real_sample,
        "articles": article_rows,
    }

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    run_report = REPORT_DIR / f"validation_report_run{run_number}.json"
    latest_report = REPORT_DIR / "validation_report.json"
    payload = json.dumps(report, ensure_ascii=False, indent=2)
    run_report.write_text(payload, encoding="utf-8")
    latest_report.write_text(payload, encoding="utf-8")

    db.close()

    # ── 控制台摘要 ────────────────────────────────────────────────────
    print()
    print("── 逐源结果 ──")
    for s in sources:
        st = per_source_stats[s["id"]]
        err = f" ERROR={st['error']}" if st.get("error") else ""
        print(
            f"  {s['id']}: fetched={st['fetched']} parsed={st['parsed']} "
            f"inserted={st['inserted']} fresh={st['fresh']} "
            f"relevant={st['relevant']} important={st['important']}{err}"
        )
    print("── 汇总 ──")
    print(
        f"  fetched={total_fetched} run_dups_removed={run_removed} "
        f"hist_dups={max(0, dup_count - run_removed)} "
        f"filtered={filtered_count} inserted={len(inserted)} "
        f"failed={failed or 'none'}"
    )
    print(
        f"  fresh={len(fr.fresh_articles)} stale={len(fr.stale_articles)} "
        f"unknown={len(fr.unknown_time_articles)} future={len(fr.future_time_articles)}"
    )
    print(
        f"  international(delivery): included={len(included)} excluded={len(excluded)} "
        f"canonical={len(canonical)} merged_events={merged_events}"
    )
    print(
        f"  international(all inserted): included={len(all_incl)} excluded={len(all_excl)} "
        f"canonical={len(all_canon)} merged_events={all_merged}"
    )
    print(
        f"  international(DB 全量 {len(db_all_articles)} 行): included={len(db_incl)} "
        f"excluded={len(db_excl)} canonical={len(db_canon)} merged_events={db_merged}"
    )
    print(f"  importance: {post_summary}")
    if word_result.get("generated"):
        print(f"  Word(真实交付): {word_result['stable_copy']} (articles={word_result['article_count']})")
        if word_fallback:
            print(f"  Word fallback: {word_fallback}")
    else:
        print("  Word(真实交付): 未生成（本轮无 fresh+relevant 文章）")
    unchanged = prod_before == fingerprint(PRODUCTION_DB)
    print(f"  生产 news.db 未变更: {unchanged}")
    print(f"  报告: {run_report}")
    print()
    return 0


def _build_real_sample_word(inserted: list[Article], cfg: dict) -> dict:
    """真实样本 Word：本轮真实采集的全部国际文章进“国际媒体”栏目（显示层，
    绕过相关度过滤，仅用于目视确认真实英文标题/真实 URL 渲染正常）。"""
    if not inserted:
        return {"generated": False, "reason": "no inserted articles this run"}
    canonical, coverage = dedupe_international_for_digest(inserted, cfg)
    path = build_word_digest(
        canonical, REPORT_DIR, generated_at=datetime.now(TAIPEI),
        catch_up_urls=set(), importance_results=[],
        international_config=cfg, international_coverage=coverage,
    )
    stable = REPORT_DIR / f"validation_word_real_sample.docx"
    if stable.exists():
        stable.unlink()
    path.rename(stable)
    return {
        "generated": True,
        "path": str(stable),
        "article_count": len(canonical),
        "input_count": len(inserted),
        "sha256": sha256_file(stable),
        "note": "真实采集文章，显示层绕过相关度过滤（仅目视渲染验收，非交付语义）",
    }


if __name__ == '__main__':
    sys.exit(main())

