"""SQLite persistence helpers for the political social monitor."""

from __future__ import annotations

import json
from typing import Any

from ..database import Database
from .models import (
    PoliticalPerson,
    SocialAccount,
    SocialPost,
    utc_now_iso,
)
from .normalize import content_hash, normalize_social_text, normalize_url, stable_post_id


PERSON_COLUMNS = [
    "person_id", "canonical_name", "display_name", "current_role", "organization",
    "party", "jurisdiction_level", "jurisdiction", "election_scope", "candidate_id",
    "monitoring_tier", "active_from", "active_to", "enabled", "created_at", "updated_at",
]
ACCOUNT_COLUMNS = [
    "account_id", "person_id", "platform", "platform_user_id", "handle", "canonical_url",
    "display_name", "account_type", "account_status", "verification_status",
    "verification_method", "verification_source_url", "verified_at", "collection_method",
    "enabled", "monitoring_priority", "first_seen_at", "last_checked_at", "last_success_at",
    "last_seen_post_id", "last_seen_published_at", "consecutive_failures", "last_error_code",
    "created_at", "updated_at",
]
POST_COLUMNS = [
    "post_id", "platform", "platform_post_id", "account_id", "person_id", "published_at",
    "first_seen_at", "last_seen_at", "text", "normalized_text", "title", "canonical_url",
    "media_type", "media_urls_json", "thumbnail_url", "external_urls_json", "is_reply",
    "is_repost", "is_quote", "reply_to_post_id", "quoted_post_id", "reposted_post_id",
    "language", "content_hash", "crosspost_group_id", "edited", "deleted",
    "raw_payload_hash", "raw_snapshot_path", "created_at", "updated_at",
]
RUN_COLUMNS = [
    "run_id", "started_at", "finished_at", "accounts_attempted", "accounts_successful",
    "accounts_failed", "posts_seen", "posts_new", "posts_updated", "posts_deleted",
    "crossposts_created", "errors_json", "warnings_json",
]


def _d(row, columns: list[str]) -> dict[str, Any] | None:
    if row is None:
        return None
    return {name: row[index] for index, name in enumerate(columns)}


