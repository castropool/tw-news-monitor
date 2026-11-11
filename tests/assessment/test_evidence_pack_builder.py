import json
from datetime import date
from pathlib import Path

import pytest

from app.assessment.evidence_pack_builder import (
    EvidencePackError,
    FormalData,
    build_pack,
    build_active_research_tasks,
    build_coverage_gaps,
    build_do_not_infer,
    build_known_limitations,
    collect_blocked_ids,
    collect_sources_for_events,
    extract_period_events,
    include_polls,
    normalize_event,
    select_active_snapshot,
    select_background_events,
    select_coverage_version,
    select_previous_snapshot,
    uncovered_range,
)
from app.assessment.reporting_period import ReportingPeriod


def _event(eid, event_date, subevents=None, sources=None, sig=50):
    return {
        "event_id": eid,
        "election_id": "TW-2026-TNN-MAYOR",
        "event_date": event_date,
        "occurred_at": f"{event_date}T00:00:00+08:00",
        "event_type": "test",
        "title": eid,
        "fact_summary": "",
        "fact_status": "verified",
        "significance_score": sig,
        "verified_facts": [],
        "actor_statements": [],
        "media_interpretations": [],
        "analytical_significance": "",
        "limitations": [],
        "mentions": [],
        "source_ids": sources or [],
        "subevents": subevents or [],
    }


def _active_snapshot(event_ids=None):
    return {"snapshot_id": "active", "state": {"supporting_event_ids": event_ids or []}}


PERIOD_START = date(2026, 7, 16)
PERIOD_END = date(2026, 7, 31)


class TestPeriodEvents:
    def test_event_date_in_period(self):
        events = {"e1": _event("e1", "2026-07-20")}
        out = extract_period_events(events, PERIOD_START, PERIOD_END, _active_snapshot())
        assert len(out) == 1
        assert out[0]["evidence_role"] == "period_event"
        assert "event_date_in_period" in out[0]["inclusion_reasons"]

    def test_subevent_in_period_with_main_outside(self):
        events = {
            "e1": _event(
                "e1",
                "2026-05-04",
                subevents=[{"subevent_date": "2026-07-22", "description": "x"}],
            )
        }
        out = extract_period_events(events, PERIOD_START, PERIOD_END, _active_snapshot())
        assert len(out) == 1
        assert "subevent_date_in_period" in out[0]["inclusion_reasons"]
        assert out[0]["in_period_subevents"][0]["subevent_date"] == "2026-07-22"

    def test_main_and_subevent_no_duplicate(self):
        events = {
            "e1": _event(
                "e1",
                "2026-07-20",
                subevents=[{"subevent_date": "2026-07-22", "description": "x"}],
            )
        }
        out = extract_period_events(events, PERIOD_START, PERIOD_END, _active_snapshot())
        assert len(out) == 1
        assert set(out[0]["inclusion_reasons"]) == {
            "event_date_in_period",
            "subevent_date_in_period",
        }

    def test_outside_period_excluded(self):
        events = {"e1": _event("e1", "2026-06-01")}
        out = extract_period_events(events, PERIOD_START, PERIOD_END, _active_snapshot())
        assert out == []

    def test_active_snapshot_reference_noted(self):
        events = {"e1": _event("e1", "2026-07-20")}
        out = extract_period_events(
            events, PERIOD_START, PERIOD_END, _active_snapshot(["e1"])
        )
        assert "active_snapshot_evidence" in out[0]["inclusion_reasons"]

    def test_active_reference_alone_is_not_period_event(self):
        events = {"e_old": _event("e_old", "2026-01-15")}
        out = extract_period_events(
            events, PERIOD_START, PERIOD_END, _active_snapshot(["e_old"])
        )
        assert out == []


