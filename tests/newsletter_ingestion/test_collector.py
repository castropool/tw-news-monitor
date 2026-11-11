from datetime import datetime

from app.models import Article
from app.newsletter_ingestion.collector import NewsletterCollector
from app.newsletter_ingestion.models import NewsletterMessage
from app.newsletter_ingestion.policy import SourcePolicy
from app.time_utils import TAIPEI


SOURCE = {
    "id": "wsj_newsletter", "name": "Wall Street Journal",
    "category": "international", "language": "en", "section": "China Newsletter",
}


def _message(message_id="m1", title="Taiwan Strait Briefing", url="https://www.wsj.com/a"):
    return NewsletterMessage(
        message_id=message_id, sender="news@wsj.com",
        received_at=datetime(2026, 8, 14, 19, 30, tzinfo=TAIPEI), subject="Brief",
        html=f'<h2><a href="{url}">{title}</a></h2><p>Teaser</p>', text=None,
        label="InternationalNews",
    )


def test_newsletter_collector_sets_existing_article_fields():
    articles = NewsletterCollector(
        SOURCE, policy=SourcePolicy("InternationalNews", {"wsj.com"})
    ).collect(_message())
    assert len(articles) == 1
    assert isinstance(articles[0], Article)
    assert articles[0].access_level == "newsletter"
    assert articles[0].language == "en"
    assert articles[0].source_id == "wsj_newsletter"
    assert articles[0].summary_source == "newsletter"
    assert articles[0].category == "international"


def test_collector_rejects_sender_and_article_host_policy():
    message = NewsletterMessage("m", "spam@evil.example", None, "", '<a href="https://evil.example/a">x</a>', None, "InternationalNews")
    collector = NewsletterCollector(SOURCE, policy=SourcePolicy("InternationalNews", {"wsj.com"}, allowed_article_domains={"wsj.com"}))
    assert collector.collect(message) == []


def test_collector_deduplicates_across_messages_by_url_and_title_fingerprint():
    collector = NewsletterCollector(SOURCE, policy=SourcePolicy("InternationalNews", {"wsj.com"}))
    assert len(collector.collect(_message())) == 1
    assert collector.collect(_message("m2", "Different title", "https://www.wsj.com/a?utm_campaign=x")) == []
    assert collector.collect(_message("m3", "Taiwan Strait Briefing!", "https://www.wsj.com/b")) == []

