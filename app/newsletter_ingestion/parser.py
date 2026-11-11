"""Offline HTML/plain/EML Newsletter parser with conservative limits."""

from datetime import datetime
import email
import email.header
import email.message
import email.utils
import hashlib
import re
from typing import Any
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from ..time_utils import TAIPEI
from .models import NewsletterItem, NewsletterMessage
from .policy import SourcePolicy
from .url_policy import URLPolicy, normalize_tracking_url


_URL_LINE_RE = re.compile(r"^https://[^\s<>]+$", re.IGNORECASE)
_HEADING_TAGS = ("h1", "h2", "h3", "h4", "h5", "h6")
_BLOCK_TAGS = ("p", "li", "td", "div")
_SKIP_LINK_WORDS = re.compile(r"\b(unsubscribe|manage preferences|view in browser|privacy policy)\b", re.I)


def parse_message(
    message: NewsletterMessage | bytes | str | email.message.Message,
    *,
    policy: SourcePolicy | None = None,
    url_policy: URLPolicy | None = None,
    source_id: str | None = None,
    max_message_bytes: int | None = None,
    max_parts: int | None = None,
    max_items: int | None = None,
) -> list[NewsletterItem]:
    """Parse one message without making network calls.

    Raw bytes are treated as EML.  A raw string beginning with HTML is treated
    as HTML for convenience; other strings are treated as plain text unless
    they contain mail headers.  A ``NewsletterMessage`` is already normalized
    by a mailbox client and can carry either or both body representations.
    """
    if isinstance(message, NewsletterMessage):
        if policy is not None:
            decision = policy.check(message.label, message.sender)
            if not decision.accepted:
                return []
        if not _within_message_limit(message, max_message_bytes or (policy.max_message_bytes if policy else 5 * 1024 * 1024)):
            return []
        return _parse_normalized_message(
            message, policy=policy, url_policy=url_policy, source_id=source_id,
            max_items=max_items or (policy.max_items if policy else 20),
        )
    if isinstance(message, email.message.Message):
        return _parse_email_message(
            message, policy=policy, url_policy=url_policy, source_id=source_id,
            max_message_bytes=max_message_bytes or (policy.max_message_bytes if policy else 5 * 1024 * 1024),
            max_parts=max_parts or (policy.max_parts if policy else 64),
            max_items=max_items or (policy.max_items if policy else 20),
        )
    if isinstance(message, (bytes, bytearray)):
        raw = bytes(message)
        raw_limit = max_message_bytes or (policy.max_message_bytes if policy else 5 * 1024 * 1024)
        # This check deliberately precedes ``message_from_bytes``.  MIME
        # parsing can expand encoded parts and consume substantial memory, so
        # the original transport bytes (headers + boundaries + body) are the
        # first and authoritative size gate.
        if len(raw) > raw_limit:
            return []
        if raw.lstrip().startswith(b"<"):
            return parse_message(raw.decode("utf-8", errors="replace"), policy=policy,
                                 url_policy=url_policy, source_id=source_id,
                                 max_message_bytes=max_message_bytes, max_parts=max_parts,
                                 max_items=max_items)
        parsed = email.message_from_bytes(raw)
        return parse_message(parsed, policy=policy, url_policy=url_policy, source_id=source_id,
                             max_message_bytes=max_message_bytes, max_parts=max_parts, max_items=max_items)
    if isinstance(message, str):
        if _looks_like_eml(message):
            raw_limit = max_message_bytes or (policy.max_message_bytes if policy else 5 * 1024 * 1024)
            if len(message.encode("utf-8", errors="replace")) > raw_limit:
                return []
            parsed = email.message_from_string(message)
            return parse_message(parsed, policy=policy, url_policy=url_policy, source_id=source_id,
                                 max_message_bytes=max_message_bytes, max_parts=max_parts, max_items=max_items)
        normalized = NewsletterMessage("raw", "", None, "", message if _looks_like_html(message) else None,
                                       None if _looks_like_html(message) else message, "")
        return parse_message(normalized, policy=None, url_policy=url_policy, source_id=source_id,
                             max_message_bytes=max_message_bytes, max_items=max_items)
    raise TypeError("message must be NewsletterMessage, EML bytes/string, or email.message.Message")


