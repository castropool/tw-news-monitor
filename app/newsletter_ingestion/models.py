"""Newsletter ingestion boundary models.

These models deliberately contain no credentials, tokens, cookies, HTTP clients,
or mailbox service objects.  They are small value objects suitable for fixture
tests and for handoff between a readonly mailbox client and the parser.
"""

from dataclasses import dataclass
from datetime import datetime


@dataclass(slots=True, frozen=True)
class NewsletterMessage:
    message_id: str
    sender: str
    received_at: datetime | None
    subject: str
    html: str | None
    text: str | None
    label: str


@dataclass(slots=True, frozen=True)
class NewsletterItem:
    item_id: str = ""
    source_id: str = ""
    title: str = ""
    url: str = ""
    summary: str | None = None
    published_at: datetime | None = None

