"""Bridge social posts into the reporting source layer (one canonical post → one source)."""

from __future__ import annotations

import hashlib

from .crosspost import canonical_posts
from .models import utc_now_iso
from .repository import SocialRepository


def source_id_for_post(post: dict) -> str:
    raw = f"{post.get('platform')}:{post.get('platform_post_id')}"
    return "src_social_" + str(post.get("platform")) + "_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:16]


def bridge_post(repo: SocialRepository, post_id: str) -> str:
    existing = repo.conn.execute(
        "SELECT source_id FROM social_source_link WHERE post_id=?", (post_id,)
    ).fetchone()
    if existing:
        return str(existing[0])
    post = repo.get_post_by_id(post_id)
    if not post:
        raise ValueError(f"social post not found: {post_id}")
    source_id = source_id_for_post(post)
    repo.conn.execute(
        "INSERT OR IGNORE INTO social_source_link(post_id, source_id, created_at) VALUES (?, ?, ?)",
        (post_id, source_id, utc_now_iso()),
    )
    repo.conn.commit()
    existing = repo.conn.execute(
        "SELECT source_id FROM social_source_link WHERE post_id=?", (post_id,)
    ).fetchone()
    return str(existing[0]) if existing else source_id


def bridge_all(repo: SocialRepository, *, person_id: str | None = None) -> dict[str, int]:
    """Create one canonical source per crosspost group (or per ungrouped post)."""
    posts = canonical_posts(repo, person_id=person_id)
    linked = 0
    for post in posts:
        bridge_post(repo, post["post_id"])
        linked += 1
    return {"linked": linked}


def source_record(repo: SocialRepository, post_id: str) -> dict:
    post = repo.get_post_by_id(post_id)
    if not post:
        raise ValueError(f"social post not found: {post_id}")
    source_id = bridge_post(repo, post_id)
    return {
        "source_id": source_id,
        "source_type": "political_social",
        "publisher_type": "political_person",
        "platform": post.get("platform"),
        "person_id": post.get("person_id"),
        "account_id": post.get("account_id"),
        "post_id": post_id,
        "published_at": post.get("published_at"),
        "canonical_url": post.get("canonical_url"),
    }