def _parse_email_message(msg: email.message.Message, *, policy, url_policy, source_id, max_message_bytes, max_parts, max_items):
    try:
        if len(msg.as_bytes()) > max_message_bytes:
            return []
    except (OSError, ValueError):
        # A mailbox-provided Message object may not be serializable.  The
        # per-part byte accounting below remains the safe fallback.
        pass
    parts = list(msg.walk()) if msg.is_multipart() else [msg]
    if len(parts) > max_parts:
        return []
    total = 0
    html_body = None
    text_body = None
    for part in parts:
        if part.is_multipart():
            continue
        raw_payload = part.get_payload(decode=True)
        if isinstance(raw_payload, bytes):
            total += len(raw_payload)
            if total > max_message_bytes:
                return []
        if _is_ignored_part(part):
            continue
        if part.get_content_type() not in {"text/html", "text/plain"}:
            continue
        body = _decode_part(part)
        # Count undecodable/string payloads as UTF-8 when get_payload(decode=True)
        # was unavailable (malformed fixtures and unusual charset headers).
        if raw_payload is None:
            total += len(body.encode("utf-8", errors="replace"))
            if total > max_message_bytes:
                return []
        if part.get_content_type() == "text/html" and html_body is None:
            html_body = body
        elif part.get_content_type() == "text/plain" and text_body is None:
            text_body = body
    sender = _decode_header(str(msg.get("From", "")))
    subject = _decode_header(str(msg.get("Subject", "")))
    received_at = _parse_date(msg.get("Date"))
    normalized = NewsletterMessage(
        message_id=str(msg.get("Message-ID", "raw-eml")), sender=sender,
        received_at=received_at, subject=subject, html=html_body, text=text_body,
        label="",
    )
    if policy is not None and not policy.check(normalized.label, normalized.sender).accepted:
        # EML fixtures can provide a label through X-Label; absent labels are
        # intentionally rejected when a policy is supplied.
        label = str(msg.get("X-Newsletter-Label", ""))
        normalized = NewsletterMessage(normalized.message_id, normalized.sender, normalized.received_at,
                                       normalized.subject, normalized.html, normalized.text, label)
        if not policy.check(label, normalized.sender).accepted:
            return []
    return _parse_normalized_message(normalized, policy=policy, url_policy=url_policy,
                                     source_id=source_id, max_items=max_items)


def _parse_normalized_message(message: NewsletterMessage, *, policy, url_policy, source_id, max_items):
    source = source_id or (policy.source_id if policy else "")
    policy_url = url_policy or URLPolicy()
    items: list[NewsletterItem] = []
    seen_urls: set[str] = set()
    if message.html:
        candidates = _parse_html_candidates(message.html, message.received_at)
    elif message.text:
        candidates = _parse_text_candidates(message.text, message.received_at)
    else:
        return []
    for title, url, summary, published_at in candidates:
        if len(items) >= max_items:
            break
        title = _clean_text(title)
        if not title or _SKIP_LINK_WORDS.search(title):
            continue
        try:
            canonical = normalize_tracking_url(url, policy=policy_url)
        except ValueError:
            continue
        host = urlparse(canonical).hostname or ""
        if policy is not None and not policy.article_host_allowed(host):
            continue
        if canonical in seen_urls:
            continue
        seen_urls.add(canonical)
        item_id = hashlib.sha256(f"{source}|{canonical}|{_title_fingerprint(title)}".encode()).hexdigest()[:24]
        items.append(NewsletterItem(item_id, source, title, canonical, _clean_text(summary) or None, published_at))
    return items


