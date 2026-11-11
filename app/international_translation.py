"""Pluggable, title/teaser-only translation for international delivery."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any, Callable, Literal, Protocol, runtime_checkable

from .models import Article


@runtime_checkable
class InternationalNewsTranslator(Protocol):
    def translate(
        self, title: str, summary: str | None, *, source_name: str
    ) -> tuple[str, str]:
        """Return a Chinese title and summary based only on supplied metadata."""


@dataclass(frozen=True, slots=True)
class TranslationResult:
    cn_title: str
    cn_summary: str
    status: Literal["translated", "fallback"]
    limitation: str | None
    body_fetch_count: int


class FakeTranslator(InternationalNewsTranslator):
    """Deterministic **test-only** translator.

    Production callers must inject a real translator (for example via
    :class:`SummarizerTranslator`) or leave it unset and receive the explicit
    English-metadata fallback.  Keeping this fixture here avoids making tests
    depend on a network/model provider while making accidental production use
    visible in code review.
    """

    def __init__(
        self,
        *,
        raise_error: bool = False,
        return_empty: bool = False,
        empty_result: bool | None = None,
    ) -> None:
        self.raise_error = raise_error
        self.return_empty = return_empty if empty_result is None else empty_result

    def translate(
        self, title: str, summary: str | None, *, source_name: str
    ) -> tuple[str, str]:
        if self.raise_error:
            raise RuntimeError("fake translator failure")
        if self.return_empty:
            return "", ""
        fact = (summary or title or "").strip()
        cn_title = _translate_common_terms(title.strip()) or title.strip()
        cn_fact = _translate_common_terms(fact) or fact
        judgment = (
            f"系统判断：本摘要依据{source_name or '该媒体'}提供的公开标题与合法导语，"
            "未读取文章正文；具体影响仍以原文为准。"
        )
        cn_summary = f"媒体报道事实：{cn_fact}。{judgment}"
        # Keep the deterministic fixture output within the delivery contract.
        # Padding is a limitation disclosure, not an invented event detail;
        # truncation prevents a long legal teaser from overrunning Word/card
        # layout while preserving the fact/judgment boundary.
        if len(cn_summary) < 100:
            cn_summary += "本结果仅作公开信息整理，未对文章正文或未提供的事实作推断。"
        if len(cn_summary) > 250:
            # Retain the explicit judgment suffix even when a teaser is long.
            fact_budget = max(20, 250 - len(judgment) - len("媒体报道事实：。"))
            cn_summary = (
                f"媒体报道事实：{cn_fact[:fact_budget].rstrip()}…。{judgment}"
            )
        return cn_title, cn_summary


class SummarizerTranslator(InternationalNewsTranslator):
    """Adapter for an existing summarizer-like callable.

    The callable receives ``title, summary, source_name`` and returns a
    ``(Chinese title, Chinese summary)`` tuple.  Keeping this small adapter
    means the production pipeline can reuse the existing summarizer/LLM
    abstraction without importing a provider into the international layer.
    """

    def __init__(self, summarizer: Callable[..., tuple[str, str]] | Any) -> None:
        self._summarizer = summarizer

    def translate(
        self, title: str, summary: str | None, *, source_name: str
    ) -> tuple[str, str]:
        # The existing summarizer layer is intentionally adapted at this
        # metadata boundary.  It may be supplied as a callable or as a small
        # object exposing ``translate``/``summarize``.  No Article/URL is
        # passed, so the adapter cannot fetch a page body by accident.
        target = self._summarizer
        if callable(target):
            result = target(title, summary, source_name=source_name)
        else:
            method = getattr(target, "translate", None) or getattr(target, "summarize", None)
            if not callable(method):
                raise TypeError("summarizer must be callable or expose translate/summarize")
            result = method(title, summary, source_name=source_name)
        if not isinstance(result, tuple) or len(result) != 2:
            raise ValueError("summarizer must return (Chinese title, Chinese summary)")
        return result


def _translate_common_terms(text: str) -> str:
    """Small deterministic vocabulary for offline fixtures, not a fact source."""

    result = text
    replacements = (
        ("United States", "美国"),
        ("US", "美国"),
        ("Washington", "华盛顿"),
        ("Taiwan", "台湾"),
        ("China", "中国"),
        ("Chinese", "中国"),
        ("arms sales package", "军售方案"),
        ("arms sale", "军售"),
        ("export controls", "出口管制"),
        ("semiconductor", "半导体"),
        ("military drills", "军事演习"),
        ("military exercises", "军事演习"),
        ("approves", "批准"),
        ("approved", "批准"),
        ("announced", "宣布"),
        ("announces", "宣布"),
    )
    for source, target in replacements:
        # Word-boundary replacement avoids corrupting unrelated words such as
        # ``business`` when translating the standalone token ``US``.
        result = re.sub(
            rf"(?<![A-Za-z]){re.escape(source)}(?![A-Za-z])",
            target,
            result,
            flags=re.IGNORECASE,
        )
    return result.strip()


def translate_article(
    article: Article,
    translator: InternationalNewsTranslator | None = None,
    body_fetcher: Callable[[str], str] | None = None,
) -> TranslationResult:
    """Translate an Article without ever fetching its body.

    ``body_fetcher`` remains in the signature for compatibility with callers
    that used an earlier experimental hook.  It is intentionally ignored for
    every access level, including ``public``: the legal boundary for this
    delivery component is the Article title and already-collected teaser.
    """

    del body_fetcher
    title = str(article.title or "").strip()
    summary = (
        article.summary.strip()
        if isinstance(article.summary, str)
        and article.summary.strip()
        and getattr(article, "summary_source", None) in {None, "rss", "meta"}
        else None
    )
    if translator is None:
        # A missing provider is a deliberate, legal fallback—not a reason to
        # invent Chinese text from a dictionary or fetch a restricted body.
        limitation = (
            "未配置翻译器；仅保留英文标题和已取得的合法导语，未抓取文章正文。"
        )
        return TranslationResult(
            cn_title=title,
            cn_summary=summary or "",
            status="fallback",
            limitation=limitation,
            body_fetch_count=0,
        )
    try:
        cn_title, cn_summary = translator.translate(
            title, summary, source_name=str(article.source_name or "")
        )
        if not isinstance(cn_title, str) or not isinstance(cn_summary, str):
            raise ValueError("translator returned non-string output")
        cn_title, cn_summary = cn_title.strip(), cn_summary.strip()
        if not cn_title or not cn_summary:
            raise ValueError("translator returned empty output")
    except Exception:
        limitation = "翻译失败；仅保留英文标题和已取得的合法导语，未抓取文章正文。"
        return TranslationResult(
            cn_title=title,
            cn_summary=summary or limitation,
            status="fallback",
            limitation=limitation,
            body_fetch_count=0,
        )
    return TranslationResult(
        cn_title=cn_title,
        cn_summary=cn_summary,
        status="translated",
        limitation=None,
        body_fetch_count=0,
    )


__all__ = [
    "FakeTranslator",
    "InternationalNewsTranslator",
    "SummarizerTranslator",
    "TranslationResult",
    "translate_article",
]
