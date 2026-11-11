import inspect
from datetime import datetime, timezone

from app.international_translation import (
    FakeTranslator,
    InternationalNewsTranslator,
    SummarizerTranslator,
    TranslationResult,
    translate_article,
)
from app.models import Article


ARTICLE = Article(
    source_id="reuters_international",
    source_name="Reuters",
    category="international",
    title="US approves new arms sales package for Taiwan",
    url="https://example.test/article/1",
    published_at=datetime(2026, 8, 15, 1, tzinfo=timezone.utc),
    fetched_at=datetime(2026, 8, 15, 1, tzinfo=timezone.utc),
    position=1,
    summary="The administration announced the package on Friday and said it supports Taiwan's defensive capabilities.",
    access_level="metadata_only",
)


def test_protocol_signature_and_metadata_only_translation_never_fetches_body():
    signature = inspect.signature(FakeTranslator.translate)
    assert list(signature.parameters) == ["self", "title", "summary", "source_name"]
    assert signature.parameters["source_name"].kind is inspect.Parameter.KEYWORD_ONLY
    result = translate_article(ARTICLE, translator=FakeTranslator(), body_fetcher=lambda _url: (_ for _ in ()).throw(AssertionError("body fetch")))
    assert isinstance(FakeTranslator(), InternationalNewsTranslator)
    assert isinstance(result, TranslationResult)
    assert result.status == "translated"
    assert result.body_fetch_count == 0
    assert "媒体报道事实" in result.cn_summary
    assert "系统判断" in result.cn_summary


def test_translator_error_uses_english_metadata_fallback():
    result = translate_article(ARTICLE, translator=FakeTranslator(raise_error=True))
    assert result.status == "fallback"
    assert result.cn_title == ARTICLE.title
    assert result.cn_summary == ARTICLE.summary
    assert result.limitation
    assert result.body_fetch_count == 0


def test_empty_translation_is_not_fabricated():
    result = translate_article(ARTICLE, translator=FakeTranslator(return_empty=True))
    assert result.status == "fallback"
    assert result.cn_title == ARTICLE.title
    assert result.cn_summary == ARTICLE.summary
    assert result.body_fetch_count == 0


def test_missing_translator_is_strict_english_metadata_fallback():
    result = translate_article(ARTICLE, translator=None)
    assert result.status == "fallback"
    assert result.cn_title == ARTICLE.title
    assert result.cn_summary == ARTICLE.summary
    assert result.limitation
    assert "媒体报道事实" not in result.cn_title + result.cn_summary
    assert result.body_fetch_count == 0


def test_llm_summary_is_not_passed_as_english_metadata():
    article = Article(
        source_id="reuters_international",
        source_name="Reuters",
        category="international",
        title="Taiwan military drills begin",
        url="https://example.test/llm-summary",
        published_at=ARTICLE.published_at,
        fetched_at=ARTICLE.fetched_at,
        position=ARTICLE.position,
        summary="这是中文模型梗概。",
        summary_source="llm",
    )
    received: list[tuple[str, str | None, str]] = []

    class CapturingTranslator:
        def translate(self, title, summary, *, source_name):
            received.append((title, summary, source_name))
            return "台湾军演启动", "台湾启动军事演习。"

    result = translate_article(article, translator=CapturingTranslator())

    assert result.status == "translated"
    assert received == [(article.title, None, "Reuters")]


def test_summarizer_adapter_receives_only_metadata_and_no_body_fetch():
    calls = []

    def summarizer(title, summary, *, source_name):
        calls.append((title, summary, source_name))
        return "中文标题", "中文摘要"

    result = translate_article(
        ARTICLE,
        translator=SummarizerTranslator(summarizer),
        body_fetcher=lambda _url: (_ for _ in ()).throw(AssertionError("body fetch")),
    )
    assert result.status == "translated"
    assert result.cn_title == "中文标题"
    assert calls == [(ARTICLE.title, ARTICLE.summary, ARTICLE.source_name)]
    assert result.body_fetch_count == 0
