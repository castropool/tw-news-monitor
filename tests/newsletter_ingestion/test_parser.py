from datetime import datetime
from email.message import EmailMessage

from app.newsletter_ingestion.models import NewsletterMessage
from app.newsletter_ingestion.parser import parse_message
import app.newsletter_ingestion.parser as parser_module
from app.time_utils import TAIPEI


def test_parse_eml_returns_two_articles_and_removes_tracking_parameters():
    eml = b"""From: news@wsj.com\nDate: Fri, 14 Aug 2026 09:30:00 +0800\nSubject: Brief\nMIME-Version: 1.0\nContent-Type: text/html; charset=\"utf-8\"\n\n<html><body><h2><a href=\"https://www.wsj.com/a?utm_source=x\">One</a></h2><p>Summary</p><h2><a href=\"https://www.wsj.com/b?fbclid=y\">Two</a></h2></body></html>"""
    items = parse_message(eml)
    assert len(items) == 2
    assert all("utm_" not in item.url and "fbclid" not in item.url for item in items)
    assert items[0].published_at == datetime(2026, 8, 14, 9, 30, tzinfo=TAIPEI)


def test_parse_message_supports_html_plain_and_received_date():
    received = datetime(2026, 8, 14, 10, 0, tzinfo=TAIPEI)
    html = NewsletterMessage("m", "news@ft.com", received, "Brief", '<h2><a href="https://www.ft.com/a">A</a></h2><p>S</p>', None, "InternationalNews")
    assert parse_message(html)[0].summary == "S"
    text = NewsletterMessage("m2", "news@ft.com", received, "Brief", None, "Title\nhttps://www.ft.com/a\nSummary", "InternationalNews")
    assert parse_message(text)[0].published_at == received


def test_parse_multipart_prefers_html_and_ignores_attachment_and_nested_forward():
    msg = EmailMessage()
    msg["From"] = "news@bloomberg.com"
    msg["Date"] = "Fri, 14 Aug 2026 09:30:00 +0800"
    msg["Subject"] = "Brief"
    msg.set_content("Plain title\nhttps://www.bloomberg.com/plain")
    msg.add_alternative('<h2><a href="https://www.bloomberg.com/html">HTML title</a></h2><p>Teaser</p>', subtype="html")
    msg.add_attachment(b"<h2><a href='https://evil.example/attachment'>No</a></h2>", maintype="text", subtype="html", filename="x.html")
    items = parse_message(msg.as_bytes())
    assert [item.title for item in items] == ["HTML title"]


def test_parse_bad_charset_degrades_without_exception():
    eml = b"From: news@reuters.com\nContent-Type: text/html; charset=x-not-a-real-charset\n\n<h2><a href='https://www.reuters.com/a'>A</a></h2>"
    assert parse_message(eml)[0].title == "A"


def test_parse_html_deduplicates_url_and_title_fingerprint():
    html = '<h2><a href="https://www.wsj.com/a">Same title</a></h2><h2><a href="https://www.wsj.com/b">Same title!</a></h2>'
    items = parse_message(NewsletterMessage("m", "news@wsj.com", None, "", html, None, "InternationalNews"))
    assert len(items) == 2  # URL dedup is parser-local; cross-email fingerprint is collector-owned.


def test_parse_limits_reject_oversized_and_too_many_items():
    html = "".join(f'<h2><a href="https://www.ft.com/{i}">Title {i}</a></h2>' for i in range(6))
    message = NewsletterMessage("m", "news@ft.com", None, "", html, None, "InternationalNews")
    assert parse_message(message, max_items=3)[-1].title == "Title 2"
    huge = NewsletterMessage("h", "news@ft.com", None, "", "x" * 20, None, "InternationalNews")
    assert parse_message(huge, max_message_bytes=10) == []


def test_raw_eml_size_limit_rejects_before_mime_parse(monkeypatch):
    raw = b"From: news@wsj.com\r\nContent-Type: text/plain\r\n\r\n" + (b"x" * 200)

    def must_not_parse(*args, **kwargs):
        raise AssertionError("MIME parser must not run for oversized raw EML")

    monkeypatch.setattr(parser_module.email, "message_from_bytes", must_not_parse)
    assert parse_message(raw, max_message_bytes=64) == []
