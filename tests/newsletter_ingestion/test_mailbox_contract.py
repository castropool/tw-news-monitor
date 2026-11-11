from datetime import datetime

from app.newsletter_ingestion.mailbox import MailboxClient
from app.newsletter_ingestion.models import NewsletterMessage


def test_mailbox_protocol_has_provider_neutral_readonly_method():
    assert hasattr(MailboxClient, "list_messages")
    message = NewsletterMessage("id", "news@reuters.com", datetime.now(), "subject", "<p>x</p>", None, "InternationalNews")
    assert message.label == "InternationalNews"