class TestBackgroundEvents:
    def test_background_marked_and_ranked(self):
        events = {
            "e_old1": _event("e_old1", "2026-01-15", sig=90),
            "e_old2": _event("e_old2", "2026-02-01", sig=80),
            "e_period": _event("e_period", "2026-07-20"),
        }
        active = {
            "snapshot_id": "active",
            "state": {
                "supporting_event_ids": ["e_old1", "e_old2", "e_period"],
                "milestone_events": ["e_old1"],
                "dpp_integration": {"supporting_event_ids": ["e_old1"]},
            },
        }
        out = select_background_events(
            events,
            {"e_period"},
            active,
            None,
            {
                "background": {
                    "max_total": 10,
                    "max_per_mainline": 10,
                    "mainlines": [
                        {
                            "key": "chen_tingfei_integration",
                            "label": "陈亭妃整合",
                            "snapshot_field": "dpp_integration",
                        }
                    ],
                }
            },
        )
        ids = [e["event_id"] for e in out]
        assert "e_period" not in ids
        assert set(ids) == {"e_old1", "e_old2"}
        assert all(e["evidence_role"] == "background" for e in out)
        assert "active_snapshot_reference" in out[0]["ranking_basis"]
        assert any("mainline:chen_tingfei_integration" in e["ranking_basis"] for e in out)

    def test_background_total_cap(self):
        events = {f"e{i}": _event(f"e{i}", f"2026-01-{i:02d}", sig=50) for i in range(1, 21)}
        active = {
            "snapshot_id": "active",
            "state": {
                "supporting_event_ids": set(events),
                "milestone_events": [],
                "dpp_integration": {},
                "kmt_organization": {},
                "kmt_tpp_cooperation": {},
            },
        }
        out = select_background_events(
            events, set(), active, None, {"background": {"max_total": 5, "max_per_mainline": 2}}
        )
        assert len(out) == 5


class TestSourcesAndPolls:
    def test_sources_collected_with_links(self):
        events = [
            {**_event("e1", "2026-07-20"), "source_ids": ["s1", "s2"]},
            {**_event("e2", "2026-07-21"), "source_ids": ["s1"]},
        ]
        sources = {
            "s1": {"source_id": "s1", "publisher": "p", "title": "t", "url": "u"},
            "s2": {"source_id": "s2", "publisher": "p", "title": "t", "url": "u"},
        }
        links = {("e1", "s1"), ("e1", "s2"), ("e2", "s1")}
        out, ids = collect_sources_for_events(events, sources, links)
        assert ids == ["s1", "s2"]
        assert out[0]["linked_event_ids"] == ["e1", "e2"]
        assert all(s["is_formal_source"] for s in out)

    def test_orphan_source_not_included(self):
        events = [{**_event("e1", "2026-07-20"), "source_ids": ["s1"]}]
        sources = {"s1": {"source_id": "s1"}, "s2": {"source_id": "s2"}}
        links = {("e1", "s1")}
        out, ids = collect_sources_for_events(events, sources, links)
        assert ids == ["s1"]
        assert "s2" not in ids

    def test_poll_release_date_in_period(self):
        polls = [{"poll_id": "p1", "release_date": "2026-07-20", "fieldwork_end": "2026-07-10"}]
        out, gap, period_count, context_count = include_polls(
            polls, PERIOD_START, PERIOD_END, _active_snapshot()
        )
        assert period_count == 1
        assert out[0]["evidence_role"] == "period_poll"
        assert gap is False

    def test_poll_field_end_in_period(self):
        polls = [{"poll_id": "p1", "release_date": "2026-08-02", "fieldwork_end": "2026-07-25"}]
        out, gap, period_count, context_count = include_polls(
            polls, PERIOD_START, PERIOD_END, _active_snapshot()
        )
        assert period_count == 1
        assert "fieldwork_end_in_period" in out[0]["inclusion_reasons"]

    def test_poll_gap_when_none_in_period(self):
        polls = [{"poll_id": "p1", "release_date": "2026-03-01", "fieldwork_end": "2026-02-28"}]
        out, gap, period_count, context_count = include_polls(
            polls, PERIOD_START, PERIOD_END, _active_snapshot()
        )
        assert out == []
        assert gap is True
        assert period_count == 0

    def test_snapshot_referenced_poll_is_context(self):
        active = {"snapshot_id": "a", "state": {"supporting_poll_ids": ["p1"]}}
        polls = [{"poll_id": "p1", "release_date": "2026-03-01", "fieldwork_end": "2026-02-28"}]
        out, gap, period_count, context_count = include_polls(
            polls, PERIOD_START, PERIOD_END, active
        )
        assert out[0]["evidence_role"] == "context_poll"
        assert "active_snapshot_reference" in out[0]["inclusion_reasons"]
        assert period_count == 0


