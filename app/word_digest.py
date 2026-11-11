from datetime import datetime
import os
from pathlib import Path
import tempfile
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH
from docx.oxml import parse_xml
from docx.oxml.ns import nsdecls
from docx.shared import Cm, Pt, RGBColor
import html


from .models import Article
from .source_registry import is_official_source, get_source_info, get_official_sources
from .international import display_name, is_international_media
from .international_translation import TranslationResult, translate_article
from .summarizer import clean_summary_text
from .time_utils import TAIPEI, format_article_publish_time
from .election2026.classifier import classify_article as classify_election_article
from .election2026.models import ElectionAnnotation

CATEGORY_NAMES = {
    "politics": "政治新闻",
    "economy": "经济新闻",
    "military": "军武动态",
    "international": "国际及两岸新闻",
    "religion": "宗教",
}
CATEGORY_ORDER = ["politics", "economy", "military", "international", "religion"]
CATEGORY_NUMBERS = ["（一）", "（二）", "（三）", "（四）", "（五）"]
HYPERLINK_REL_TYPE = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"

def _add_article_publish_time(doc, article, label: str = "发布时间") -> None:
    """Render the one canonical precision-aware publish time line."""
    text = format_article_publish_time(article)
    if not text:
        return
    if label != "发布时间" and text.startswith("发布时间："):
        text = label + text[len("发布时间："):]
    paragraph = doc.add_paragraph()
    run = paragraph.add_run(text)
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