def _parse_html_candidates(html: str, received_at: datetime | None):
    soup = BeautifulSoup(html, "html.parser")
    default_date = _html_date(soup) or received_at
    candidates = []
    for heading in soup.find_all(_HEADING_TAGS):
        anchor = heading.find("a", href=True)
        if anchor is None:
            continue
        summary = None
        sibling = heading.find_next_sibling("p")
        if sibling is not None:
            summary = sibling.get_text(" ", strip=True)
        candidates.append((anchor.get_text(" ", strip=True), anchor.get("href", ""), summary, default_date))
    for anchor in soup.find_all("a", href=True):
        if anchor.find_parent(_HEADING_TAGS):
            continue
        title = anchor.get_text(" ", strip=True)
        if not title:
            continue
        summary = None
        parent = anchor.find_parent(_BLOCK_TAGS)
        if parent is not None:
            text = parent.get_text(" ", strip=True)
            rest = text.replace(title, "", 1).strip(" -–—:")
            if rest and rest != title:
                summary = rest
        candidates.append((title, anchor.get("href", ""), summary, default_date))
    return candidates


def _parse_text_candidates(text: str, received_at: datetime | None):
    candidates = []
    for block in re.split(r"\n\s*\n+", text.strip()):
        lines = [line.strip() for line in block.splitlines() if line.strip()]
        if not lines:
            continue
        index = next((i for i, line in enumerate(lines) if _URL_LINE_RE.match(line.rstrip(".,;"))), None)
        if index is None or index == 0:
            continue
        url = lines[index].rstrip(".,;")
        candidates.append((" ".join(lines[:index]), url, " ".join(lines[index + 1:]) or None, received_at))
    return candidates


def _decode_part(part: email.message.Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        if isinstance(raw, bytes):
            payload = raw
        else:
            return str(raw or "")
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except (LookupError, UnicodeError):
        return payload.decode("utf-8", errors="replace")


def _is_ignored_part(part: email.message.Message) -> bool:
    if part.get_content_type() == "message/rfc822":
        return True
    disposition = (part.get_content_disposition() or "").lower()
    return disposition == "attachment" or bool(part.get_filename())


def _within_message_limit(message: NewsletterMessage, limit: int) -> bool:
    total = sum(len(value.encode("utf-8", errors="replace")) for value in (message.html, message.text) if value)
    return total <= limit


def _looks_like_eml(value: str) -> bool:
    return bool(re.search(r"(?im)^(from|date|subject|mime-version|content-type):\s", value) and "\n\n" in value)


def _looks_like_html(value: str) -> bool:
    return bool(re.search(r"<\s*(html|body|h[1-6]|a|p|table)\b", value, re.I))


def _clean_text(value: str | None) -> str:
    return " ".join((value or "").split())


def _title_fingerprint(title: str) -> str:
    return "".join(ch.lower() for ch in title if ch.isalnum())


def _decode_header(value: str) -> str:
    out = []
    for fragment, charset in email.header.decode_header(value):
        if isinstance(fragment, bytes):
            try:
                out.append(fragment.decode(charset or "utf-8", errors="replace"))
            except LookupError:
                out.append(fragment.decode("utf-8", errors="replace"))
        else:
            out.append(fragment)
    return "".join(out).strip()


def _parse_date(value: Any) -> datetime | None:
    if not value:
        return None
    text = str(value).strip()
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError, OverflowError):
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except (TypeError, ValueError, OverflowError):
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=TAIPEI)
    return parsed.astimezone(TAIPEI)


def _html_date(soup: BeautifulSoup) -> datetime | None:
    for element in soup.find_all("time"):
        value = element.get("datetime") or element.get_text(" ", strip=True)
        parsed = _parse_date(value)
        if parsed:
            return parsed
    for key in ("article:published_time", "date", "pubdate"):
        tag = soup.find("meta", attrs={"property": key}) or soup.find("meta", attrs={"name": key})
        if tag:
            parsed = _parse_date(tag.get("content"))
            if parsed:
                return parsed
    return None
