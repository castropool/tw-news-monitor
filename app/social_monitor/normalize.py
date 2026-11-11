"""Conservative text/URL normalisation for political social posts."""

from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


ZERO_WIDTH_RE = re.compile(r"[\u200b-\u200f\u2060\ufeff]")
WHITESPACE_RE = re.compile(r"[ \t\f\v]+")
MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
TRACKING_PARAMS = {
    "fbclid", "gclid", "igshid", "mc_cid", "mc_eid", "ref", "ref_src",
    "si", "utm_source", "utm_medium", "utm_campaign", "utm_term", "utm_content",
}


def normalize_social_text(value: str | None) -> str:
    """Normalize platform text without rewriting the speaker's meaning."""
    text = html.unescape(str(value or ""))
    text = unicodedata.normalize("NFKC", text)
    text = ZERO_WIDTH_RE.sub("", text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    lines = [WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n")]
    text = "\n".join(lines)
    text = MULTI_NEWLINE_RE.sub("\n\n", text).strip()
    return text


def normalize_url(value: str | None) -> str | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    parts = urlsplit(raw)
    if not parts.scheme:
        return raw
    query = [(k, v) for k, v in parse_qsl(parts.query, keep_blank_values=True) if k.lower() not in TRACKING_PARAMS]
    path = re.sub(r"/+$", "", parts.path) or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, urlencode(query, doseq=True), ""))


def content_hash(text: str | None) -> str:
    normalized = normalize_social_text(text)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def stable_post_id(platform: str, platform_post_id: str) -> str:
    raw = f"{str(platform).lower()}:{str(platform_post_id)}"
    return "sp_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:24]