def build_word_digest(
    articles: list[Article],
    output_dir: Path,
    generated_at: datetime | None = None,
    catch_up_urls: set[str] | None = None,
    importance_results: list | None = None,
    international_config: dict | None = None,
    international_coverage: dict[str, list[Article]] | None = None,
    international_translations: dict[str, TranslationResult] | None = None,
    election_config: dict | None = None,
    election_entities: dict | None = None,
    election_annotations: dict[str, ElectionAnnotation] | None = None,
    military_topic_urls: set[str] | None = None,
    military_event_config: dict | None = None,
    social_items: list[dict] | None = None,
) -> Path:
    if not articles:
        raise ValueError("No articles to generate Word digest")
    output_dir.mkdir(parents=True, exist_ok=True)
    generated_at = generated_at or datetime.now()
    importance_by_url = {
        article.url: result
        for article, result in (importance_results or [])
    }
    
    if catch_up_urls is None:
        catch_up_urls = set()
    fresh_count = len([a for a in articles if a.url not in catch_up_urls])
    catch_up_count = len([a for a in articles if a.url in catch_up_urls])
    military_topic_urls = set(military_topic_urls or set())
    military_events = []
    event_urls: set[str] = set()
    event_rules: dict | None = None
    # Optional event presentation only; collection, stored topics and importance
    # stay intact. Event construction is deferred until election URLs are known so
    # an election article cannot be re-added through this second military route.
    try:
        from .military.config import load_rules, validate_rules

        event_rules = military_event_config
        if event_rules is None:
            event_rules = load_rules(os.getenv("MILITARY_RULES_PATH") or None)
        if event_rules.get("word_enabled", False):
            validate_rules(event_rules)
        else:
            event_rules = None
    except Exception as exc:
        import logging
        logging.getLogger(__name__).warning("Military event presentation failed; retaining legacy routing: %s", exc)
        event_rules = None

    media_articles = [a for a in articles if not is_official_source(a.source_id)]
    total = len(articles)
    media_count = len(media_articles)

    # 国际媒体层 Phase I：启用配置时，国际媒体文章只进“国际媒体”二级栏目，
    # 不再进入（一）~（五）分类小节；国内“国际新闻”类文章仍进（四）国际新闻。
    intl_enabled = bool(international_config and international_config.get("enabled", False))
    if intl_enabled:
        election_candidate_articles = [
            a for a in media_articles
            if not is_international_media(a.source_name, international_config)
        ]
    else:
        election_candidate_articles = media_articles

    # 九合一选举专题：启用配置时，从国内媒体稿中摘出九合一新闻，
    # 其余仍进（一）~（五）分类小节。专题展示采用 election-first 互斥路由：
    # 九合一稿不再进入普通分类、军武文章或军武事件；双 topic 仍保留在数据库。
    election_enabled = bool(election_config and election_config.get("enabled", False))
    election_articles: list[Article] = []
    election_urls: set[str] = set()
    if election_enabled:
        election_articles = [
            a for a in election_candidate_articles
            if _is_election_article(a, election_config, election_entities, election_annotations)
        ]
        election_urls = {a.url for a in election_articles}

    if event_rules is not None:
        try:
            from .military import is_military_source_article
            from .military.events import build_events

            # 军武栏只收录军武来源新闻：events 分类只对来源白名单内的文章生效，
            # 普通政治/经济频道的军武标题稿不再进入军武事件。
            military_events = build_events(
                [a for a in articles if a.url not in election_urls
                 and is_military_source_article(a)],
                event_rules,
            )
            event_urls = {
                row["url"]
                for event in military_events
                for row in event["articles"]
            }
            military_topic_urls.update(event_urls)
        except Exception as exc:
            import logging
            logging.getLogger(__name__).warning(
                "Military event presentation failed; retaining legacy routing: %s",
                exc,
            )
            military_events = []
            event_urls = set()

    military_topic_urls.difference_update(election_urls)
    # 白名单兜底：陈旧/污染的 topic 记录（非军武来源 URL）不进入军武栏，
    # 被剔除的文章自然回到其原有分类栏目，不会从简报中消失。
    # 白名单不可用时 fail-closed：宁可军武栏为空，也不收录任何 topic。
    if military_topic_urls:
        try:
            from .military import is_military_source_article
            military_topic_urls &= {a.url for a in articles if is_military_source_article(a)}
        except Exception as exc:  # noqa: BLE001 - topic metadata must not block Word
            import logging
            military_topic_urls = set()
            military_events = []
            event_urls = set()
            logging.getLogger(__name__).warning(
                "Military topic source filtering failed; military section disabled: %s",
                exc,
            )
    military_articles = [a for a in articles if a.url in military_topic_urls]
    official_articles = [
        a for a in articles
        if is_official_source(a.source_id) and a.url not in military_topic_urls
    ]
    # Top-level statistics must stay self-consistent: official-military
    # sources are official even when their article is routed to 军武动态.
    official_count = len([
        a for a in articles
        if is_official_source(a.source_id)
    ])

    regular_media_articles = [
        a for a in media_articles if a.url not in military_topic_urls
    ]
    if intl_enabled:
        intl_media_articles = [
            a for a in regular_media_articles
            if is_international_media(a.source_name, international_config)
        ]
        domestic_media_articles = [
            a for a in regular_media_articles
            if not is_international_media(a.source_name, international_config)
        ]
    else:
        intl_media_articles = []
        domestic_media_articles = regular_media_articles

    if election_urls:
        domestic_media_articles = [
            a for a in domestic_media_articles
            if a.url not in election_urls
        ]

    highlighted_articles: list[Article] = []
    highlighted_urls: set[str] = set()
    for article, result in importance_results or []:
        if result.level not in {"critical", "important"}:
            continue
        if article.url in highlighted_urls:
            continue
        highlighted_urls.add(article.url)
        highlighted_articles.append(article)
    
    doc = Document()
    section = doc.sections[0]
    section.page_width = Cm(21.0)
    section.page_height = Cm(29.7)
    section.top_margin = Cm(2.0)
    section.bottom_margin = Cm(2.0)
    section.left_margin = Cm(2.5)
    section.right_margin = Cm(2.5)

    # Keep package metadata stable for deterministic isolated reruns.  The
    # report's visible generated time remains the caller-supplied value.
    stable_core_time = generated_at
    if stable_core_time.tzinfo is not None:
        stable_core_time = stable_core_time.astimezone(TAIPEI).replace(tzinfo=None)
    doc.core_properties.author = "python-docx"
    doc.core_properties.last_modified_by = ""
    doc.core_properties.revision = 1
    doc.core_properties.created = stable_core_time
    doc.core_properties.modified = stable_core_time
    
    title = doc.add_heading("台湾新闻监测", level=0)
    title.alignment = WD_ALIGN_PARAGRAPH.CENTER
    for run in title.runs:
        run.font.size = Pt(22)
    
    meta = doc.add_paragraph()
    meta.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = meta.add_run(f"生成时间：{generated_at.strftime('%Y年%m月%d日 %H:%M')}")
    run.font.size = Pt(11)
    run.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    
    meta2 = doc.add_paragraph()
    meta2.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run2 = meta2.add_run(f"新闻总数：{total}条")
    run2.font.size = Pt(11)
    run2.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    if catch_up_count > 0:
        run3a = meta2.add_run(f"\n正常新闻：{fresh_count}条")
        run3a.font.size = Pt(11)
        run3a.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
        run3b = meta2.add_run(f"\n补发新闻：{catch_up_count}条")
        run3b.font.size = Pt(11)
        run3b.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    run3 = meta2.add_run(f"\n官方信源：{official_count}条")
    run3.font.size = Pt(11)
    run3.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    run4 = meta2.add_run(f"\n新闻媒体：{media_count}条")
    run4.font.size = Pt(11)
    run4.font.color.rgb = RGBColor(0x66, 0x66, 0x66)
    
    heading_num = 0
    
    if official_articles:
        heading_num += 1
        doc.add_heading(f"{_primary_num(heading_num)}、官方信源", level=1)
        official_by_source: dict[str, list[Article]] = {}
        for a in official_articles:
            official_by_source.setdefault(a.source_id, []).append(a)
        official_sources = get_official_sources()
        sub_idx = 0
        for order, sid, info in official_sources:
            if sid not in official_by_source:
                continue
            sub_idx += 1
            cat_arts = official_by_source[sid]
            cat_arts.sort(key=lambda x: (x.published_at.timestamp() if x.published_at else 0, x.position), reverse=True)
            doc.add_heading(f"{'一二三四五六七八九十'[sub_idx-1]}）{info['display_name']}", level=2)
            for idx, article in enumerate(cat_arts, 1):
                p = doc.add_paragraph()
                result = importance_by_url.get(article.url)
                _lev = result.level if result is not None else ""
                _pfx = "【重大】" if _lev == "critical" else "【重点】" if _lev == "important" else ""
                if article.url in catch_up_urls:
                    display_title = f"{idx}. 【补发】{_pfx}{article.title}"
                else:
                    display_title = f"{idx}. {_pfx}{article.title}"
                run = p.add_run(display_title)
                run.bold = True
                run.font.size = Pt(12)

                summary = clean_summary_text(article.summary)
                if summary:
                    p = doc.add_paragraph()
                    run = p.add_run(f"梗概：{summary}")
                    run.font.size = Pt(10)
                    run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
                    run.italic = True
                
                p = doc.add_paragraph()
                run = p.add_run(f"来源：{article.source_name}")
                run.font.size = Pt(10)
                run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
                
                p = doc.add_paragraph()
                run = p.add_run(f"类型：{info['document_type']}")
                run.font.size = Pt(10)
                run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
                
                _add_article_publish_time(doc, article)
                
                p = doc.add_paragraph()
                if article.url in catch_up_urls:
                    p = doc.add_paragraph()
                    run = p.add_run("状态：补发")
                    run.font.size = Pt(10)
                    run.font.color.rgb = RGBColor(0xCC, 0x66, 0x00)
                p = doc.add_paragraph()
                _add_hyperlink(p, article.url, article.url)
                doc.add_paragraph()

    if highlighted_articles:
        heading_num += 1
        doc.add_heading(f"{_primary_num(heading_num)}、重点提示", level=1)
        for idx, article in enumerate(highlighted_articles, 1):
            _render_media_item(
                doc, article, idx, catch_up_urls,
                importance_results, prefix_note="",
                importance_by_url=importance_by_url,
            )
    
    # 九合一选举专题一级栏目：位于官方信源之后、新闻媒体之前。
    # 空则整栏隐藏；二级分栏（全局动向/县市）动态生成、动态编号。
    if election_articles:
        heading_num += 1
        doc.add_heading(f"{_primary_num(heading_num)}、九合一选举", level=1)
        election_groups = _group_election_articles(
            election_articles, election_config, election_entities,
            election_annotations, importance_results,
            importance_by_url=importance_by_url,
        )
        for sub_idx, (group_title, group_articles) in enumerate(election_groups, 1):
            doc.add_heading(f"{_subsection_num(sub_idx)}{group_title}", level=2)
            for idx, article in enumerate(group_articles, 1):
                _render_media_item(
                    doc, article, idx, catch_up_urls,
                    importance_results, prefix_note="",
                    importance_by_url=importance_by_url,
                )

    if military_articles or military_events:
        heading_num += 1
        doc.add_heading(f"{_primary_num(heading_num)}、军武动态", level=1)
        if military_events:
            from .military.output import render_word_events
            render_word_events(doc, military_events, catch_up_urls)
        military_articles.sort(
            key=lambda x: (
                x.published_at.timestamp() if x.published_at else 0,
                x.position,
            ),
            reverse=True,
        )
        legacy_military_articles = [a for a in military_articles if a.url not in event_urls]
        for idx, article in enumerate(legacy_military_articles, len(military_events) + 1):
            _render_media_item(
                doc, article, idx, catch_up_urls,
                importance_results, prefix_note="",
                importance_by_url=importance_by_url,
            )
    
    if media_articles:
        grouped: dict[str, list[Article]] = {}
        for cat in CATEGORY_ORDER:
            grouped[cat] = []
        for article in domestic_media_articles:
            if article.category in grouped:
                grouped[article.category].append(article)
        for cat_arts in grouped.values():
            cat_arts.sort(key=lambda x: (x.published_at.timestamp() if x.published_at else 0, x.position), reverse=True)
        
        for cat in CATEGORY_ORDER:
            cat_articles = grouped.get(cat, [])
            if not cat_articles:
                continue
            cat_name = CATEGORY_NAMES.get(cat, cat)
            heading_num += 1
            doc.add_heading(
                f"{_primary_num(heading_num)}、{cat_name}", level=1
            )
            for idx, article in enumerate(cat_articles, 1):
                p = doc.add_paragraph()
                result = importance_by_url.get(article.url)
                _lev = result.level if result is not None else ""
                _pfx = "【重大】" if _lev == "critical" else "【重点】" if _lev == "important" else ""
                if article.url in catch_up_urls:
                    display_title = f"{idx}. 【补发】{_pfx}{article.title}"
                else:
                    display_title = f"{idx}. {_pfx}{article.title}"
                run = p.add_run(display_title)
                run.bold = True
                run.font.size = Pt(12)

                summary = clean_summary_text(article.summary)
                if summary:
                    p = doc.add_paragraph()
                    run = p.add_run(f"梗概：{summary}")
                    run.font.size = Pt(10)
                    run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
                    run.italic = True
                
                p = doc.add_paragraph()
                run = p.add_run(f"来源：{article.source_name}")
                run.font.size = Pt(10)
                run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
                
                _add_article_publish_time(doc, article)
                
                p = doc.add_paragraph()
                if article.url in catch_up_urls:
                    p = doc.add_paragraph()
                    run = p.add_run("状态：补发")
                    run.font.size = Pt(10)
                    run.font.color.rgb = RGBColor(0xCC, 0x66, 0x00)
                p = doc.add_paragraph()
                _add_hyperlink(p, article.url, article.url)
                doc.add_paragraph()

        # 国际媒体二级栏目（Phase I）：位于分类小节之后，编号接续现有分类小节。
            # 仅当 international_config 启用时渲染；栏目内按重要性、发布时间排序。
        if intl_media_articles:
            heading_num += 1
            doc.add_heading(
                f"{_primary_num(heading_num)}、国际媒体", level=1
            )
            def _importance_order(article: Article) -> int:
                result = importance_by_url.get(article.url)
                if result is not None:
                    return {"critical": 0, "important": 1, "normal": 2}.get(result.level, 2)
                return 2

            intl_sorted = sorted(
                intl_media_articles,
                key=lambda x: (
                    _importance_order(x),
                    -(x.published_at.timestamp() if x.published_at else 0),
                    -x.position,
                ),
            )
            for idx, article in enumerate(intl_sorted, 1):
                p = doc.add_paragraph()
                result = importance_by_url.get(article.url)
                _lev = result.level if result is not None else ""
                _pfx = "【重大】" if _lev == "critical" else "【重点】" if _lev == "important" else ""
                translation = (international_translations or {}).get(article.url)
                if translation is None:
                    # Missing mapping is a legal, metadata-only fallback; it
                    # must never look like a real Chinese translation.
                    translation = translate_article(article, translator=None)
                # A fallback must not relabel an English title as Chinese.
                # The canonical English title is rendered in the dedicated
                # field below; only a real translator gets a title line.
                if translation.status == "translated" and translation.cn_title:
                    chinese_title = translation.cn_title
                    if article.url in catch_up_urls:
                        display_title = f"{idx}. 【补发】{_pfx}{chinese_title}"
                    else:
                        display_title = f"{idx}. {_pfx}{chinese_title}"
                    run = p.add_run(display_title)
                    run.bold = True
                    run.font.size = Pt(12)

                # 来源：中文展示名（英文 canonical 名），英文标题原样输出不做翻译
                p = doc.add_paragraph()
                run = p.add_run(
                    f"来源：{display_name(article.source_name, international_config)}"
                    f"（{article.source_name}）"
                )
                run.font.size = Pt(10)
                run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

                p = doc.add_paragraph()
                run = p.add_run(f"英文原题：{article.title}")
                run.font.size = Pt(10)
                run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

                # LLM summaries in this pipeline are Chinese.  Only an RSS/
                # metadata teaser (or an unspecified legacy value) may be
                # presented as the publisher's English summary.
                english_summary = (
                    clean_summary_text(article.summary)
                    if article.summary_source in {None, "rss", "meta"}
                    else None
                )
                p = doc.add_paragraph()
                run = p.add_run(
                    f"英文摘要：{english_summary or '未提供合法英文摘要。'}"
                )
                run.font.size = Pt(10)
                run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
                run.italic = True

                _add_article_publish_time(doc, article, label="发布时间（Asia/Taipei）")

                if translation.status == "translated":
                    translated_summary = translation.cn_summary or "未提供中文摘要。"
                else:
                    translated_summary = ""
                if "系统判断：" in translated_summary:
                    facts, judgment = translated_summary.split("系统判断：", 1)
                    facts = facts.replace("媒体报道事实：", "").strip(" ：")
                    judgment = judgment.strip()
                    p = doc.add_paragraph()
                    run = p.add_run(f"中文摘要：{facts}")
                    run.font.size = Pt(10)
                    run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
                    run.italic = True
                    p = doc.add_paragraph()
                    run = p.add_run(f"系统判断：{judgment}")
                    run.font.size = Pt(10)
                    run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
                elif translated_summary:
                    p = doc.add_paragraph()
                    run = p.add_run(f"中文摘要：{translated_summary}")
                    run.font.size = Pt(10)
                    run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
                    run.italic = True
                if translation.status == "fallback" and translation.limitation:
                    p = doc.add_paragraph()
                    run = p.add_run(f"限制：{translation.limitation}")
                    run.font.size = Pt(9)
                    run.font.color.rgb = RGBColor(0x99, 0x66, 0x00)

                p = doc.add_paragraph()
                if article.url in catch_up_urls:
                    p = doc.add_paragraph()
                    run = p.add_run("状态：补发")
                    run.font.size = Pt(10)
                    run.font.color.rgb = RGBColor(0xCC, 0x66, 0x00)
                p = doc.add_paragraph()
                _add_hyperlink(p, article.url, article.url)

                # 同一事件的其他国际媒体报道（canonical 且有 coverage 时追加一行）
                members = (international_coverage or {}).get(article.url) or []
                others = [m for m in members if m.url != article.url]
                if others:
                    names = [
                        display_name(m.source_name, international_config) for m in others
                    ]
                    tail = "等" if len(names) > 1 else ""
                    p = doc.add_paragraph()
                    run = p.add_run(f"另据{'、'.join(names)}{tail}报道同一事件")
                    run.font.size = Pt(10)
                    run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
                    p = doc.add_paragraph()
                    run = p.add_run("Coverage 原文链接：")
                    run.font.size = Pt(9)
                    run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)
                    for link_index, member in enumerate(others):
                        if link_index:
                            p.add_run("；")
                        _add_hyperlink(p, member.url, member.url)
                doc.add_paragraph()

    if social_items:
        from .social_monitor.word_section import append_social_section
        append_social_section(doc, social_items, generated_at)

    doc.add_paragraph()
    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run("本简报由自动新闻监测程序生成，具体内容以原媒体报道为准。")
    run.font.size = Pt(9)
    run.font.color.rgb = RGBColor(0x99, 0x99, 0x99)
    run.italic = True
    
    filename = f"台湾新闻监测_{generated_at.strftime('%Y-%m-%d_%H%M')}.docx"
    output_path = output_dir / filename
    doc.save(str(output_path))
    _normalise_docx_package(output_path)
    return output_path


