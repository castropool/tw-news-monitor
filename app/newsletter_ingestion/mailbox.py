"""Readonly mailbox protocol used by the Newsletter ingestion layer.

The parser and collectors depend on this tiny interface instead of a Gmail
object.  A mailbox implementation must return normalized messages and must
not expose credentials, cookies, or provider-specific service objects.
"""

from __future__ import annotations

from typing import Protocol

from .models import NewsletterMessage


class MailboxClient(Protocol):
    """Provider-neutral, read-only mailbox contract."""

    def list_messages(
        self,
        label: str,
        sender_allowlist: set[str],
        since: str,
    ) -> list[NewsletterMessage]:
        """Return messages matching the exact label, sender policy and window."""

