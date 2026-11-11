"""Newsletter parsing layer tests (app/newsletter.py) — local fixtures only.

Covers: one email / multiple articles, tracking URL cleanup via the existing
normalize_url, duplicate URL dedup (first wins), missing publish time
(published_at=None, never an error), and the WSJ/Bloomberg placeholder
adapters proving the chain.
"""

from datetime import datetime
from pathlib import Path

import pytest

from app.newsletter import (
    BloombergNewsletterAdapter,
    NewsletterItem,
    WSJNewsletterAdapter,
    parse_newsletter,
)
from app.models import Article
from app.time_utils import TAIPEI

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "international"

MULTI_ARTICLE_HTML = """\
<html><body>
<h2><a href="https://www.wsj.com/articles/a1?utm_source=newsletter&utm_campaign=test">Article One</a></h2>
<p>First summary text.</p>
<h3><a href="https://www.wsj.com/articles/a2?utm_source=newsletter">Article Two</a></h3>
<p>Second summary.</p>
<ul><li><a href="https://www.bloomberg.com/news/newsletters/2026-08-13/third">Article Three</a> — Third summary.</li></ul>
</body></html>
"""


def _eml(*header_lines: str, body: str, content_type: str = "text/html; charset=\"utf-8\"") -> bytes:
    lines = [
        "From: test@example.com",
        "To: reader@example.com",
        "Subject: Daily Briefing",
        "MIME-Version: 1.0",
        f"Content-Type: {content_type}",
        *header_lines,
        "",
        body,
    ]
    return "\r\n".join(lines).encode("utf-8")


# ── html format ─────────────────────────────────────────────────────

def test_parse_html_multiple_articles():
    items = parse_newsletter(MULTI_ARTICLE_HTML, "html")
    assert len(items) == 3

    a1, a2, a3 = items
    assert isinstance(a1, NewsletterItem)
    assert a1.title == "Article One"
    assert a1.url == "https://www.wsj.com/articles/a1"  # utm params stripped
    assert a1.summary == "First summary text."
    assert a1.published_at is None  # raw HTML has no publish time

    assert a2.title == "Article Two"
    assert a2.url == "https://www.wsj.com/articles/a2"
    assert a2.summary == "Second summary."

    assert a3.title == "Article Three"
    assert a3.url == "https://www.bloomberg.com/news/newsletters/2026-08-13/third"
    assert a3.summary == "Third summary."


def test_parse_html_accepts_bytes():
    items = parse_newsletter(MULTI_ARTICLE_HTML.encode("utf-8"), "html")
    assert len(items) == 3


def test_parse_html_tracking_url_cleaned():
    html = (
        '<h2><a href="https://www.wsj.com/articles/track?utm_source=newsletter'
        '&utm_campaign=weekly&fbclid=abc&utm_content=1#frag">Tracked</a></h2>'
    )
    items = parse_newsletter(html, "html")
    assert len(items) == 1
    assert items[0].url == "https://www.wsj.com/articles/track"


def test_parse_html_duplicate_url_keeps_first():
    html = (
        "<html><body>"
        '<h2><a href="https://www.wsj.com/articles/dup">First title</a></h2>'
        "<p>first summary</p>"
        '<h3><a href="https://www.wsj.com/articles/dup?utm_source=newsletter">Second title</a></h3>'
        "<p>second summary</p>"
        "</body></html>"
    )
    items = parse_newsletter(html, "html")
    assert len(items) == 1
    assert items[0].title == "First title"
    assert items[0].summary == "first summary"


def test_parse_html_empty_input_returns_empty():
    assert parse_newsletter("", "html") == []
    assert parse_newsletter("<html><body></body></html>", "html") == []


# ── eml format ──────────────────────────────────────────────────────

def test_parse_eml_with_date_header():
    body = (
        "<html><body>"
        '<h2><a href="https://www.example.com/a1">Alpha article</a></h2>'
        "<p>alpha summary</p>"
        '<h3><a href="https://www.example.com/a2">Beta article</a></h3>'
        "<p>beta summary</p>"
        "</body></html>"
    )
    eml = _eml("Date: Thu, 13 Aug 2026 09:30:00 +0800", body=body)
    items = parse_newsletter(eml, "eml")
    assert len(items) == 2
    assert items[0].title == "Alpha article"
    assert items[0].summary == "alpha summary"
    # +0800 == Asia/Taipei: Date header time is kept verbatim.
    assert items[0].published_at == datetime(2026, 8, 13, 9, 30, tzinfo=TAIPEI)
    assert items[1].published_at == datetime(2026, 8, 13, 9, 30, tzinfo=TAIPEI)


def test_parse_eml_without_date_header_yields_none():
    eml = _eml(body="<html><body><h2><a href=\"https://www.example.com/a1\">A</a></h2></body></html>")
    items = parse_newsletter(eml, "eml")
    assert len(items) == 1
    assert items[0].published_at is None