class TestNormalizeEvent:
    def test_subevents_extracted_from_enrichment(self):
        analysis = {
            "enrich_rt04": {
                "subevents": ["2026-05-05", "2026-07-22"],
                "added_facts": [{"subevent_date": "2026-07-22", "fact": "f"}],
            }
        }
        ev = {
            "event_id": "e1",
            "election_id": "E",
            "occurred_at": "2026-05-04T00:00:00+08:00",
            "event_type": "x",
            "title": "t",
            "analysis_json": json.dumps(analysis, ensure_ascii=False),
            "sources": [{"source_id": "s1"}],
        }
        norm = normalize_event(ev)
        dates = {s["subevent_date"] for s in norm["subevents"]}
        assert "2026-07-22" in dates
        assert norm["source_ids"] == ["s1"]

    def test_verified_facts_normalized_to_text(self):
        analysis = {"verified_facts": [{"fact": "f1"}, "f2"]}
        ev = {
            "event_id": "e1",
            "election_id": "E",
            "occurred_at": "2026-05-04T00:00:00+08:00",
            "event_type": "x",
            "title": "t",
            "analysis_json": json.dumps(analysis, ensure_ascii=False),
            "sources": [],
        }
        norm = normalize_event(ev)
        assert norm["verified_facts"] == ["f1", "f2"]

    def test_structured_assertions_preserve_existing_fact_and_statement_metadata(self):
        analysis = {
            "verified_facts": [
                {"fact_id": "f1", "fact": "甲举行记者会", "source_ids": ["s1"], "confidence": 0.99}
            ],
            "candidate_claims": [
                {"claim_id": "c1", "speaker": "甲", "claim": "乙确有问题", "source_ids": ["s1"], "confidence": 0.8}
            ],
            "research_claims": [
                {"speaker": "甲", "claim": "合作已有进展", "claim_status": "candidate_claim_unverified", "source_ids": ["s1"]}
            ],
            "media_interpretations": [
                {"interpretation_id": "m1", "interpretation": "媒体认为攻防升温", "source_ids": ["s1"]}
            ],
        }
        ev = {
            "event_id": "e1",
            "election_id": "E",
            "occurred_at": "2026-05-04T00:00:00+08:00",
            "event_type": "campaign_attack",
            "title": "t",
            "analysis_json": json.dumps(analysis, ensure_ascii=False),
            "sources": [{"source_id": "s1"}],
        }
        assertions = normalize_event(ev)["evidence_assertions"]
        by_type = {item["assertion_type"]: item for item in assertions}
        assert by_type["observed_fact"]["assertion_id"] == "f1"
        assert by_type["allegation"]["speaker"] == "甲"
        assert by_type["allegation"]["source_ids"] == ["s1"]
        assert by_type["actor_statement"]["assertion_status"] == "candidate_claim_unverified"
        assert by_type["media_interpretation"]["assertion_id"] == "m1"