class SocialRepository:
    def __init__(self, db: Database):
        self.db = db
        self.conn = db.conn

    # -- registry ---------------------------------------------------------
    def upsert_person(self, person: PoliticalPerson) -> None:
        now = utc_now_iso()
        existed = self.get_person(person.person_id)
        created_at = existed["created_at"] if existed else now
        self.conn.execute(
            """
            INSERT INTO political_person
                (person_id, canonical_name, display_name, current_role, organization,
                 party, jurisdiction_level, jurisdiction, election_scope, candidate_id,
                 monitoring_tier, active_from, active_to, enabled, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(person_id) DO UPDATE SET
                canonical_name=excluded.canonical_name, display_name=excluded.display_name,
                current_role=excluded.current_role, organization=excluded.organization,
                party=excluded.party, jurisdiction_level=excluded.jurisdiction_level,
                jurisdiction=excluded.jurisdiction, election_scope=excluded.election_scope,
                candidate_id=excluded.candidate_id, monitoring_tier=excluded.monitoring_tier,
                active_from=excluded.active_from, active_to=excluded.active_to,
                enabled=excluded.enabled, updated_at=excluded.updated_at
            """,
            (
                person.person_id, person.canonical_name, person.display_name,
                person.current_role, person.organization, person.party,
                person.jurisdiction_level, person.jurisdiction, person.election_scope,
                person.candidate_id, person.monitoring_tier, person.active_from,
                person.active_to, 1 if person.enabled else 0, created_at, now,
            ),
        )
        self.conn.commit()

    def get_person(self, person_id: str) -> dict[str, Any] | None:
        return _d(self.conn.execute(
            "SELECT * FROM political_person WHERE person_id=?", (person_id,)
        ).fetchone(), PERSON_COLUMNS)

    def list_persons(self) -> list[dict[str, Any]]:
        return [_d(r, PERSON_COLUMNS) for r in self.conn.execute("SELECT * FROM political_person").fetchall()]

    def upsert_account(self, account: SocialAccount) -> None:
        now = utc_now_iso()
        existed = self.get_account(account.account_id)
        created_at = existed["created_at"] if existed else (account.created_at or now)
        self.conn.execute(
            """
            INSERT INTO social_account
                (account_id, person_id, platform, platform_user_id, handle, canonical_url,
                 display_name, account_type, account_status, verification_status,
                 verification_method, verification_source_url, verified_at,
                 collection_method, enabled, monitoring_priority, first_seen_at,
                 last_checked_at, last_success_at, last_seen_post_id,
                 last_seen_published_at, consecutive_failures, last_error_code,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(account_id) DO UPDATE SET
                person_id=excluded.person_id, platform=excluded.platform,
                platform_user_id=excluded.platform_user_id, handle=excluded.handle,
                canonical_url=excluded.canonical_url, display_name=excluded.display_name,
                account_type=excluded.account_type, account_status=excluded.account_status,
                verification_status=excluded.verification_status,
                verification_method=excluded.verification_method,
                verification_source_url=excluded.verification_source_url,
                verified_at=excluded.verified_at, collection_method=excluded.collection_method,
                enabled=excluded.enabled, monitoring_priority=excluded.monitoring_priority,
                updated_at=excluded.updated_at
            """,
            (
                account.account_id, account.person_id, account.platform,
                account.platform_user_id, account.handle, normalize_url(account.canonical_url),
                account.display_name, account.account_type, account.account_status,
                account.verification_status, account.verification_method,
                account.verification_source_url, account.verified_at,
                account.collection_method, 1 if account.enabled else 0,
                account.monitoring_priority, account.first_seen_at or now,
                account.last_checked_at, account.last_success_at,
                account.last_seen_post_id, account.last_seen_published_at,
                account.consecutive_failures, account.last_error_code, created_at, now,
            ),
        )
        self.conn.commit()

    def get_account(self, account_id: str) -> dict[str, Any] | None:
        return _d(self.conn.execute(
            "SELECT * FROM social_account WHERE account_id=?", (account_id,)
        ).fetchone(), ACCOUNT_COLUMNS)

    def list_accounts(self, *, platform: str | None = None, person_id: str | None = None,
                      account_id: str | None = None) -> list[dict[str, Any]]:
        clauses, params = [], []
        if platform:
            clauses.append("platform=?"); params.append(platform)
        if person_id:
            clauses.append("person_id=?"); params.append(person_id)
        if account_id:
            clauses.append("account_id=?"); params.append(account_id)
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(f"SELECT * FROM social_account{where}", params).fetchall()
        return [_d(r, ACCOUNT_COLUMNS) for r in rows]

    # -- posts ------------------------------------------------------------
    def get_post(self, platform: str, platform_post_id: str) -> dict[str, Any] | None:
        return _d(self.conn.execute(
            "SELECT * FROM social_post WHERE platform=? AND platform_post_id=?",
            (platform, platform_post_id),
        ).fetchone(), POST_COLUMNS)

    def get_post_by_id(self, post_id: str) -> dict[str, Any] | None:
        return _d(self.conn.execute(
            "SELECT * FROM social_post WHERE post_id=?", (post_id,)
        ).fetchone(), POST_COLUMNS)

    def _next_revision_number(self, post_id: str) -> int:
        row = self.conn.execute(
            "SELECT COALESCE(MAX(revision_number), 0) + 1 FROM social_post_revision WHERE post_id=?",
            (post_id,),
        ).fetchone()
        return int(row[0] or 1)

    def upsert_post(self, post: SocialPost) -> tuple[str, str]:
        now = utc_now_iso()
        post_id = post.post_id or stable_post_id(post.platform, post.platform_post_id)
        existing = self.get_post(post.platform, post.platform_post_id)
        normalized = post.normalized_text or normalize_social_text(post.text)
        new_hash = post.content_hash or content_hash(normalized)
        if existing is None:
            self.conn.execute(
                """
                INSERT INTO social_post
                    (post_id, platform, platform_post_id, account_id, person_id,
                     published_at, first_seen_at, last_seen_at, text, normalized_text,
                     title, canonical_url, media_type, media_urls_json, thumbnail_url,
                     external_urls_json, is_reply, is_repost, is_quote, reply_to_post_id,
                     quoted_post_id, reposted_post_id, language, content_hash,
                     crosspost_group_id, edited, deleted, raw_payload_hash,
                     raw_snapshot_path, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    post_id, post.platform, str(post.platform_post_id), post.account_id,
                    post.person_id, post.published_at, now, now, post.text, normalized,
                    post.title, normalize_url(post.canonical_url), post.media_type,
                    json.dumps(post.media_urls or [], ensure_ascii=False), post.thumbnail_url,
                    json.dumps(post.external_urls or [], ensure_ascii=False),
                    int(post.is_reply), int(post.is_repost), int(post.is_quote),
                    post.reply_to_post_id, post.quoted_post_id, post.reposted_post_id,
                    post.language, new_hash, post.crosspost_group_id, int(post.edited),
                    int(post.deleted), post.raw_payload_hash, post.raw_snapshot_path, now, now,
                ),
            )
            self.conn.commit()
            return post_id, "new"
        old_hash = existing.get("content_hash")
        if old_hash and old_hash != new_hash:
            revision_number = self._next_revision_number(post_id)
            self.conn.execute(
                """
                INSERT INTO social_post_revision
                    (revision_id, post_id, revision_number, text, content_hash,
                     observed_at, raw_snapshot_hash)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (f"{post_id}:r{revision_number}", post_id, revision_number,
                 existing.get("text"), old_hash, existing.get("last_seen_at") or now,
                 existing.get("raw_payload_hash")),
            )
            self.conn.execute(
                "UPDATE social_post SET text=?, normalized_text=?, content_hash=?, edited=1, last_seen_at=?, updated_at=? WHERE post_id=?",
                (post.text, normalized, new_hash, now, now, post_id),
            )
            self.conn.commit()
            return post_id, "updated"
        self.conn.execute(
            "UPDATE social_post SET last_seen_at=?, updated_at=? WHERE post_id=?",
            (now, now, post_id),
        )
        self.conn.commit()
        return post_id, "duplicate"

    def mark_deleted(self, post_id: str, *, deleted: bool = True) -> None:
        self.conn.execute(
            "UPDATE social_post SET deleted=?, updated_at=? WHERE post_id=?",
            (1 if deleted else 0, utc_now_iso(), post_id),
        )
        self.conn.commit()

    def list_posts(self, *, person_id: str | None = None, account_id: str | None = None,
                   include_deleted: bool = False) -> list[dict[str, Any]]:
        clauses, params = [], []
        if person_id:
            clauses.append("person_id=?"); params.append(person_id)
        if account_id:
            clauses.append("account_id=?"); params.append(account_id)
        if not include_deleted:
            clauses.append("deleted=0")
        where = (" WHERE " + " AND ".join(clauses)) if clauses else ""
        rows = self.conn.execute(
            f"SELECT * FROM social_post{where} ORDER BY published_at DESC, post_id", params
        ).fetchall()
        return [_d(r, POST_COLUMNS) for r in rows]

    def update_account_cursor(
        self, account_id: str, *, last_post_id: str | None,
        last_published_at: str | None, success: bool, error_code: str | None = None,
    ) -> None:
        now = utc_now_iso()
        if success:
            self.conn.execute(
                """
                UPDATE social_account SET last_checked_at=?, last_success_at=?,
                    last_seen_post_id=COALESCE(?, last_seen_post_id),
                    last_seen_published_at=COALESCE(?, last_seen_published_at),
                    consecutive_failures=0, last_error_code=NULL,
                    account_status='active', updated_at=?
                WHERE account_id=?
                """,
                (now, now, last_post_id, last_published_at, now, account_id),
            )
        else:
            self.conn.execute(
                """
                UPDATE social_account SET last_checked_at=?,
                    consecutive_failures=consecutive_failures+1,
                    last_error_code=?, updated_at=? WHERE account_id=?
                """,
                (now, error_code, now, account_id),
            )
        self.conn.commit()

    # -- runs / links -----------------------------------------------------
    def start_run(self, run_id: str) -> None:
        self.conn.execute(
            "INSERT INTO social_monitor_run(run_id, started_at) VALUES (?, ?)",
            (run_id, utc_now_iso()),
        )
        self.conn.commit()

    def finish_run(self, run_id: str, **values: Any) -> None:
        allowed = {"accounts_attempted", "accounts_successful", "accounts_failed",
                   "posts_seen", "posts_new", "posts_updated", "posts_deleted",
                   "crossposts_created", "errors_json", "warnings_json"}
        updates = {k: v for k, v in values.items() if k in allowed}
        if not updates:
            return
        assignments = ", ".join(f"{key}=?" for key in updates)
        self.conn.execute(
            f"UPDATE social_monitor_run SET finished_at=?, {assignments} WHERE run_id=?",
            [utc_now_iso(), *updates.values(), run_id],
        )
        self.conn.commit()

    def get_run(self, run_id: str) -> dict[str, Any] | None:
        return _d(self.conn.execute(
            "SELECT * FROM social_monitor_run WHERE run_id=?", (run_id,)
        ).fetchone(), RUN_COLUMNS)

    def list_source_links(self) -> list[dict[str, Any]]:
        return [dict(post_id=r[0], source_id=r[1], created_at=r[2])
                for r in self.conn.execute("SELECT post_id, source_id, created_at FROM social_source_link").fetchall()]

    def list_crosspost_groups(self) -> list[dict[str, Any]]:
        columns = ["crosspost_group_id", "person_id", "canonical_post_id",
                   "member_post_ids_json", "matching_method", "matching_score",
                   "created_at", "updated_at"]
        rows = self.conn.execute("SELECT * FROM social_crosspost_group").fetchall()
        return [_d(r, columns) for r in rows]
