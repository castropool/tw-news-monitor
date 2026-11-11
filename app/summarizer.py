"""Best-effort article summary enrichment (RSS teasers + content + DeepSeek).

Tiers, controlled by ``SUMMARIZER_MODE`` (default ``hybrid``):

- ``rss``: feed teaser text captured during collection (zero extra requests)
- ``llm``: batch DeepSeek generation from title/teaser only
- ``hybrid``: fetch article body, extract main text, then batch DeepSeek
  (CNA-quality summaries for sources without native RSS teasers)
- ``meta``: fallback to <meta name="description"> / og:description
- ``none``: disable all summary enrichment

This module is best-effort by design: it never raises, and summary failures
must never block Word generation or Feishu delivery.
"""

import json
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

from .deepseek_analysis import DeepSeekClient
from .models import Article

logger = logging.getLogger(__name__)

DEFAULT_MAX_LENGTH = 150
DEFAULT_BATCH_SIZE = 40
DEFAULT_CONTENT_CHARS = 800
DEFAULT_RETRY_HOURS = 6
SENTENCE_ENDINGS = frozenset("。！？!?")
TRAILING_CUTOFF_RE = re.compile(r"(?:\s*(?:\.{2,}|…+|⋯+|︙+)\s*)+$")
META_TIMEOUT = 15.0
META_MAX_WORKERS = 5
META_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/120.0.0.0 Safari/537.36"
)

PROMPT_PATH = Path(__file__).resolve().parent.parent / "prompts" / "summarize.md"
TRANSLATION_SYSTEM_PROMPT = (
    "你是国际新闻翻译助手。仅根据提供的英文标题、英文导语和媒体名称，"
    "返回严格 JSON 对象：{\"title\": \"繁体中文标题\", \"summary\": \"繁体中文摘要\"}。"
    "不得使用外部知识、不得补充事实、不得访问或要求文章链接或正文；摘要必须是完整句子。"
)


def _int_env(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, str(default)).strip())
    except (TypeError, ValueError):
        return default


def summarizer_mode() -> str:
    mode = os.getenv("SUMMARIZER_MODE", "hybrid").strip().lower()
    if mode not in ("none", "rss", "llm", "meta", "hybrid"):
        logger.warning("Unknown SUMMARIZER_MODE=%r, falling back to hybrid", mode)
        return "hybrid"
    return mode


def truncate_to_complete_sentence(text: str, max_length: int = DEFAULT_MAX_LENGTH) -> str:
    """Keep a complete sentence within the target length when possible.

    The length limit is intentionally soft: returning an intact source teaser
    is preferable to fabricating an ellipsis in the middle of a sentence.
    """
    text = (text or "").strip()
    if len(text) <= max_length:
        return text
    endings = [index for index, char in enumerate(text[:max_length]) if char in SENTENCE_ENDINGS]
    if endings:
        return text[: endings[-1] + 1].rstrip()
    return text


def _sentence_end_positions(text: str):
    """Yield positions that can safely terminate a sentence."""
    for index, char in enumerate(text):
        if char in SENTENCE_ENDINGS:
            yield index
            continue
        if char not in ".．":
            continue
        next_char = text[index + 1 : index + 2]
        if not next_char or next_char.isspace() or next_char in "」』）》)\"'”’":
            yield index


def _strip_trailing_cutoff_marker(text: str) -> tuple[str, bool]:
    """Remove a feed/model hard-cut marker and report whether one was found."""
    match = TRAILING_CUTOFF_RE.search(text)
    if not match:
        return text, False
    return text[: match.start()].rstrip(), True