def test_parse_eml_prefers_html_part_over_plain_text():
    eml = (
        "From: test@example.com\r\n"
        "To: reader@example.com\r\n"
        "Subject: Multi\r\n"
        "Date: Thu, 13 Aug 2026 09:30:00 +0800\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/alternative; boundary="B0"\r\n'
        "\r\n"
        "--B0\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "\r\n"
        "Alpha article\r\n"
        "https://www.example.com/a1\r\n"
        "plain-text summary line.\r\n"
        "--B0\r\n"
        'Content-Type: text/html; charset="utf-8"\r\n'
        "\r\n"
        "<html><body><h2><a href=\"https://www.example.com/a1\">Alpha article</a></h2>"
        "<p>html summary.</p></body></html>\r\n"
        "--B0--\r\n"
    ).encode("utf-8")
    items = parse_newsletter(eml, "eml")
    assert len(items) == 1
    assert items[0].title == "Alpha article"
    assert items[0].summary == "html summary."  # html part wins
    assert items[0].published_at == datetime(2026, 8, 13, 9, 30, tzinfo=TAIPEI)


def test_parse_eml_plain_text_only_part():
    eml = (
        "From: test@example.com\r\n"
        "To: reader@example.com\r\n"
        "Subject: Text only\r\n"
        "Date: Thu, 13 Aug 2026 09:30:00 +0800\r\n"
        "MIME-Version: 1.0\r\n"
        'Content-Type: multipart/alternative; boundary="B0"\r\n'
        "\r\n"
        "--B0\r\n"
        'Content-Type: text/plain; charset="utf-8"\r\n'
        "\r\n"
        "Alpha article\r\n"
        "https://www.example.com/a1\r\n"
        "plain-text summary line.\r\n"
        "--B0--\r\n"
    ).encode("utf-8")
    items = parse_newsletter(eml, "eml")
    assert len(items) == 1
    assert items[0].title == "Alpha article"
    assert items[0].url == "https://www.example.com/a1"
    assert items[0].summary == "plain-text summary line."
    assert items[0].published_at == datetime(2026, 8, 13, 9, 30, tzinfo=TAIPEI)


def test_parse_eml_accepts_string_input():
    eml_text = _eml(body="<h2><a href=\"https://www.example.com/a1\">A</a></h2>").decode("utf-8")
    items = parse_newsletter(eml_text, "eml")
    assert len(items) == 1


# ── text format ─────────────────────────────────────────────────────

TEXT_BLOCKS = """\
Alpha article
https://www.example.com/a1
Summary line one.

Beta article with tracking
https://www.example.com/a2?utm_source=newsletter&utm_campaign=weekly
Second summary line.
"""


def test_parse_text_blocks():
    items = parse_newsletter(TEXT_BLOCKS, "text")
    assert len(items) == 2
    a1, a2 = items
    assert a1.title == "Alpha article"
    assert a1.url == "https://www.example.com/a1"
    assert a1.summary == "Summary line one."
    assert a1.published_at is None
    assert a2.title == "Beta article with tracking"
    assert a2.url == "https://www.example.com/a2"  # utm params stripped
    assert a2.summary == "Second summary line."


def test_parse_text_ignores_blocks_without_url():
    text = "Just a note.\nNo links here.\n"
    assert parse_newsletter(text, "text") == []
    assert parse_newsletter("", "text") == []


def test_parse_text_duplicate_url_keeps_first():
    text = (
        "First title\n"
        "https://www.example.com/dup\n"
        "first summary.\n"
        "\n"
        "Second title\n"
        "https://www.example.com/dup\n"
        "second summary.\n"
    )
    items = parse_newsletter(text, "text")
    assert len(items) == 1
    assert items[0].title == "First title"
    assert items[0].summary == "first summary."


# ── format validation ───────────────────────────────────────────────

def test_parse_newsletter_invalid_format_raises():
    with pytest.raises(ValueError):
        parse_newsletter("x", "pdf")


# ── placeholder adapters ────────────────────────────────────────────

def test_wsj_newsletter_adapter_parses():
    adapter = WSJNewsletterAdapter({
        "id": "wsj_newsletter",
        "name": "Wall Street Journal",
        "category": "international",
        "section": "China Newsletter",
        "language": "en",
    })
    items = adapter.parse(
        (FIXTURE_DIR / "wsj_china_newsletter.eml").read_bytes(), fmt="eml"
    )
    assert len(items) == 2
    assert isinstance(items[0], Article)
    assert items[0].title == "Taiwan Strait Briefing"
    assert items[0].source_name == "Wall Street Journal"
    assert items[0].section == "China Newsletter"
    assert items[0].access_level == "newsletter"
    assert items[0].summary_source == "newsletter"
    assert items[0].published_at == datetime(2026, 8, 14, 19, 30, tzinfo=TAIPEI)
    text_items = adapter.parse(TEXT_BLOCKS, fmt="text")
    assert len(text_items) == 2


def test_bloomberg_newsletter_adapter_parses():
    adapter = BloombergNewsletterAdapter({"id": "bbg_newsletter", "name": "Bloomberg"})
    items = adapter.parse(
        (FIXTURE_DIR / "wsj_bloomberg_newsletter.html").read_text(encoding="utf-8"),
        fmt="html",
    )
    assert len(items) == 2  # duplicate WSJ tracking URL is removed
    assert items[1].url == "https://www.bloomberg.com/news/articles/2026-08-14/tsmc-chip-outlook"
    assert all(isinstance(item, Article) for item in items)
    assert all(item.access_level == "newsletter" for item in items)
