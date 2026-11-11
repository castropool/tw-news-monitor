"""Typed domain objects for the political social monitor."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any


PLATFORMS = ("facebook", "threads", "instagram", "youtube", "x", "line", "tiktok", "telegram", "other")
ACCOUNT_TYPES = (
    "personal_official",
    "office_official",
    "party_official",
    "campaign_official",
    "legislative_official",
    "other_official",
)
ACCOUNT_STATUSES = ("active", "inactive", "unverified", "suspended", "deleted", "redirected", "unknown")
VERIFICATION_STATUSES = ("verified", "unverified", "rejected", "pending")
JURISDICTION_LEVELS = ("national", "city", "county", "district", "party", "other")
MONITORING_TIERS = (1, 2, 3, 4)
RELEVANCE_CATEGORIES = (
    "policy", "election", "candidate_status", "party_strategy", "cross_strait",
    "defense", "foreign_affairs", "national_security", "legislature",
    "governance", "controversy_response", "personnel", "local_government",
    "campaign_activity", "routine_activity", "personal", "ceremonial", "other",
)
WORD_ALLOWED_CATEGORIES = frozenset({
    "policy", "election", "candidate_status", "party_strategy", "cross_strait",
    "defense", "foreign_affairs", "national_security", "legislature",
    "controversy_response", "personnel", "local_government",
})
CLAIM_TYPES = (
    "attributed_statement", "official_announcement", "policy_position",
    "campaign_statement", "personal_opinion", "factual_claim_unverified",
)
CLAIM_VERIFICATION_STATUSES = ("attributed_only", "corroborated", "official_record", "disputed", "unknown")


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def to_utc_iso(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    else:
        parsed = value
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).replace(microsecond=0).isoformat()


@dataclass(slots=True)
class PoliticalPerson:
    person_id: str
    canonical_name: str
    display_name: str | None = None
    current_role: str | None = None
    organization: str | None = None
    party: str | None = None
    jurisdiction_level: str | None = None
    jurisdiction: str | None = None
    election_scope: str | None = None
    candidate_id: str | None = None
    monitoring_tier: int | None = None
    active_from: str | None = None
    active_to: str | None = None
    enabled: bool = True


@dataclass(slots=True)
class SocialAccount:
    account_id: str
    person_id: str
    platform: str
    platform_user_id: str | None = None
    handle: str | None = None
    canonical_url: str | None = None
    display_name: str | None = None
    account_type: str = "personal_official"
    account_status: str = "unknown"
    verification_status: str = "unverified"
    verification_method: str | None = None
    verification_source_url: str | None = None
    verified_at: str | None = None
    collection_method: str | None = None
    enabled: bool = True
    monitoring_priority: int | None = None
    first_seen_at: str | None = None
    last_checked_at: str | None = None
    last_success_at: str | None = None
    last_seen_post_id: str | None = None
    last_seen_published_at: str | None = None
    consecutive_failures: int = 0
    last_error_code: str | None = None
    created_at: str | None = None
    updated_at: str | None = None


@dataclass(slots=True)
class SocialPost:
    post_id: str
    platform: str
    platform_post_id: str
    account_id: str
    person_id: str
    published_at: str | None
    text: str | None = None
    normalized_text: str | None = None
    title: str | None = None
    canonical_url: str | None = None
    media_type: str | None = None
    media_urls: list[str] = field(default_factory=list)
    thumbnail_url: str | None = None
    external_urls: list[str] = field(default_factory=list)
    is_reply: bool = False
    is_repost: bool = False
    is_quote: bool = False
    reply_to_post_id: str | None = None
    quoted_post_id: str | None = None
    reposted_post_id: str | None = None
    language: str | None = None
    content_hash: str | None = None
    crosspost_group_id: str | None = None
    edited: bool = False
    deleted: bool = False
    raw_payload_hash: str | None = None
    raw_snapshot_path: str | None = None
    first_seen_at: str | None = None
    last_seen_at: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
    relevance_category: str | None = None
    claim_type: str = "attributed_statement"
    claim_verification: str = "attributed_only"
    election_id: str | None = None
    city: str | None = None
    county: str | None = None
    candidate_id: str | None = None
    party: str | None = None
    raw_payload: dict[str, Any] | None = None


@dataclass(slots=True)
class SocialPostRevision:
    revision_id: str
    post_id: str
    revision_number: int
    text: str | None
    content_hash: str
    observed_at: str
    raw_snapshot_hash: str | None = None


@dataclass(slots=True)
class SocialCrosspostGroup:
    crosspost_group_id: str
    person_id: str
    canonical_post_id: str
    member_post_ids: list[str] = field(default_factory=list)
    matching_method: str = ""
    matching_score: float = 0.0
    created_at: str | None = None
    updated_at: str | None = None
