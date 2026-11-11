"""Gmail API implementation of the provider-neutral readonly mailbox."""

from __future__ import annotations

import base64
import binascii
import email.utils
import logging
import re
from datetime import datetime, timedelta, timezone
from email.header import decode_header
from email.utils import parseaddr
from typing import Any

from ..time_utils import TAIPEI
from .mailbox import MailboxClient
from .models import NewsletterMessage
from .oauth import (
    AUTHORIZED_READONLY,
    GMAIL_READONLY_SCOPE,
    AuthContext,
    load_credentials,
)


DEFAULT_LABEL = "InternationalNews"
DEFAULT_MAX_MESSAGE_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_MESSAGES = 100
DEFAULT_USER_AGENT = "tw-news-monitor-newsletter/1.0 (readonly)"
_LOGGER = logging.getLogger(__name__)


class GmailMailboxClient:
    """Read Gmail messages without exposing mutation APIs.

    ``service`` is injectable so all tests can use a fake service.  In normal
    operation it is a Google discovery service created by ``build_service``.
    Passing ``modify=True`` is rejected rather than silently weakening the
    readonly contract.
    """

    readonly_scope = GMAIL_READONLY_SCOPE

    def __init__(
        self,
        service: object | None,
        label: str = DEFAULT_LABEL,
        modify: bool = False,
        auth: AuthContext | None = None,
        *,
        max_message_bytes: int = DEFAULT_MAX_MESSAGE_BYTES,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        user_id: str = "me",
    ) -> None:
        if modify:
            raise ValueError("GmailMailboxClient is readonly; modify must be False")
        if not label or label != DEFAULT_LABEL:
            # The exact label is a safety boundary, not a caller preference.
            raise ValueError(f"Gmail mailbox label must be exactly {DEFAULT_LABEL}")
        if max_message_bytes <= 0 or max_messages <= 0:
            raise ValueError("Gmail mailbox limits must be positive")
        self.service = service
        self.label = label
        self.modify = False
        self.auth = auth
        self.max_message_bytes = max_message_bytes
        self.max_messages = max_messages
        self.user_id = user_id

    def list_messages(
        self,
        label: str,
        sender_allowlist: set[str],
        since: str,
    ) -> list[NewsletterMessage]:
        if label != self.label or not _auth_ready(self.auth) or self.service is None:
            return []
        cutoff = _since_datetime(since)
        if cutoff is None:
            _LOGGER.warning("Gmail readonly mailbox read rejected: invalid_since")
            return []
        allowed = _normalize_allowlist(sender_allowlist)
        if not allowed:
            return []
        try:
            label_id = self._find_exact_label(label)
            if not label_id:
                return []
            query = _gmail_since_query(cutoff)
            refs = self._list_refs(label_id, query)
            messages: list[NewsletterMessage] = []
            for ref in refs[: self.max_messages]:
                message_id = str(ref.get("id", ""))
                if not message_id:
                    continue
                raw = _execute(
                    self.service.users().messages().get(
                        userId=self.user_id, id=message_id, format="full"
                    )
                )
                normalized = _normalize_gmail_message(
                    raw, self.label, self.max_message_bytes
                )
                if normalized is None:
                    continue
                if cutoff is not None and (
                    normalized.received_at is None
                    or normalized.received_at.astimezone(timezone.utc) < cutoff
                ):
                    continue
                if not _sender_allowed(normalized.sender, allowed):
                    continue
                messages.append(normalized)
                if len(messages) >= self.max_messages:
                    break
            return sorted(
                messages,
                key=lambda item: item.received_at or datetime.min.replace(tzinfo=TAIPEI),
                reverse=True,
            )
        except Exception:
            # Keep source failures observable without serializing provider
            # exceptions, which can contain tokens, message bodies, or IDs.
            _LOGGER.warning("Gmail readonly mailbox read failed: provider_error")
            # A source failure is isolated and does not expose provider data.
            return []

    def _find_exact_label(self, label: str) -> str | None:
        response = _execute(self.service.users().labels().list(userId=self.user_id))
        for item in response.get("labels", []) if isinstance(response, dict) else []:
            if item.get("name") == label:
                return str(item.get("id")) if item.get("id") else None
        return None

    def _list_refs(self, label_id: str, query: str | None) -> list[dict[str, Any]]:
        refs: list[dict[str, Any]] = []
        page_token: str | None = None
        while len(refs) < self.max_messages:
            kwargs: dict[str, Any] = {
                "userId": self.user_id,
                "labelIds": [label_id],
                "maxResults": min(100, self.max_messages - len(refs)),
            }
            if query:
                kwargs["q"] = query
            if page_token:
                kwargs["pageToken"] = page_token
            payload = _execute(self.service.users().messages().list(**kwargs))
            refs.extend(payload.get("messages", []) if isinstance(payload, dict) else [])
            page_token = payload.get("nextPageToken") if isinstance(payload, dict) else None
            if not page_token:
                break
        return refs[: self.max_messages]