class TestSnapshotsAndCoverage:
    def test_active_snapshot_unique(self):
        snaps = [
            {"snapshot_id": "a", "snapshot_status": "active"},
        ]
        assert select_active_snapshot(snaps)["snapshot_id"] == "a"

    def test_multiple_active_fails(self):
        snaps = [
            {"snapshot_id": "a", "snapshot_status": "active"},
            {"snapshot_id": "b", "snapshot_status": "active"},
        ]
        with pytest.raises(EvidencePackError):
            select_active_snapshot(snaps)

    def test_no_active_fails(self):
        with pytest.raises(EvidencePackError):
            select_active_snapshot([])

    def test_previous_via_superseded_by_chain(self):
        snaps = [
            {"snapshot_id": "prev", "snapshot_status": "superseded", "superseded_by": "active",
             "created_at": "2026-01-01T00:00:00+08:00"},
            {"snapshot_id": "older", "snapshot_status": "superseded", "superseded_by": "prev",
             "created_at": "2025-01-01T00:00:00+08:00"},
            {"snapshot_id": "active", "snapshot_status": "active", "superseded_by": None,
             "created_at": "2026-08-01T00:00:00+08:00", "state": {}},
        ]
        active = select_active_snapshot(snaps)
        prev, basis = select_previous_snapshot(active, snaps)
        assert prev["snapshot_id"] == "prev"
        assert basis == "superseded_by_chain"

    def test_previous_fallback_by_created_at_not_id(self):
        snaps = [
            {"snapshot_id": "zzz_old", "snapshot_status": "superseded", "superseded_by": None,
             "created_at": "2026-01-01T00:00:00+08:00"},
            {"snapshot_id": "aaa_newer", "snapshot_status": "superseded", "superseded_by": None,
             "created_at": "2026-07-01T00:00:00+08:00"},
            {"snapshot_id": "active", "snapshot_status": "active", "superseded_by": None,
             "created_at": "2026-08-01T00:00:00+08:00", "state": {}},
        ]
        active = select_active_snapshot(snaps)
        prev, basis = select_previous_snapshot(active, snaps)
        assert prev["snapshot_id"] == "aaa_newer"
        assert basis == "latest_created_at_before_active"

    def test_previous_none(self):
        snaps = [
            {"snapshot_id": "active", "snapshot_status": "active", "superseded_by": None,
             "created_at": "2026-08-01T00:00:00+08:00", "state": {}},
        ]
        active = select_active_snapshot(snaps)
        prev, basis = select_previous_snapshot(active, snaps)
        assert prev is None
        assert basis == "no_previous_snapshot"

    def test_coverage_version_highest_ready(self, tmp_path):
        _write_coverage(tmp_path, "fact_coverage_20260727_v3", ready=True)
        _write_coverage(tmp_path, "fact_coverage_20260801_v4", ready=True)
        _write_coverage(tmp_path, "fact_coverage_20260801_v4_blocked_backup", ready=True)
        path, name, preflight, validation = select_coverage_version(tmp_path)
        assert name == "fact_coverage_20260801_v4"

    def test_candidate_or_invalid_coverage_not_used(self, tmp_path):
        _write_coverage(tmp_path, "fact_coverage_20260801_v4", ready=False)
        with pytest.raises(EvidencePackError):
            select_coverage_version(tmp_path)

    def test_blocked_ids_collected(self, tmp_path):
        neg_dir = tmp_path / "negative_findings"
        neg_dir.mkdir()
        (neg_dir / "rt01.json").write_text(
            json.dumps({"event_id": "e_neg", "poll_id": "p_neg"}), encoding="utf-8"
        )
        (tmp_path / "normal.json").write_text(
            json.dumps({"event_id": "e_ok"}), encoding="utf-8"
        )
        blocked = collect_blocked_ids(tmp_path)
        assert "e_neg" in blocked
        assert "p_neg" in blocked
        assert "e_ok" not in blocked