def _primary_num(index: int) -> str:
    """Return a safe primary-section label without a fixed section limit."""
    hanzi = "一二三四五六七八九十"
    if 1 <= index <= 10:
        return hanzi[index - 1]
    return str(index)


def _subsection_num(index: int) -> str:
    """二级标题编号：（一）（二）…（十）→ 超过十用（11）（12）…

    九合一栏最多可含 22 个县市分组 + 全局动向，汉字下标法只支持到十。
    """
    hanzi = "一二三四五六七八九十"
    if 1 <= index <= 10:
        return f"（{hanzi[index-1]}）"
    return f"（{index}）"


def _election_annotation_for(
    article: Article,
    election_config: dict | None,
    election_entities: dict | None,
    election_annotations: dict[str, ElectionAnnotation] | None,
) -> ElectionAnnotation:
    """取一篇文章的九合一标注。

    优先用调用方传入的内存标注（主链路已算好）；缺省时用同一确定性
    纯函数兜底重算（--export-word 等历史稿场景），保证结果一致。
    """
    if election_annotations and article.url in election_annotations:
        return election_annotations[article.url]
    return classify_election_article(
        article.title,
        article.summary or "",
        config=election_config,
        entities=election_entities,
    )


def _is_election_article(
    article: Article,
    election_config: dict | None,
    election_entities: dict | None,
    election_annotations: dict[str, ElectionAnnotation] | None,
) -> bool:
    return _election_annotation_for(
        article, election_config, election_entities, election_annotations
    ).is_election


