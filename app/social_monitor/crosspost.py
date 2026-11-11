"""Cross-platform duplicate grouping for political social posts."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from difflib import SequenceMatcher

from .models import utc_now_iso
from .repository import SocialRepository
from .normalize import normalize_social_text


PLATFORM_PRIORITY = {"facebook": 0, "threads": 1, "instagram": 2, "x": 3, "youtube": 4, "other": 9}
SIMILARITY_THRESHOLD = 0.90
TIME_WINDOW_SECONDS = 30 * 60


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)
    except ValueError:
        return None


def _similar(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    if a == b:
        return 1.0
    return SequenceMatcher(None, a, b).ratio()


def _canonical(post: dict) -> tuple:
    return (
        PLATFORM_PRIORITY.get(str(post.get("platform")), 9),
        _dt(post.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc),
        str(post.get("post_id")),
    )


def build_crossposts(repo: SocialRepository, *, person_id: str | None = None) -> dict[str, int]:
    posts = repo.list_posts(person_id=person_id, include_deleted=False)
    by_person: dict[str, list[dict]] = {}
    for post in posts:
        by_person.setdefault(post["person_id"], []).append(post)

    people = [person_id] if person_id else list(by_person)
    for pid in people:
        self_posts = by_person.get(pid, [])
        # Idempotent rebuild for the selected person only.
        repo.conn.execute(
            "DELETE FROM social_crosspost_group WHERE person_id=?", (pid,)
        )
        repo.conn.execute(
            "UPDATE social_post SET crosspost_group_id=NULL WHERE person_id=?", (pid,)
        )
        groups: list[list[dict]] = []
        for post in sorted(self_posts, key=lambda p: (_dt(p.get("published_at")) or datetime.min.replace(tzinfo=timezone.utc))):
            placed = False
            for group in groups:
                anchor = group[0]
                same_text = _similar(
                    normalize_social_text(post.get("normalized_text") or post.get("text")),
                    normalize_social_text(anchor.get("normalized_text") or anchor.get("text")),
                )
                if same_text < SIMILARITY_THRESHOLD:
                    continue
                a = _dt(anchor.get("published_at"))
                b = _dt(post.get("published_at"))
                if a is not None and b is not None and abs((a - b).total_seconds()) > TIME_WINDOW_SECONDS:
                    continue
                group.append(post)
                placed = True
                break
            if not placed:
                groups.append([post])
        created = 0
        for members in groups:
            if len(members) < 2:
                continue
            canonical = min(members, key=_canonical)
            member_ids = sorted(str(m["post_id"]) for m in members)
            group_id = "cpg_" + hashlib.sha256("|".join(member_ids).encode("utf-8")).hexdigest()[:24]
            now = utc_now_iso()
            repo.conn.execute(
                """
                INSERT INTO social_crosspost_group
                    (crosspost_group_id, person_id, canonical_post_id, member_post_ids_json,
                     matching_method, matching_score, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(crosspost_group_id) DO UPDATE SET
                    canonical_post_id=excluded.canonical_post_id,
                    member_post_ids_json=excluded.member_post_ids_json,
                    matching_method=excluded.matching_method,
                    matching_score=excluded.matching_score,
                    updated_at=excluded.updated_at
                """,
                (
                    group_id, pid, canonical["post_id"],
                    json.dumps(member_ids, ensure_ascii=False),
                    "normalized_text_time_window", 1.0, now, now,
                ),
            )
            for member in members:
                repo.conn.execute(
                    "UPDATE social_post SET crosspost_group_id=? WHERE post_id=?",
                    (group_id, member["post_id"]),
                )
            created += 1
        repo.conn.commit()
    return {"groups": sum(
        1 for row in repo.conn.execute("SELECT 1 FROM social_crosspost_group")
    )}


def canonical_posts(repo: SocialRepository, *, person_id: str | None = None) -> list[dict]:
    """Return one reporting item per crosspost group, plus ungrouped posts."""
    posts = repo.list_posts(person_id=person_id, include_deleted=False)
    by_id = {post["post_id"]: post for post in posts}
    import json as _json
    groups = repo.list_crosspost_groups()
    grouped_ids: set[str] = set()
    output: list[dict] = []
    for group in groups:
        if person_id and group.get("person_id") != person_id:
            continue
        for post_id in _json.loads(group.get("member_post_ids_json") or "[]"):
            grouped_ids.add(str(post_id))
        post = by_id.get(group["canonical_post_id"])
        if post:
            output.append(post)
    for post in posts:
        if post["post_id"] not in grouped_ids:
            output.append(post)
    return output
