"""Entity and relation registry with valid time (design capability C4).

Entities are people, organisations, parties and posts.  Relations link them
with a predicate and a validity period (``valid_from`` / ``valid_to``), and
always carry their source (a claim id and/or URL).

Sensitivity rule (design doc section 8, and the open person-level policy):

- Formal predicates (holding a post, party membership, leading an organisation,
  nomination, caucus membership) may be *proposed* automatically from
  appointment claims; they are shown marked 未確認 until an analyst confirms.
- Informal predicates (faction, informal alliance, alleged tie) are
  *sensitive*: never created automatically, and never shown or exported until
  an analyst confirms them.  Allegation claims never create relations.

CLI::

    python -m app.programs.registry seed
    python -m app.programs.registry propose --days 30
    python -m app.programs.registry list --entity 賴清德 [--at 2026-11-01] [--all]
    python -m app.programs.registry review <relation_id> confirmed|rejected --reviewer NAME
    python -m app.programs.registry add <subject> <predicate> <object> --from YYYY-MM-DD --source URL --reviewer NAME
    python -m app.programs.registry end <relation_id> --to YYYY-MM-DD --reviewer NAME
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone

FORMAL_PREDICATES = {
    "holds_post": "擔任",
    "member_of": "隸屬",
    "leads": "領導",
    "nominated_for": "獲提名",
    "caucus_member": "黨團成員",
}
SENSITIVE_PREDICATES = {
    "faction_of": "派系",
    "informal_ally": "非正式結盟",
    "alleged_tie": "傳聞關係",
}
PREDICATES = {**FORMAL_PREDICATES, **SENSITIVE_PREDICATES}
KINDS = ("person", "organization", "party", "post")
STATUSES = ("proposed", "confirmed", "rejected")


def ensure_tables(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS entities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            name TEXT NOT NULL,
            aliases_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT NOT NULL,
            UNIQUE(kind, name)
        );
        CREATE TABLE IF NOT EXISTS relations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            subject_id INTEGER NOT NULL,
            predicate TEXT NOT NULL,
            object_id INTEGER NOT NULL,
            valid_from TEXT,
            valid_to TEXT,
            source_claim_id INTEGER,
            source_url TEXT,
            status TEXT NOT NULL DEFAULT 'proposed',
            sensitive INTEGER NOT NULL,
            reviewer TEXT,
            reviewed_at TEXT,
            note TEXT,
            created_at TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_relations_subject ON relations(subject_id);
        CREATE INDEX IF NOT EXISTS idx_relations_object ON relations(object_id);
        CREATE UNIQUE INDEX IF NOT EXISTS idx_relations_claim
            ON relations(source_claim_id, predicate) WHERE source_claim_id IS NOT NULL;
        """
    )
    conn.commit()


def _now(now: datetime | None) -> str:
    return (now or datetime.now(timezone.utc)).isoformat()


def upsert_entity(conn: sqlite3.Connection, kind: str, name: str, *, aliases: list[str] | None = None,
                  now: datetime | None = None) -> int:
    if kind not in KINDS:
        raise ValueError(f"kind must be one of {KINDS}")
    name = name.strip()
    if not name:
        raise ValueError("entity name required")
    ensure_tables(conn)
    row = conn.execute("SELECT id, aliases_json FROM entities WHERE kind = ? AND name = ?", (kind, name)).fetchone()
    if row:
        if aliases:
            merged = sorted(set(json.loads(row[1])) | set(aliases))
            conn.execute("UPDATE entities SET aliases_json = ? WHERE id = ?", (json.dumps(merged, ensure_ascii=False), row[0]))
            conn.commit()
        return row[0]
    cur = conn.execute(
        "INSERT INTO entities(kind, name, aliases_json, created_at) VALUES (?, ?, ?, ?)",
        (kind, name, json.dumps(sorted(set(aliases or [])), ensure_ascii=False), _now(now)),
    )
    conn.commit()
    return cur.lastrowid


def find_entity(conn: sqlite3.Connection, name: str) -> list[dict]:
    ensure_tables(conn)
    out = []
    for eid, kind, ename, aliases in conn.execute("SELECT id, kind, name, aliases_json FROM entities"):
        if name == ename or name in json.loads(aliases):
            out.append({"id": eid, "kind": kind, "name": ename})
    return out