def _group_election_articles(
    articles: list[Article],
    election_config: dict | None,
    election_entities: dict | None,
    election_annotations: dict[str, ElectionAnnotation] | None,
    importance_results: list | None,
    importance_by_url: dict | None = None,
) -> list[tuple[str, list[Article]]]:
    """把九合一稿按展示分组：全局动向 + 各县市（含合并组）。

    返回 [(组标题, [文章...])]，组顺序 = 全局动向优先 + regions.display_order；
    空组不出现。组内按 importance（critical/important/normal）→ 时间倒序。
    """
    if not election_config:
        return []
    if importance_by_url is None:
        importance_by_url = {
            article.url: result
            for article, result in (importance_results or [])
        }
    regions_cfg = election_config.get("regions", {})
    merge_groups = regions_cfg.get("merge_groups", {}) or {}
    display_order = regions_cfg.get("display_order", []) or []

    # 每篇文章归属的展示组名
    def _display_group(article: Article) -> str:
        ann = _election_annotation_for(
            article, election_config, election_entities, election_annotations
        )
        if ann.scope == "national":
            return "全局動向"
        if ann.scope in ("local", "multi_region"):
            # 若涉及县市属于某个合并组，展示为该合并组名
            std_regions = ann.regions or ([ann.region] if ann.region else [])
            for group_name, members in merge_groups.items():
                if any(r in members for r in std_regions):
                    return group_name
            # 否则展示主县市（若在 display_order 中）
            if ann.region in display_order:
                return ann.region
            # 兜底：display_order 中出现的县市
            for region in std_regions:
                if region in display_order:
                    return region
        return "全局動向"

    grouped: dict[str, list[Article]] = {}
    for article in articles:
        grouped.setdefault(_display_group(article), []).append(article)

    # 排序键：importance → 时间倒序
    def _sort_key(article: Article):
        result = importance_by_url.get(article.url)
        level = result.level if result is not None else ""
        order = {"critical": 0, "important": 1, "normal": 2}.get(level, 2)
        return (
            order,
            -(article.published_at.timestamp() if article.published_at else 0),
            -article.position,
        )

    result: list[tuple[str, list[Article]]] = []
    # 全局动向优先
    if "全局動向" in grouped:
        result.append(
            ("全局動向", sorted(grouped["全局動向"], key=_sort_key))
        )
    # 其余按 display_order
    for group_name in display_order:
        if group_name == "全局動向":
            continue
        if group_name in grouped:
            result.append((group_name, sorted(grouped[group_name], key=_sort_key)))
    return result