class TestCoverageAndLimits:
    def test_uncovered_range(self):
        assert uncovered_range("2026-07-27", date(2026, 7, 31)) == [
            "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31",
        ]

    def test_uncovered_range_empty_when_covered(self):
        assert uncovered_range("2026-07-31", date(2026, 7, 31)) == []

    def test_coverage_gaps_classifications(self):
        formal = _formal(
            blocker_triage={
                "rt05_danas_typhoon": {"classification": "soft_limitation"},
                "rt06_sanye_budget": {"classification": "soft_limitation"},
                "rt07_feb_mar_gap": {"classification": "non_blocking_gap"},
            },
            gap_reconciliation=[
                {"gap_id": "gap_danas_typhoon", "v2_gap_text": "丹娜丝", "current_status": "unchanged"},
            ],
        )
        gaps = build_coverage_gaps(formal)
        by_id = {g["gap_id"]: g for g in gaps}
        assert by_id["gap_danas_typhoon"]["classification"] == "soft_limitation"
        assert by_id["rt07_feb_mar_gap"]["classification"] == "non_blocking_gap"

    def test_active_research_tasks_exclude_completed(self):
        formal = _formal(
            research_backlog=[
                {"research_task_id": "RT01", "research_status": "completed", "research_priority": "P0"},
                {"research_task_id": "RT05", "research_priority": "P1", "coverage_status": "missing"},
            ]
        )
        tasks = build_active_research_tasks(formal)
        assert [t["research_task_id"] for t in tasks] == ["RT05"]

    def test_known_limitations_and_do_not_infer(self):
        formal = _formal(
            closure_record={"do_not_infer": ["全国协议不等于全市整合"], "remaining_gaps": ["g1"]},
            gap_reconciliation=[{"gap_id": "gap_x", "remaining_gap": "rx"}],
            research_backlog=[{"research_task_id": "RT05", "do_not_assume": ["不要假设"]}],
        )
        state = {"coverage": {"known_gaps": ["k1"]}, "dpp_integration": {"prohibited_conclusions": ["禁止1"]}}
        lims = build_known_limitations(formal, state)
        assert "k1" in lims
        assert "gap_x: rx" in lims
        dnis = build_do_not_infer(formal, state)
        assert "禁止1" in dnis
        assert "全国协议不等于全市整合" in dnis
        assert "RT05: 不要假设" in dnis

    def test_preflight_facts_cutoff_overrides_stale_snapshot_value(self):
        formal = _formal(
            active_snapshot={
                "snapshot_id": "tn_state_stale",
                "election_id": "TW-2026-TNN-MAYOR",
                "as_of": "2026-08-01",
                "created_at": "2026-08-01T00:00:00+08:00",
                "state": {
                    "coverage": {
                        "facts_cutoff": "2026-07-27",
                        "poll_cutoff": "2026-03-12",
                    }
                },
            },
            coverage_preflight={
                "facts_cutoff": "2026-08-08",
                "poll_cutoff": "2026-03-12",
            },
            counts={
                "formal_event_count": 0,
                "formal_source_count": 0,
                "formal_link_count": 0,
                "formal_poll_count": 0,
            },
            coverage_name="fact_coverage_20260809_v5",
        )
        period = ReportingPeriod(
            timezone="Asia/Taipei",
            run_at="2026-08-09T00:00:00+08:00",
            run_date="2026-08-09",
            resolution_mode="scheduled",
            period_start=date(2026, 7, 16),
            period_end=date(2026, 7, 31),
            period_label="2026-07-16至2026-07-31",
            previous_period_start=date(2026, 7, 1),
            previous_period_end=date(2026, 7, 15),
            period_complete=True,
            scheduled_run_date="2026-08-09",
            calendar_lag_days=9,
            full_preparation_days=8,
        )
        config = {
            "election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"},
            "evidence_pack": {"include_background_events": False},
        }
        pack = build_pack(formal, period, config, Path("."))
        assert pack["data_status"]["facts_cutoff"] == "2026-08-08"
        assert pack["data_status"]["report_period_fully_covered_by_facts"] is True
        assert pack["generation_eligibility"]["final_report_allowed"] is True


def _write_coverage(root: Path, name: str, ready: bool) -> None:
    d = root / name
    d.mkdir()
    (d / "coverage_preflight.json").write_text(
        json.dumps({"preflight_ready": ready, "coverage_version": "v"}), encoding="utf-8"
    )
    (d / "coverage_validation.json").write_text(
        json.dumps({"coverage_ready": ready}), encoding="utf-8"
    )


def _formal(**kwargs):
    defaults = {
        "election_id": "TW-2026-TNN-MAYOR",
        "events": {},
        "sources": {},
        "links": set(),
        "polls": [],
        "snapshots": [],
        "fts_count": 0,
        "counts": {},
        "active_snapshot": {"snapshot_id": "active", "state": {}},
        "previous_snapshot": None,
        "snapshot_selection_basis": "",
        "coverage_dir": Path("."),
        "coverage_name": "v",
        "coverage_preflight": {},
        "coverage_validation": {},
        "gap_reconciliation": [],
        "research_backlog": [],
        "closure_record": None,
        "blocker_triage": {},
        "theme_matrix": [],
        "blocked_ids": set(),
    }
    defaults.update(kwargs)
    return FormalData(**defaults)