def add_relation(conn: sqlite3.Connection, subject_id: int, predicate: str, object_id: int, *,
                 valid_from: str | None = None, valid_to: str | None = None, source_claim_id: int | None = None,
                 source_url: str | None = None, status: str = "proposed", reviewer: str | None = None,
                 note: str | None = None, now: datetime | None = None) -> int | None:
    """Insert a relation; returns its id, or None if the same claim already proposed it."""
    if predicate not in PREDICATES:
        raise ValueError(f"unknown predicate {predicate}")
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}")
    if not (source_claim_id or source_url):
        raise ValueError("a relation needs a source (claim id or URL)")
    for d in (valid_from, valid_to):
        if d:
            date.fromisoformat(d)
    ensure_tables(conn)
    try:
        cur = conn.execute(
            "INSERT INTO relations(subject_id, predicate, object_id, valid_from, valid_to, source_claim_id, "
            "source_url, status, sensitive, reviewer, reviewed_at, note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (subject_id, predicate, object_id, valid_from, valid_to, source_claim_id, source_url, status,
             int(predicate in SENSITIVE_PREDICATES), reviewer, _now(now) if reviewer else None, note, _now(now)),
        )
    except sqlite3.IntegrityError:
        return None
    conn.commit()
    return cur.lastrowid


def review(conn: sqlite3.Connection, relation_id: int, status: str, reviewer: str, note: str | None = None,
           now: datetime | None = None) -> bool:
    if status not in ("confirmed", "rejected"):
        raise ValueError("status must be confirmed or rejected")
    ensure_tables(conn)
    cur = conn.execute("UPDATE relations SET status = ?, reviewer = ?, reviewed_at = ?, note = COALESCE(?, note) "
                       "WHERE id = ?", (status, reviewer, _now(now), note, relation_id))
    conn.commit()
    return cur.rowcount == 1


def end_relation(conn: sqlite3.Connection, relation_id: int, valid_to: str, reviewer: str,
                 now: datetime | None = None) -> bool:
    date.fromisoformat(valid_to)
    ensure_tables(conn)
    cur = conn.execute("UPDATE relations SET valid_to = ?, reviewer = ?, reviewed_at = ? WHERE id = ?",
                       (valid_to, reviewer, _now(now), relation_id))
    conn.commit()
    return cur.rowcount == 1


def relations_for(conn: sqlite3.Connection, entity_id: int, *, at: date | None = None,
                  include_unreviewed_sensitive: bool = False, include_rejected: bool = False) -> list[dict]:
    """Relations of an entity (either side), valid at ``at`` when given.

    Sensitive relations appear only once confirmed, unless explicitly requested
    for review.
    """
    ensure_tables(conn)
    where = ["(r.subject_id = ? OR r.object_id = ?)"]
    params: list = [entity_id, entity_id]
    if at is not None:
        where.append("(r.valid_from IS NULL OR r.valid_from <= ?) AND (r.valid_to IS NULL OR r.valid_to >= ?)")
        params += [at.isoformat(), at.isoformat()]
    if not include_rejected:
        where.append("r.status != 'rejected'")
    if not include_unreviewed_sensitive:
        where.append("(r.sensitive = 0 OR r.status = 'confirmed')")
    cur = conn.execute(
        f"""
        SELECT r.id, s.name, r.predicate, o.name, r.valid_from, r.valid_to, r.status, r.sensitive,
               r.source_claim_id, r.source_url
        FROM relations r JOIN entities s ON s.id = r.subject_id JOIN entities o ON o.id = r.object_id
        WHERE {' AND '.join(where)}
        ORDER BY COALESCE(r.valid_from, ''), r.id
        """,
        params,
    )
    keys = ("id", "subject", "predicate", "object", "valid_from", "valid_to", "status", "sensitive",
            "source_claim_id", "source_url")
    return [dict(zip(keys, row)) for row in cur.fetchall()]


def seed_from_social(conn: sqlite3.Connection, now: datetime | None = None) -> int:
    """Create person entities for the social monitor's political persons (if that table exists)."""
    try:
        rows = conn.execute("SELECT canonical_name, display_name FROM political_person").fetchall()
    except sqlite3.OperationalError:
        return 0
    count = 0
    for canonical, display in rows:
        if canonical:
            upsert_entity(conn, "person", canonical, aliases=[display] if display and display != canonical else None,
                          now=now)
            count += 1
    return count