def _render_media_item(
    doc,
    article: Article,
    idx: int,
    catch_up_urls: set[str],
    importance_results: list | None,
    prefix_note: str = "",
    importance_by_url: dict | None = None,
) -> None:
    """渲染一条媒体稿条目（标题/梗概/来源/时间/链接）。

    与既有新闻媒体栏共用同一样式与【重大】【重点】【补发】前缀逻辑。
    """
    if importance_by_url is None:
        importance_by_url = {
            item.url: result
            for item, result in (importance_results or [])
        }
    result = importance_by_url.get(article.url)
    _lev = result.level if result is not None else ""
    _pfx = "【重大】" if _lev == "critical" else "【重点】" if _lev == "important" else ""
    if prefix_note:
        _pfx = f"{prefix_note}{_pfx}"
    if article.url in catch_up_urls:
        display_title = f"{idx}. 【补发】{_pfx}{article.title}"
    else:
        display_title = f"{idx}. {_pfx}{article.title}"
    p = doc.add_paragraph()
    run = p.add_run(display_title)
    run.bold = True
    run.font.size = Pt(12)

    summary = clean_summary_text(article.summary)
    if summary:
        p = doc.add_paragraph()
        run = p.add_run(f"梗概：{summary}")
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(0x33, 0x33, 0x33)
        run.italic = True

    p = doc.add_paragraph()
    run = p.add_run(f"来源：{article.source_name}")
    run.font.size = Pt(10)
    run.font.color.rgb = RGBColor(0x55, 0x55, 0x55)

    _add_article_publish_time(doc, article)

    p = doc.add_paragraph()
    if article.url in catch_up_urls:
        p = doc.add_paragraph()
        run = p.add_run("状态：补发")
        run.font.size = Pt(10)
        run.font.color.rgb = RGBColor(0xCC, 0x66, 0x00)
    p = doc.add_paragraph()
    _add_hyperlink(p, article.url, article.url)
    doc.add_paragraph()


