from datetime import datetime, timezone

from app.collectors.bloomberg_newsletter import BloombergNewsletterCollector
from app.newsletter_ingestion.models import NewsletterMessage


SOURCE = {
    "id": "bloomberg_newsletter", "name": "Bloomberg",
    "type": "bloomberg_newsletter", "category": "international",
    "language": "en", "access_level": "newsletter",
    "url": "https://www.bloomberg.com/account/newsletters",
    "mailbox_label": "InternationalNews", "sender_allowlist": ["bloomberg.com"],
    "article_allowlist": ["bloomberg.com"],
}


class FakeMailbox:
    def list_messages(self, label, sender_allowlist, since):
        return [NewsletterMessage(
            "b1", "brief@bloomberg.com", datetime(2026, 8, 15, tzinfo=timezone.utc),
            "Bloomberg Technology", None,
            '<h2><a href="https://www.bloomberg.com/news/articles/chip?utm_medium=email">Chip controls</a></h2>',
            label,
        )]


def test_bloomberg_collector_reads_only_mailbox_and_emits_newsletter_article():
    articles = BloombergNewsletterCollector(SOURCE, mailbox=FakeMailbox()).collect()
    assert len(articles) == 1
    assert articles[0].source_id == "bloomberg_newsletter"
    assert articles[0].access_level == "newsletter"
    assert articles[0].url == "https://www.bloomberg.com/news/articles/chip"
