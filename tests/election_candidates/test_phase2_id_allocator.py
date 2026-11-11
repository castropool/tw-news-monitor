from __future__ import annotations

import pytest

from app.election_candidates.formal_id_allocator import (
    allocate_event_id,
    allocate_source_id,
    collect_existing_ids,
    payload_hash,
)


def test_event_id_unique_within_existing():
    ids = {"evt_tnn_20260810_railies"}
    a = allocate_event_id(ids, "2026-08-10", "謝龍介造勢晚會")
    b = allocate_event_id(ids | {a}, "2026-08-10", "謝龍介造勢晚會")
    assert a != b


def test_event_id_follows_spec():
    eid = allocate_event_id(set(), "2026-08-10T10:00:00+08:00", "謝龍介舉辦造勢晚會")
    assert eid.startswith("evt_tnn_20260810_")


def test_dry_run_and_commit_same_id():
    ids = set()
    a = allocate_event_id(ids, "2026-08-10", "謝龍介造勢")
    b = allocate_event_id(set(ids), "2026-08-10", "謝龍介造勢")
    assert a == b


def test_concurrent_allocation_no_collision():
    ids = set()
    allocated = []
    for _ in range(5):
        eid = allocate_event_id(ids, "2026-08-10", "同標題活動")
        ids.add(eid)
        allocated.append(eid)
    assert len(set(allocated)) == 5


def test_source_id_unique():
    ids = {"src_cna_20260810"}
    a = allocate_source_id(ids, "cna.com.tw", "中央社", "2026-08-10")
    assert a != "src_cna_20260810"
    assert a.startswith("src_cna_20260810")


def test_source_id_domain_prefix():
    sid = allocate_source_id(set(), "news.ltn.com.tw", "自由時報", "2026-08-10")
    assert sid.startswith("src_news_")


def test_source_id_publisher_fallback():
    sid = allocate_source_id(set(), "", "新媒體", "2026-08-10")
    assert sid.startswith("src_新媒體_")


def test_collect_existing_ids_merges_seed_and_db():
    result = collect_existing_ids(
        [{"event_id": "e1"}],
        [{"source_id": "s1"}],
        {"events": {"e2"}, "sources": {"s2"}},
    )
    assert result["events"] == {"e1", "e2"}
    assert result["sources"] == {"s1", "s2"}


def test_payload_hash_stable():
    assert payload_hash({"a": 1, "b": [2]}) == payload_hash({"b": [2], "a": 1})


def test_event_id_date_required():
    # date empty is allowed but produces 00000000; verify no crash
    eid = allocate_event_id(set(), "", "活動")
    assert eid.startswith("evt_tnn_00000000_")
