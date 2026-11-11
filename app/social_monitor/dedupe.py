"""Single-platform post identity and revision helpers."""

from __future__ import annotations

from .normalize import content_hash, stable_post_id


def post_identity(platform: str, platform_post_id: str) -> tuple[str, str]:
    return stable_post_id(platform, platform_post_id), content_hash("")


def should_create_revision(existing_hash: str | None, incoming_text: str | None) -> bool:
    return bool(existing_hash) and existing_hash != content_hash(incoming_text)
