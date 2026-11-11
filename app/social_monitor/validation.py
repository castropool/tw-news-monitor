"""Integrity checks for political social registry and database rows."""

from __future__ import annotations

from typing import Any

from .models import ACCOUNT_TYPES, PLATFORMS, VERIFICATION_STATUSES
from .registry import validate_social_config
from .repository import SocialRepository


def validate_database(repo: SocialRepository) -> list[str]:
    errors: list[str] = []
    seen_account_ids: set[str] = set()
    seen_platform_users: set[tuple[str, str]] = set()
    for account in repo.list_accounts():
        aid = account["account_id"]
        if aid in seen_account_ids:
            errors.append(f"duplicate account_id: {aid}")
        seen_account_ids.add(aid)
        if not repo.get_person(account["person_id"]):
            errors.append(f"account {aid} references missing person {account['person_id']}")
        if account["platform"] not in PLATFORMS:
            errors.append(f"account {aid} has invalid platform {account['platform']}")
        if account.get("account_type") not in ACCOUNT_TYPES:
            errors.append(f"account {aid} has invalid account_type {account.get('account_type')}")
        if account.get("verification_status") not in VERIFICATION_STATUSES:
            errors.append(f"account {aid} has invalid verification_status")
        key = (account["platform"], str(account.get("platform_user_id") or ""))
        if key[1]:
            if key in seen_platform_users:
                errors.append(f"duplicate platform/platform_user_id: {key}")
            seen_platform_users.add(key)
    for post in repo.list_posts(include_deleted=True):
        if not repo.get_post_by_id(post["post_id"]):
            errors.append(f"orphan social_post: {post['post_id']}")
        if not repo.get_account(post["account_id"]):
            errors.append(f"social_post {post['post_id']} references missing account")
        if not repo.get_person(post["person_id"]):
            errors.append(f"social_post {post['post_id']} references missing person")
        if not post.get("canonical_url"):
            errors.append(f"social_post {post['post_id']} missing canonical_url")
    for row in repo.list_source_links():
        if not repo.get_post_by_id(row["post_id"]):
            errors.append(f"source link references missing post: {row['post_id']}")
    import json as _json
    for row in repo.list_crosspost_groups():
        member_ids = _json.loads(row["member_post_ids_json"] or "[]")
        if row["canonical_post_id"] not in member_ids:
            errors.append(f"crosspost {row['crosspost_group_id']} canonical not in members")
        for post_id in member_ids:
            if not repo.get_post_by_id(post_id):
                errors.append(f"crosspost {row['crosspost_group_id']} missing member {post_id}")
    return errors


def validate_config_and_database(config: dict[str, Any], repo: SocialRepository) -> dict[str, Any]:
    errors = validate_social_config(config)
    errors.extend(validate_database(repo))
    return {"valid": not errors, "errors": errors}