def clean_summary_text(text: str | None, max_length: int | None = None) -> str | None:
    """Return a safe summary without a demonstrably incomplete tail.

    Feed teasers and model responses sometimes end with a visible hard-cut
    marker.  When that marker is present, only text through the last complete
    sentence is retained.  If the whole value is a cut fragment, return None
    instead of presenting an incomplete sentence or inventing missing facts.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    normalized = re.sub(r"\s+", " ", text).strip()
    normalized, was_cut = _strip_trailing_cutoff_marker(normalized)
    if was_cut:
        endings = list(_sentence_end_positions(normalized))
        if not endings:
            return None
        normalized = normalized[: endings[-1] + 1].rstrip()
    if not normalized:
        return None
    if max_length is not None:
        normalized = truncate_to_complete_sentence(normalized, max_length)
    return normalized or None


def summary_needs_rewrite(article: Article, max_length: int = DEFAULT_MAX_LENGTH) -> bool:
    """Return whether an existing summary has a visible hard-cut marker."""
    summary = (getattr(article, "summary", None) or "").strip()
    del max_length
    return bool(summary) and bool(TRAILING_CUTOFF_RE.search(summary))


def _env_truthy(name: str) -> bool:
    return os.getenv(name, "").strip().lower() in {"1", "true", "yes", "on"}


def translate_metadata(
    title: str,
    summary: str | None,
    *,
    source_name: str,
) -> tuple[str, str]:
    """Translate supplied international metadata without fetching article content."""
    if not _env_truthy("INTERNATIONAL_TRANSLATION_ENABLED"):
        raise RuntimeError("international metadata translation is disabled")
    client = _load_deepseek_client()
    if client is None:
        raise RuntimeError("international metadata translation client unavailable")
    payload = {
        "title": (title or "").strip(),
        "summary": (summary or "").strip(),
        "source_name": (source_name or "").strip(),
    }
    response = client.analyze(
        TRANSLATION_SYSTEM_PROMPT,
        json.dumps(payload, ensure_ascii=False),
    )
    if response.get("status") != "success":
        raise RuntimeError(response.get("error", "international metadata translation failed"))
    cn_title = response.get("title")
    cn_summary = response.get("summary")
    if not isinstance(cn_title, str) or not isinstance(cn_summary, str):
        raise ValueError("international metadata translation returned invalid fields")
    cn_title = cn_title.strip()
    cn_summary = clean_summary_text(
        cn_summary, _int_env("SUMMARIZER_MAX_LENGTH", DEFAULT_MAX_LENGTH)
    )
    if not cn_title or not cn_summary:
        raise ValueError("international metadata translation returned empty fields")
    return cn_title, cn_summary


def clean_rss_summary(text: str, max_length: int = DEFAULT_MAX_LENGTH) -> str | None:
    """Strip HTML and collapse whitespace from a feed teaser."""
    if not text:
        return None
    soup = BeautifulSoup(text, "html.parser")
    plain = soup.get_text()
    plain = re.sub(r"\s+", " ", plain).strip()
    plain = _strip_leading_captions(plain)
    if not plain:
        return None
    return clean_summary_text(plain, max_length=max_length)


def _strip_leading_captions(text: str) -> str:
    """Remove leading image-caption boilerplate seen in some feeds.

    Example (Newtalk): "圖為...。（中央社檔案照片） 圖：中央社提供 Newtalk新聞
    解放軍今天..." -> "解放軍今天..."
    """
    s = text
    while True:
        m = re.match(r"^圖為[^）)]*[）)]\s*", s)
        if m:
            s = s[m.end():]
            continue
        m = re.match(r"^圖：[^\s，。]{1,10}(?:\s+[^\s，。]{1,10}){0,1}\s*", s)
        if m:
            s = s[m.end():]
            continue
        return s


def rss_summary_from_entry(entry, max_length: int = DEFAULT_MAX_LENGTH) -> str | None:
    """Extract a clean teaser from a feedparser entry (zero extra requests)."""
    raw = (entry.get("summary") or entry.get("description") or "").strip()
    return clean_rss_summary(raw, max_length=max_length)


def deepseek_available() -> bool:
    """Whether the summarizer's LLM endpoint is configured.

    The summarizer is fully independent from the election assessment
    pipeline: it enables itself from ``SUMMARIZER_API_KEY`` (any non-empty
    placeholder works - the local Command Code gateway does not validate
    keys) and falls back to ``DEEPSEEK_API_KEY`` only for older .env files.
    """
    return bool(
        (os.getenv("SUMMARIZER_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")).strip()
    )


def _load_deepseek_client() -> DeepSeekClient | None:
    """Build the summarizer's LLM client.

    The summarizer resolves its endpoint independently from the election
    assessment pipeline so the two can point at different providers:
    ``SUMMARIZER_BASE_URL`` / ``SUMMARIZER_MODEL`` win when set (e.g. a local
    Command Code gateway + ``deepseek/deepseek-v4-flash``), otherwise they
    fall back to the shared ``DEEPSEEK_*`` variables.
    """
    if not deepseek_available():
        return None
    api_key = os.getenv("SUMMARIZER_API_KEY", "") or os.getenv("DEEPSEEK_API_KEY", "")
    return DeepSeekClient(
        api_key=api_key.strip(),
        base_url=os.getenv(
            "SUMMARIZER_BASE_URL",
            os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
        ),
        model=os.getenv(
            "SUMMARIZER_MODEL",
            os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
        ),
        timeout=_int_env("SUMMARIZER_TIMEOUT_SECONDS", 180),
        max_retries=_int_env("SUMMARIZER_MAX_RETRIES", 2),
    )


def _load_system_prompt() -> str:
    if PROMPT_PATH.exists():
        return PROMPT_PATH.read_text(encoding="utf-8")
    return (
        "你是台湾新闻摘要助手。根据给定的标题、导语和正文为每篇新闻撰写中文摘要。"
        "要求：1) 严格按新闻五要素组织（时间、人物或机构、事件、地点、原因或背景），"
        "输入中没有的要素不得编造；"
        "2) 每条摘要 50-120 字，用完整句子，必须使用繁体中文，优先保留日期、数字、人名和机构名，"
        "不得只复述标题；"
        "3) 只压缩输入信息，不得添加输入中没有的事实；"
        "4) 如果某条 has_content=false（没有正文也没有导语），必须仅依据标题概括，"
        "禁止用任何外部知识补充日期、人名、数字或机构名，宁可输出极简标题转述，不得扩写；"
        "5) 输出严格 JSON 对象，键为输入中的 url，值为摘要；不得遗漏或新增条目。"
    )


def _teaser_for(article: Article, max_chars: int = 300) -> str:
    return (article.summary or "")[:max_chars]


def build_batch_prompt(
    articles: list[Article],
    contents: dict[str, str] | None = None,
    max_teaser_chars: int = 300,
    max_content_chars: int | None = None,
) -> tuple[str, str]:
    contents = contents or {}
    max_content_chars = max_content_chars or _int_env(
        "SUMMARIZER_CONTENT_CHARS", DEFAULT_CONTENT_CHARS
    )
    items = []
    for a in articles:
        item = {"url": a.url, "title": a.title, "teaser": _teaser_for(a, max_teaser_chars)}
        content = contents.get(a.url, "")
        if content:
            item["content"] = content[:max_content_chars]
        item["has_content"] = bool(content)
        items.append(item)
    return _load_system_prompt(), json.dumps({"articles": items}, ensure_ascii=False)


def parse_summaries_response(
    data, requested_urls: set[str], max_length: int = DEFAULT_MAX_LENGTH
) -> dict[str, str]:
    """Normalize DeepSeek summary responses into {url: summary}.

    Accepts ``{url: text}``, ``{"summaries": {url: text}}``, or a list of
    ``{"url": ..., "summary": ...}`` items. Unknown URLs are ignored.
    """
    if isinstance(data, dict):
        raw = data.get("summaries") or data.get("summary") or data
    elif isinstance(data, list):
        raw = {}
        for item in data:
            if isinstance(item, dict) and item.get("url"):
                raw[item["url"]] = item.get("summary") or item.get("content") or ""
    else:
        return {}

    if not isinstance(raw, dict):
        return {}

    result: dict[str, str] = {}
    for url, text in raw.items():
        if url not in requested_urls:
            continue
        if not isinstance(text, str):
            continue
        text = re.sub(r"\s+", " ", text).strip()
        if len(text) < 5:
            continue
        text = clean_summary_text(text, max_length=max_length)
        if text:
            result[url] = text
    return result


def summarize_with_deepseek(
    articles: list[Article],
    client: DeepSeekClient | None = None,
    batch_size: int | None = None,
    max_length: int | None = None,
    contents: dict[str, str] | None = None,
) -> dict[str, str]:
    """Generate summaries in batches; returns {url: summary} for successes."""
    if not articles:
        return {}
    batch_size = batch_size or _int_env("SUMMARIZER_BATCH_SIZE", DEFAULT_BATCH_SIZE)
    max_length = max_length or _int_env("SUMMARIZER_MAX_LENGTH", DEFAULT_MAX_LENGTH)
    if client is None:
        client = _load_deepseek_client()
    if client is None:
        logger.info("DeepSeek summarizer skipped: DEEPSEEK_API_KEY not set")
        return {}

    results: dict[str, str] = {}
    for start in range(0, len(articles), batch_size):
        chunk = articles[start : start + batch_size]
        batch_no = start // batch_size + 1
        system, user = build_batch_prompt(chunk, contents=contents)
        try:
            resp = client.analyze(system, user)
        except Exception as exc:
            logger.warning("DeepSeek summary batch %d failed: %s", batch_no, exc)
            continue
        if resp.get("status") != "success":
            logger.warning(
                "DeepSeek summary batch %d failed: %s",
                batch_no,
                resp.get("error"),
            )
            continue
        chunk_result = parse_summaries_response(
            resp, {a.url for a in chunk}, max_length=max_length
        )
        logger.info(
            "DeepSeek summaries: batch %d got %d/%d",
            batch_no,
            len(chunk_result),
            len(chunk),
        )
        results.update(chunk_result)
    return results


def _extract_article_text(html: str) -> str | None:
    """Extract main article text with trafilatura (best effort)."""
    try:
        from trafilatura import extract
    except ImportError:
        logger.warning("trafilatura is not installed; content extraction disabled")
        return None
    try:
        text = extract(
            html,
            include_comments=False,
            include_tables=False,
            include_links=False,
            include_images=False,
        )
    except Exception as exc:
        logger.debug("trafilatura extraction failed: %s", exc)
        return None
    if not text:
        return None
    plain = re.sub(r"\s+", " ", text).strip()
    return plain or None


def _fetch_article_content(url: str, client: httpx.Client) -> tuple[str | None, bool]:
    """Return (text, fetched_ok). fetched_ok=False on network/HTTP errors."""
    try:
        resp = client.get(url, timeout=META_TIMEOUT)
        resp.raise_for_status()
    except Exception as exc:
        logger.debug("Article content fetch failed for %s: %s", url, exc)
        return None, False
    text = _extract_article_text(resp.text)
    if not text:
        text = _extract_meta_description(resp.text)
    return text, True


def fetch_article_contents(
    articles: list[Article], max_workers: int | None = None
) -> tuple[dict[str, str], list[str]]:
    """Fetch article pages and extract main text.

    Returns (contents, empty_urls). ``empty_urls`` contains pages that were
    fetched successfully but yielded no extractable text (permanent misses);
    network errors are not included so they retry on the next run.
    """
    urls = [a.url for a in articles]
    if not urls:
        return {}, []
    results: dict[str, str] = {}
    empty_urls: list[str] = []
    max_workers = max_workers or META_MAX_WORKERS
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        with httpx.Client(
            headers={"User-Agent": META_USER_AGENT},
            follow_redirects=True,
        ) as client:
            futures = {
                executor.submit(_fetch_article_content, url, client): url
                for url in urls
            }
            for future in as_completed(futures):
                url = futures[future]
                text, fetched_ok = future.result()
                if not fetched_ok:
                    continue
                if text:
                    results[url] = text
                else:
                    empty_urls.append(url)
    logger.info("Article contents fetched: %d/%d", len(results), len(urls))
    return results, empty_urls


def _extract_meta_description(html: str) -> str | None:
    soup = BeautifulSoup(html, "html.parser")
    for attr in ("property", "name"):
        meta = soup.find(
            "meta", attrs={attr: re.compile(r"^(?:og:)?description$", re.I)}
        )
        if meta and meta.get("content"):
            text = meta["content"].strip()
            if text:
                return text
    return None


def _fetch_meta_description(url: str, client: httpx.Client) -> str | None:
    try:
        resp = client.get(url, timeout=META_TIMEOUT)
        resp.raise_for_status()
        return _extract_meta_description(resp.text)
    except Exception as exc:
        logger.debug("Meta description fetch failed for %s: %s", url, exc)
        return None


def fetch_meta_descriptions(
    articles: list[Article], max_workers: int | None = None
) -> dict[str, str]:
    """Fetch meta description fallback; one GET per article (best effort)."""
    urls = [a.url for a in articles]
    if not urls:
        return {}
    results: dict[str, str] = {}
    max_workers = max_workers or META_MAX_WORKERS
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        with httpx.Client(
            headers={"User-Agent": META_USER_AGENT},
            follow_redirects=True,
        ) as client:
            futures = {
                executor.submit(_fetch_meta_description, url, client): url
                for url in urls
            }
            for future in as_completed(futures):
                url = futures[future]
                summary = clean_summary_text(future.result())
                if summary:
                    results[url] = summary
    logger.info("Meta summaries fetched: %d/%d", len(results), len(urls))
    return results


def enrich_articles_with_summaries(articles: list[Article], db=None) -> list[Article]:
    """Add summaries to articles lacking one. Best-effort, never raises.

    RSS teasers are already attached by collectors; this fills the gaps by
    fetching article content (hybrid) and calling DeepSeek in batches.

    Articles with an explicit ``access_level`` belong to the international
    media layer. Their collector/newsletter metadata is the legal boundary:
    they may receive a title/teaser-only LLM summary, but this function must
    never fetch their article page or meta description.
    """
    if not articles:
        return articles
    try:
        mode = summarizer_mode()
        if mode in ("none", "rss"):
            return articles

        now = datetime.now(timezone.utc)
        retry_hours = _int_env("SUMMARIZER_RETRY_HOURS", DEFAULT_RETRY_HOURS)

        def _retryable(a: Article) -> bool:
            if a.summary_attempted_at is None:
                return True
            at = a.summary_attempted_at
            if at.tzinfo is None:
                at = at.replace(tzinfo=timezone.utc)
            return (now - at).total_seconds() > retry_hours * 3600

        def _needs_summary(a: Article) -> bool:
            return not a.summary or summary_needs_rewrite(a)

        def _eligible(a: Article) -> bool:
            # A trailing cutoff marker is a known hard-cut bug and should be
            # repaired immediately on the next export instead of waiting for
            # the normal negative-cache window.
            return summary_needs_rewrite(a) or _retryable(a)

        pending = [a for a in articles if _needs_summary(a) and _eligible(a)]
        if not pending:
            return articles
        rewrite_pending = {a.url for a in pending if summary_needs_rewrite(a)}

        page_fetch_pending = [a for a in pending if a.access_level is None]

        llm_summaries: dict[str, str] = {}
        meta_summaries: dict[str, str] = {}
        contents: dict[str, str] = {}

        empty_urls: list[str] = []
        if mode == "hybrid":
            contents, empty_urls = fetch_article_contents(page_fetch_pending)
        if mode in ("llm", "hybrid"):
            llm_summaries.update(summarize_with_deepseek(pending, contents=contents))

        llm_summaries = _clean_summary_map(llm_summaries)

        remaining = [
            a for a in page_fetch_pending if a.url not in llm_summaries
        ]
        if remaining and (
            mode == "meta" or (mode == "hybrid" and not deepseek_available())
        ):
            meta_summaries.update(fetch_meta_descriptions(remaining))
        meta_summaries = _clean_summary_map(meta_summaries)

        fallback_updates: dict[str, dict[str, str]] = {}

        for article in articles:
            if article not in pending:
                continue
            if article.url in llm_summaries:
                article.summary = llm_summaries[article.url]
                article.summary_source = "llm"
                article.summary_attempted_at = now
            elif article.url in meta_summaries:
                article.summary = meta_summaries[article.url]
                article.summary_source = "meta"
                article.summary_attempted_at = now
            elif article.url in rewrite_pending:
                fallback = clean_summary_text(article.summary)
                fallback_source = article.summary_source or "fallback"
                if fallback is None and article.title:
                    fallback = f"标题指出：{article.title.strip().rstrip('。！？!?…')}。"
                    fallback_source = "fallback"
                if fallback:
                    article.summary = fallback
                    article.summary_source = fallback_source
                    article.summary_attempted_at = now
                    fallback_updates.setdefault(fallback_source, {})[
                        article.url
                    ] = fallback

        if db is not None:
            try:
                if llm_summaries:
                    db.update_article_summaries(
                        llm_summaries, source="llm", attempted_at=now
                    )
                if meta_summaries:
                    db.update_article_summaries(
                        meta_summaries, source="meta", attempted_at=now
                    )
                for source, summaries in fallback_updates.items():
                    db.update_article_summaries(
                        summaries, source=source, attempted_at=now
                    )
                if mode == "hybrid":
                    failed = [
                        url
                        for url in empty_urls
                        if url not in llm_summaries and url not in meta_summaries
                    ]
                elif mode == "meta":
                    failed = [
                        a.url
                        for a in page_fetch_pending
                        if a.url not in meta_summaries
                    ]
                else:
                    failed = []
                if failed:
                    db.mark_summary_attempted(failed, attempted_at=now)
            except Exception as exc:
                logger.warning("Failed to persist summaries: %s", exc)
    except Exception as exc:
        logger.warning("Summary enrichment failed: %s", exc)
    return articles


def _clean_summary_map(summaries: dict[str, str]) -> dict[str, str]:
    """Normalize provider output before it can be assigned or persisted."""
    cleaned: dict[str, str] = {}
    for url, summary in summaries.items():
        value = clean_summary_text(summary)
        if value:
            cleaned[url] = value
    return cleaned
