from datetime import datetime, timezone

from app.collectors.wsj_newsletter import WSJNewsletterCollector
from app.newsletter_ingestion.models import NewsletterMessage


SOURCE = {
    "id": "wsj_newsletter",
    "name": "Wall Street Journal",
    "type": "wsj_newsletter",
    "category": "international",
    "language": "en",
    "access_level": "newsletter",
    "url": "https://www.wsj.com/newsletters",
    "mailbox_label": "InternationalNews",
    "sender_allowlist": ["wsj.com"],
    "article_allowlist": ["wsj.com"],
}


def _message(sender="brief@wsj.com", title="Taiwan Strait update", url="https://www.wsj.com/articles/a"):
    return NewsletterMessage(
        message_id="m1", sender=sender,
        received_at=datetime(2026, 8, 15, 1, 0, tzinfo=timezone.utc),
        subject="What's News",
        html=f'<h2><a href="{url}?utm_source=newsletter">{title}</a></h2><p>Teaser</p>',
        text=None, label="InternationalNews",
    )


class FakeMailbox:
    def __init__(self, messages):
        self.messages = messages
        self.calls = []

    def list_messages(self, label, sender_allowlist, since):
        self.calls.append((label, sender_allowlist, since))
        return self.messages


def test_wsj_collector_reads_mailbox_and_uses_newsletter_adapter_only():
    mailbox = FakeMailbox([_message()])
    articles = WSJNewsletterCollector(SOURCE, mailbox=mailbox).collect()
    assert len(articles) == 1
    assert articles[0].source_id == "wsj_newsletter"
    assert articles[0].access_level == "newsletter"
    assert articles[0].url == "https://www.wsj.com/articles/a"
    assert mailbox.calls[0][0] == "InternationalNews"


def test_wsj_collector_rejects_unallowlisted_sender_without_http():
    mailbox = FakeMailbox([_message(sender="spam@example.com")])
    collector = WSJNewsletterCollector(SOURCE, mailbox=mailbox)
    assert collector.collect() == []
    assert collector.http_calls == 0


def test_wsj_collector_deduplicates_messages():
    first = _message()
    second = _message()
    second = NewsletterMessage(**{**second.__dict__}) if hasattr(second, "__dict__") else NewsletterMessage(
        message_id="m2", sender=second.sender, received_at=second.received_at,
        subject=second.subject, html=second.html, text=second.text, label=second.label,
    )
    assert len(WSJNewsletterCollector(SOURCE, mailbox=FakeMailbox([first, second])).collect()) == 1
