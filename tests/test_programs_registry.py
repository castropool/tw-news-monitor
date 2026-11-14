"""Entity/relation registry: valid time, sources, sensitivity rules."""

from datetime import date, datetime, timezone

import pytest

from app.database import Database
from app.models import Article
from app.pilot import claims as cl
from app.programs import registry as rg

NOW = datetime(2026, 11, 14, 12, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path):
    d = Database(tmp_path / "n.db")
    d.connect()
    yield d
    d.close()


def test_entities_are_unique_and_aliases_merge(db):
    a = rg.upsert_entity(db.conn, "person", "賴清德", aliases=["賴神"], now=NOW)
    b = rg.upsert_entity(db.conn, "person", "賴清德", aliases=["William Lai"], now=NOW)
    assert a == b
    assert rg.find_entity(db.conn, "William Lai")[0]["name"] == "賴清德"
    with pytest.raises(ValueError):
        rg.upsert_entity(db.conn, "planet", "x")


def test_relations_need_a_source_and_valid_dates(db):
    p = rg.upsert_entity(db.conn, "person", "甲", now=NOW)
    o = rg.upsert_entity(db.conn, "post", "行政院長", now=NOW)
    with pytest.raises(ValueError, match="source"):
        rg.add_relation(db.conn, p, "holds_post", o)
    with pytest.raises(ValueError):
        rg.add_relation(db.conn, p, "holds_post", o, valid_from="2026/1/1", source_url="u")
    with pytest.raises(ValueError):
        rg.add_relation(db.conn, p, "best_friend", o, source_url="u")


def test_valid_time_queries(db):
    p = rg.upsert_entity(db.conn, "person", "甲", now=NOW)
    o1 = rg.upsert_entity(db.conn, "post", "部長", now=NOW)
    o2 = rg.upsert_entity(db.conn, "post", "院長", now=NOW)
    r1 = rg.add_relation(db.conn, p, "holds_post", o1, valid_from="2024-05-20", source_url="u1",
                         status="confirmed", reviewer="a")
    rg.end_relation(db.conn, r1, "2025-08-31", "a")
    rg.add_relation(db.conn, p, "holds_post", o2, valid_from="2025-09-01", source_url="u2")
    assert [r["object"] for r in rg.relations_for(db.conn, p, at=date(2025, 1, 1))] == ["部長"]
    assert [r["object"] for r in rg.relations_for(db.conn, p, at=date(2026, 1, 1))] == ["院長"]
    assert len(rg.relations_for(db.conn, p)) == 2


def test_sensitive_relations_hidden_until_confirmed(db):
    p = rg.upsert_entity(db.conn, "person", "甲", now=NOW)
    q = rg.upsert_entity(db.conn, "person", "乙", now=NOW)
    rid = rg.add_relation(db.conn, p, "informal_ally", q, source_url="u")
    assert rg.relations_for(db.conn, p) == []
    assert rg.relations_for(db.conn, p, include_unreviewed_sensitive=True)[0]["sensitive"] == 1
    rg.review(db.conn, rid, "confirmed", "analyst")
    assert rg.relations_for(db.conn, p)[0]["predicate"] == "informal_ally"


def test_proposals_from_appointment_claims_only_and_never_from_allegations(db):
    db.save_articles([Article(source_id="s", source_name="中央社", category="politics", title="t", url="https://x/1",
                              published_at=datetime(2026, 11, 1), fetched_at=datetime(2026, 11, 1), position=1)])
    cl.ensure_tables(db.conn)
    rows = [("appointment", "fact", "王某", "國防部長", 1), ("appointment", "allegation", "李某", "密使", 1),
            ("appointment", "fact", "陳某", "次長", 0), ("statement", "fact", "張某", "院長", 1)]
    for ctype, kind, actor, target, valid in rows:
        db.conn.execute(
            "INSERT INTO claims(article_url, claim_type, actor, target, event_date, quote, source_field, valid, "
            "model, prompt_version, extracted_at, program, kind, ladder) VALUES (?, ?, ?, ?, '2026-11-01', 'q', "
            "'text', ?, 'm', 'v', ?, 'P6', ?, 'decision')", ("https://x/1", ctype, actor, target, valid, NOW.isoformat(), kind))
    db.conn.commit()
    assert rg.propose_from_claims(db.conn, since=date(2026, 10, 1), now=NOW) == 1
    assert rg.propose_from_claims(db.conn, since=date(2026, 10, 1), now=NOW) == 0  # idempotent
    person = rg.find_entity(db.conn, "王某")[0]["id"]
    rel = rg.relations_for(db.conn, person)[0]
    assert (rel["object"], rel["status"], rel["valid_from"]) == ("國防部長", "proposed", "2026-11-01")
    assert rg.find_entity(db.conn, "李某") == []


def test_seed_from_social_monitor(db):
    stamp = NOW.isoformat()
    db.conn.executemany(
        "INSERT INTO political_person(person_id, canonical_name, display_name, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?)",
        [("p1", "賴清德", "賴清德", stamp, stamp), ("p2", "侯友宜", "侯市長", stamp, stamp)],
    )
    db.conn.commit()
    assert rg.seed_from_social(db.conn, now=NOW) == 2
    assert rg.find_entity(db.conn, "侯市長")[0]["name"] == "侯友宜"


def test_cli_add_list_end(db, capsys):
    path = str(db._db_path)
    db.close()
    assert rg.main(["add", "賴清德", "holds_post", "總統", "--from", "2024-05-20", "--source", "https://president.gov.tw",
                    "--reviewer", "analyst", "--db", path]) == 0
    assert rg.main(["list", "--entity", "賴清德", "--at", "2026-01-01", "--db", path]) == 0
    out = capsys.readouterr().out
    assert "賴清德 擔任 總統  2024-05-20 → 至今" in out
    db.connect()
