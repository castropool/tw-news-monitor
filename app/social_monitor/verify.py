"""Automated account verification for social registries (KOL and political).

Accounts are only collectable once ``verified`` (registry rule).  Account
identifiers in the YAML registries are *candidates*; this module proves them
on a networked deployment machine and records the evidence in the
verification store (``data/social_monitor/account_verifications.json``):

- YouTube: resolve ``/channel/UC…``, ``/@handle`` or a bare handle to a
  channel ID (from the channel page), fetch the public channel feed and
  require its title to match one of the person's configured names.
- Any account with ``feed_url``: fetch the feed and require its title or
  author to match a configured name.
- Other platforms without a feed cannot be collected (their official
  adapters are not implemented) and are reported as such.

A name mismatch is never recorded as verified.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any

import feedparser
import httpx

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)
_CHANNEL_ID_RE = re.compile(r"(UC[0-9A-Za-z_-]{22})")
_CHANNEL_PAGE_PATTERNS = (
    re.compile(r'<link rel="canonical" href="https://www\.youtube\.com/channel/(UC[0-9A-Za-z_-]{22})"'),
    re.compile(r'<meta itemprop="identifier" content="(UC[0-9A-Za-z_-]{22})"'),
    re.compile(r'"externalId":"(UC[0-9A-Za-z_-]{22})"'),
    re.compile(r'"channelId":"(UC[0-9A-Za-z_-]{22})"'),
)
YOUTUBE_FEED = "https://www.youtube.com/feeds/videos.xml?channel_id={channel_id}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_name(value: str | None) -> str:
    text = unicodedata.normalize("NFKC", str(value or "")).lower()
    return re.sub(r"[\s·・.\-_|｜/]+", "", text)


def expected_names(person: dict[str, Any], account: dict[str, Any]) -> list[str]:
    names = [person.get("canonical_name"), person.get("display_name"), account.get("display_name")]
    names.extend(person.get("aliases") or [])
    names.extend(account.get("aliases") or [])
    output: list[str] = []
    for name in names:
        norm = normalize_name(name)
        if len(norm) >= 2 and norm not in output:
            output.append(norm)
    return output


def name_matches(observed: str | None, names: list[str]) -> bool:
    norm = normalize_name(observed)
    if len(norm) < 2:
        return False
    return any(name in norm or norm in name for name in names)


def _client(client: Any = None) -> Any:
    if client is not None:
        return client
    return httpx.Client(timeout=15, follow_redirects=True, headers={"User-Agent": USER_AGENT})


def _get_text(client: Any, url: str) -> str:
    response = client.get(url)
    status = int(getattr(response, "status_code", 200) or 200)
    if status >= 400:
        raise ValueError(f"HTTP {status} for {url}")
    return str(getattr(response, "text", "") or "")


def youtube_page_url(value: str) -> str:
    value = value.strip()
    if value.startswith("http"):
        return value
    handle = value if value.startswith("@") else f"@{value}"
    return f"https://www.youtube.com/{handle}"


def resolve_youtube_channel_id(value: str, client: Any) -> tuple[str | None, str]:
    """Return ``(channel_id, evidence_url)`` for a channel URL/handle."""
    value = (value or "").strip()
    if not value:
        return None, ""
    direct = re.search(r"/channel/(UC[0-9A-Za-z_-]{22})", value)
    if direct:
        return direct.group(1), value
    if _CHANNEL_ID_RE.fullmatch(value):
        return value, f"https://www.youtube.com/channel/{value}"
    page_url = youtube_page_url(value)
    html = _get_text(client, page_url)
    for pattern in _CHANNEL_PAGE_PATTERNS:
        match = pattern.search(html)
        if match:
            return match.group(1), page_url
    return None, page_url


def _feed_identity(text: str) -> tuple[str, str, int]:
    parsed = feedparser.parse(text)
    feed = parsed.feed or {}
    author = feed.get("author") or (feed.get("author_detail") or {}).get("name") or ""
    return str(feed.get("title") or ""), str(author), len(parsed.entries or [])


def verify_account(
    person: dict[str, Any],
    account: dict[str, Any],
    *,
    url: str | None = None,
    client: Any = None,
) -> dict[str, Any]:
    """Verify one account; returns a store record (``status`` verified/failed)."""
    http = _client(client)
    platform = str(account.get("platform") or "").lower()
    names = expected_names(person, account)
    record: dict[str, Any] = {
        "account_id": account.get("account_id"),
        "person_id": person.get("person_id"),
        "platform": platform,
        "checked_at": _now_iso(),
        "status": "failed",
    }
    feed_url = str(account.get("feed_url") or "").strip()
    target = (url or "").strip()
    try:
        if platform == "youtube" and not (feed_url and not target):
            source = target or account.get("canonical_url") or account.get("handle") or account.get("platform_user_id") or ""
            if not source:
                record["reason"] = "no_candidate_identifier"
                return record
            channel_id, evidence = resolve_youtube_channel_id(str(source), http)
            record["evidence_url"] = evidence
            if not channel_id:
                record["reason"] = "channel_id_not_found"
                return record
            feed = YOUTUBE_FEED.format(channel_id=channel_id)
            title, author, entries = _feed_identity(_get_text(http, feed))
            record.update({
                "platform_user_id": channel_id,
                "canonical_url": f"https://www.youtube.com/channel/{channel_id}",
                "observed_title": title,
                "entries": entries,
                "method": "youtube_channel_feed_title_match",
            })
            if str(source).startswith("@") or "/@" in str(source):
                record["handle"] = "@" + str(source).split("@", 1)[1].split("/")[0].split("?")[0]
            matched = name_matches(title, names) or name_matches(author, names)
        else:
            if platform == "youtube" and feed_url:
                feed = feed_url
            elif target and (target.endswith((".xml", ".rss")) or "rss" in target or "feed" in target):
                feed = target
            else:
                feed = feed_url
            if not feed:
                record["reason"] = f"no_collectable_method_for_{platform or 'unknown'}"
                return record
            title, author, entries = _feed_identity(_get_text(http, feed))
            record.update({
                "feed_url": feed,
                "evidence_url": feed,
                "observed_title": title,
                "observed_author": author,
                "entries": entries,
                "method": "feed_title_match",
            })
            if target and target != feed:
                record["canonical_url"] = target
            matched = name_matches(title, names) or name_matches(author, names)
    except Exception as exc:  # noqa: BLE001 - verification reports, never raises
        record["reason"] = f"fetch_failed: {type(exc).__name__}: {exc}"[:300]
        return record
    if not matched:
        record["reason"] = "name_mismatch"
        record["expected_names"] = names
        return record
    record["status"] = "verified"
    record["reason"] = "matched"
    return record