def propose_from_claims(conn: sqlite3.Connection, *, since: date, now: datetime | None = None) -> int:
    """Propose holds_post relations from valid, non-allegation appointment claims."""
    from ..pilot.claims import ensure_tables as ensure_claims

    ensure_claims(conn)
    ensure_tables(conn)
    rows = conn.execute(
        "SELECT c.id, c.actor, c.target, c.event_date, c.article_url FROM claims c "
        "WHERE c.claim_type = 'appointment' AND c.valid = 1 AND c.review_status != 'rejected' "
        "AND (c.kind IS NULL OR c.kind != 'allegation') AND c.actor IS NOT NULL AND c.target IS NOT NULL "
        "AND COALESCE(c.event_date, substr(c.extracted_at, 1, 10)) >= ?",
        (since.isoformat(),),
    ).fetchall()
    created = 0
    for claim_id, actor, target, event_date, url in rows:
        person = upsert_entity(conn, "person", actor, now=now)
        post = upsert_entity(conn, "post", target, now=now)
        if add_relation(conn, person, "holds_post", post, valid_from=event_date, source_claim_id=claim_id,
                        source_url=url, now=now) is not None:
            created += 1
    return created


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app.programs.registry")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("seed")
    pr = sub.add_parser("propose")
    pr.add_argument("--days", type=int, default=30)
    ls = sub.add_parser("list")
    ls.add_argument("--entity", required=True)
    ls.add_argument("--at", type=date.fromisoformat)
    ls.add_argument("--all", action="store_true", help="include rejected and unreviewed sensitive relations")
    rv = sub.add_parser("review")
    rv.add_argument("relation_id", type=int)
    rv.add_argument("status", choices=("confirmed", "rejected"))
    rv.add_argument("--reviewer", required=True)
    rv.add_argument("--note")
    ad = sub.add_parser("add")
    ad.add_argument("subject")
    ad.add_argument("predicate", choices=sorted(PREDICATES))
    ad.add_argument("object")
    ad.add_argument("--subject-kind", default="person", choices=KINDS)
    ad.add_argument("--object-kind", default="post", choices=KINDS)
    ad.add_argument("--from", dest="valid_from")
    ad.add_argument("--to", dest="valid_to")
    ad.add_argument("--source", required=True, help="URL of the supporting document")
    ad.add_argument("--reviewer", required=True)
    en = sub.add_parser("end")
    en.add_argument("relation_id", type=int)
    en.add_argument("--to", required=True)
    en.add_argument("--reviewer", required=True)
    for p in (pr, ls, rv, ad, en):
        p.add_argument("--db")
    args = parser.parse_args(argv)
    try:
        sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except (AttributeError, ValueError):
        pass
    from ..database import Database
    from ..settings import get_settings

    with Database(getattr(args, "db", None) or get_settings().news_db_path) as db:
        conn = db.conn
        if args.cmd == "seed":
            print(f"person entities seeded: {seed_from_social(conn)}")
        elif args.cmd == "propose":
            since = datetime.now(timezone.utc).date() - timedelta(days=args.days)
            print(f"relations proposed: {propose_from_claims(conn, since=since)}")
        elif args.cmd == "list":
            matches = find_entity(conn, args.entity)
            if not matches:
                print("no such entity")
                return 1
            for ent in matches:
                print(f"{ent['name']} ({ent['kind']}, #{ent['id']})")
                for r in relations_for(conn, ent["id"], at=args.at, include_unreviewed_sensitive=args.all,
                                       include_rejected=args.all):
                    span = f"{r['valid_from'] or '?'} → {r['valid_to'] or '至今'}"
                    mark = "" if r["status"] == "confirmed" else "（未確認）" if r["status"] == "proposed" else "（已駁回）"
                    print(f"  #{r['id']} {r['subject']} {PREDICATES[r['predicate']]} {r['object']}  {span}{mark}"
                          f"  來源：{r['source_url'] or ('claim#' + str(r['source_claim_id']))}")
        elif args.cmd == "review":
            ok = review(conn, args.relation_id, args.status, args.reviewer, args.note)
            print("updated" if ok else "no such relation")
            return 0 if ok else 1
        elif args.cmd == "add":
            s = upsert_entity(conn, args.subject_kind, args.subject)
            o = upsert_entity(conn, args.object_kind, args.object)
            rid = add_relation(conn, s, args.predicate, o, valid_from=args.valid_from, valid_to=args.valid_to,
                               source_url=args.source, status="confirmed", reviewer=args.reviewer)
            print(f"relation #{rid} added (confirmed by {args.reviewer})")
        else:
            ok = end_relation(conn, args.relation_id, args.to, args.reviewer)
            print("updated" if ok else "no such relation")
            return 0 if ok else 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