def _normalise_docx_package(path: Path) -> None:
    """Rewrite ZIP metadata so identical input produces identical bytes."""

    temp_path: Path | None = None
    try:
        fd, raw_temp = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=str(path.parent)
        )
        os.close(fd)
        temp_path = Path(raw_temp)
        with ZipFile(path, "r") as source, ZipFile(temp_path, "w") as target:
            for item in sorted(source.infolist(), key=lambda entry: entry.filename):
                info = ZipInfo(item.filename, date_time=(1980, 1, 1, 0, 0, 0))
                info.compress_type = item.compress_type
                info.comment = item.comment
                info.create_system = item.create_system
                info.external_attr = item.external_attr
                info.extra = item.extra
                info.flag_bits = item.flag_bits
                target.writestr(info, source.read(item.filename))
        os.replace(temp_path, path)
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass

def _add_hyperlink(paragraph, url: str, text: str) -> None:
    part = paragraph.part
    r_id = part.relate_to(url, HYPERLINK_REL_TYPE, is_external=True)
    safe_text = html.escape(text)
    hyperlink_xml = (
        f'<w:hyperlink {nsdecls("w")} r:id="{r_id}" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<w:r><w:rPr><w:color w:val=\"0563C1\"/><w:u w:val=\"single\"/><w:sz w:val=\"20\"/></w:rPr>"
        f"<w:t xml:space=\"preserve\">{safe_text}</w:t></w:r></w:hyperlink>"
    )
    hyperlink = parse_xml(hyperlink_xml)
    paragraph._p.append(hyperlink)