def build_service(auth: AuthContext, *, user_agent: str = DEFAULT_USER_AGENT):
    """Build an official Gmail discovery service without starting OAuth."""

    credentials = load_credentials(auth)
    if credentials is None:
        return None
    try:
        from googleapiclient.discovery import build

        return build(
            "gmail", "v1", credentials=credentials, cache_discovery=False,
        )
    except Exception:
        return None


def _auth_ready(auth: AuthContext | None) -> bool:
    return bool(
        auth is not None
        and auth.authorized
        and auth.reason == AUTHORIZED_READONLY
        and auth.scope == GMAIL_READONLY_SCOPE
        and auth.scope_provenance == "authorized_user_file"
    )


def _execute(request):
    return request.execute() if hasattr(request, "execute") else request


def _normalize_allowlist(values: set[str] | list[str] | tuple[str, ...]) -> set[str]:
    return {str(value).strip().lower().lstrip("@").rstrip(".") for value in values if str(value).strip()}


def _sender_allowed(sender: str, allowlist: set[str]) -> bool:
    address = parseaddr(sender or "")[1].strip().lower()
    if "@" not in address:
        return False
    domain = address.rsplit("@", 1)[1].rstrip(".")
    for allowed in allowlist:
        if "@" in allowed and address == allowed:
            return True
        if domain == allowed or domain.endswith("." + allowed):
            return True
    return False


def _gmail_since_query(since: datetime) -> str:
    # Gmail's after: query is date-based and the local datetime filter below
    # supplies the precise boundary.
    return "after:" + since.astimezone(timezone.utc).strftime("%Y/%m/%d")


def _since_datetime(since: str | None) -> datetime | None:
    if not isinstance(since, str) or not since:
        return None
    text = since
    match = re.fullmatch(r"(?:0|[1-9][0-9]{0,3})d", text)
    if match:
        try:
            days = int(text[:-1])
            return datetime.now(timezone.utc) - timedelta(days=days)
        except (OverflowError, ValueError):
            return None
    if not re.fullmatch(
        r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:\d{2})",
        text,
    ):
        # RFC 2822 is accepted only when parsedate_to_datetime supplies an
        # explicit timezone; naive timestamps are ambiguous and rejected.
        try:
            value = email.utils.parsedate_to_datetime(text)
        except (TypeError, ValueError, OverflowError):
            return None
        if value is None or value.tzinfo is None:
            return None
    else:
        try:
            value = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if value.tzinfo is None:
        return None
    return value.astimezone(timezone.utc)


def validate_since(since: str | None) -> bool:
    """Return whether ``since`` uses the bounded, timezone-aware contract."""

    return _since_datetime(since) is not None


def _normalize_gmail_message(
    payload: dict[str, Any], label: str, max_bytes: int
) -> NewsletterMessage | None:
    if not isinstance(payload, dict):
        return None
    headers = {
        str(item.get("name", "")).lower(): _decode_header(str(item.get("value", "")))
        for item in payload.get("payload", {}).get("headers", [])
        if isinstance(item, dict)
    }
    sender = headers.get("from", "")
    subject = headers.get("subject", "")
    message_id = headers.get("message-id") or str(payload.get("id", ""))
    received_at = _parse_date(headers.get("date"))
    html_parts: list[str] = []
    text_parts: list[str] = []
    total = 0
    root = payload.get("payload") or {}
    for part in _walk_parts(root):
        mime = str(part.get("mimeType", "")).lower()
        if mime not in {"text/html", "text/plain"}:
            continue
        data = (part.get("body") or {}).get("data")
        if not data:
            continue
        decoded = _decode_body(data)
        if decoded is None:
            continue
        total += len(decoded)
        if total > max_bytes:
            return None
        (html_parts if mime == "text/html" else text_parts).append(decoded.decode("utf-8", errors="replace"))
    if not html_parts and not text_parts:
        return None
    return NewsletterMessage(
        message_id=message_id,
        sender=sender,
        received_at=received_at,
        subject=subject,
        html="\n".join(html_parts) or None,
        text="\n".join(text_parts) or None,
        label=label,
    )


def _walk_parts(part: dict[str, Any]):
    yield part
    for child in part.get("parts", []) or []:
        if isinstance(child, dict):
            yield from _walk_parts(child)


def _decode_body(value: str) -> bytes | None:
    try:
        return base64.urlsafe_b64decode(str(value) + "=" * (-len(str(value)) % 4))
    except (ValueError, TypeError, binascii.Error):
        return None


def _decode_header(value: str) -> str:
    output: list[str] = []
    try:
        parts = decode_header(value)
    except (TypeError, ValueError):
        return value
    for fragment, charset in parts:
        if isinstance(fragment, bytes):
            try:
                output.append(fragment.decode(charset or "utf-8", errors="replace"))
            except (LookupError, UnicodeError):
                output.append(fragment.decode("utf-8", errors="replace"))
        else:
            output.append(str(fragment))
    return "".join(output).strip()


def _parse_date(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=TAIPEI)
    return parsed.astimezone(TAIPEI)


__all__ = [
    "DEFAULT_LABEL",
    "GMAIL_READONLY_SCOPE",
    "GmailMailboxClient",
    "build_service",
    "validate_since",
]
