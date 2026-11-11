from datetime import datetime

from app.newsletter_ingestion.models import NewsletterItem, NewsletterMessage
from app.newsletter_ingestion.policy import SourcePolicy


def test_policy_accepts_label_and_sender_allowlist_only():
    decision = SourcePolicy(
        label="InternationalNews", allowed_domains={"reuters.com"}
    ).check("InternationalNews", "news@reuters.com")
    assert decision.accepted is True


def test_policy_rejects_wrong_label_and_sender_without_leaking_body():
    policy = SourcePolicy(label="InternationalNews", allowed_domains={"reuters.com"})
    assert policy.check("Inbox", "news@reuters.com").reason == "LABEL_NOT_ALLOWED"
    assert policy.check("InternationalNews", "spam@evil.example").reason == "SENDER_NOT_ALLOWED"


def test_policy_accepts_subdomain_and_case_insensitively():
    policy = SourcePolicy(label="InternationalNews", allowed_domains={"bloomberg.com"})
    assert policy.check("InternationalNews", "Newsletter@Mail.Bloomberg.com").accepted


def test_message_and_item_are_stable_slots_models():
    received = datetime(2026, 8, 14, 9, 0)
    message = NewsletterMessage(
        message_id="m1", sender="news@reuters.com", received_at=received,
        subject="Daily", html="<p>x</p>", text=None, label="InternationalNews"
    )
    item = NewsletterItem(
        item_id="i1", source_id="reuters_newsletter", title="Title",
        url="https://www.reuters.com/a", summary=None, published_at=None
    )
    assert message.message_id == "m1"
    assert item.source_id == "reuters_newsletter"


def test_policy_limits_are_explicit_and_safe_defaults():
    policy = SourcePolicy(label="InternationalNews", allowed_domains={"ft.com"})
    assert policy.max_message_bytes > 0
    assert policy.max_parts > 0
    assert policy.max_items > 0

