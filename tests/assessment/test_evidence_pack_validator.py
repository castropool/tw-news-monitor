from datetime import date

from app.assessment.evidence_pack_validator import ValidationContext, validate_evidence_pack


def _dimensions():
    names = [
        "overall_race_structure",
        "chen_tingfei_integration",
        "hsieh_longchieh_organization",
        "kmt_tpp_cooperation",
        "poll_status",
        "governance_issues",
        "known_limitations",
    ]
    return [
        {
            "dimension": n,
            "previous_status": "",
            "current_status": "",
            "change_status": "unchanged",
            "change_scope": [],
            "changed_paths": [],
            "material_for_report": False,
            "material_change_summary": "",
            "evidence_only_change_summary": "",
            "limitations_change_summary": "",
        }
        for n in names
    ]


def _pack(**kwargs):
    base = {
        "schema_version": "1.0",
        "election_id": "e",
        "election_name": "n",
        "report_period": {
            "timezone": "Asia/Taipei",
            "run_at": "",
            "run_date": "2026-08-01",
            "resolution_mode": "scheduled",
            "period_start": "2026-07-16",
            "period_end": "2026-07-31",
            "period_label": "",
            "previous_period_start": "2026-07-01",
            "previous_period_end": "2026-07-15",
            "period_complete": True,
        },
        "data_status": {
            "facts_cutoff": "2026-07-27",
            "poll_cutoff": "2026-03-12",
            "coverage_version": "fact_coverage_20260801_v4",
            "active_snapshot_id": "active",
            "formal_event_count": 1,
            "formal_source_count": 1,
            "formal_link_count": 1,
            "formal_poll_count": 0,
            "report_period_fully_covered_by_facts": False,
            "uncovered_date_range": [],
        },
        "current_snapshot": {
            "snapshot_id": "active",
            "state": {"coverage": {"known_gaps": ["k1"]}, "structural_lean": {"value": "DPP"}},
        },
        "previous_snapshot": {"snapshot_id": "prev", "state": {}},
        "state_diff": {
            "status": "unchanged",
            "state_diff_mode": "structured_comparison",
            "dimensions": _dimensions(),
            "changed_dimensions": [],
            "unchanged_dimensions": ["overall_race_structure"],
            "new_risks": [],
            "resolved_gaps": [],
            "new_gaps": [],
            "confidence_changes": [],
            "snapshot_evidence_reference_additions": [],
            "snapshot_evidence_reference_removals": [],
            "risk_changes": [],
        },
        "generation_eligibility": {
            "evidence_pack_ready": True,
            "report_period_fully_covered_by_facts": False,
            "final_report_allowed": False,
            "allowed_generation_mode": "draft_with_data_gap",
            "blocking_reasons": [],
            "warnings": [],
            "required_disclosures": ["a", "b", "c"],
        },
        "research_task_status_reconciliation": {"reconciliation_ready": True},
        "snapshot_evidence_changes": {
            "event_reference_additions": [],
            "event_reference_removals": [],
            "poll_reference_additions": [],
            "poll_reference_removals": [],
            "formal_events_deleted": [],
            "formal_polls_deleted": [],
            "reconciliation_ready": True,
        },
        "gap_changes": [],
        "risk_changes": [],
        "period_events": [],
        "background_events": [],
        "sources": [],
        "polls": [],
        "known_limitations": ["k1"],
        "do_not_infer": ["d1"],
        "evidence_statistics": {"poll_gap": True},
    }
    base.update(kwargs)
    return base


def _ctx(**kwargs):
    base = dict(
        formal_event_ids={"e1"},
        formal_source_ids={"s1"},
        formal_link_pairs={("e1", "s1")},
        formal_poll_ids=set(),
        blocked_ids=set(),
        active_snapshot_id="active",
        previous_snapshot_id="prev",
        coverage_name="fact_coverage_20260801_v4",
        facts_cutoff="2026-07-27",
        poll_cutoff="2026-03-12",
        expected_counts={
            "formal_event_count": 1,
            "formal_source_count": 1,
            "formal_link_count": 1,
            "formal_poll_count": 0,
        },
        before_hashes={"a": "h"},
        after_hashes={"a": "h"},
        period_start=date(2026, 7, 16),
        period_end=date(2026, 7, 31),
        max_background_total=15,
        authoritative_active_task_ids=[],
        llm_contract_validation={"llm_input_contract_ready": True},
    )
    base.update(kwargs)
    return ValidationContext(**base)


class TestValidator:
    def test_valid_pack_ready(self):
        v = validate_evidence_pack(_pack(), _ctx())
        assert v["evidence_pack_ready"] is True
        assert v["errors"] == []

    def test_missing_limitations_fails(self):
        v = validate_evidence_pack(_pack(known_limitations=[]), _ctx())
        assert v["limitations_present"] is False
        assert v["evidence_pack_ready"] is False

    def test_missing_do_not_infer_fails(self):
        v = validate_evidence_pack(_pack(do_not_infer=[]), _ctx())
        assert v["do_not_infer_present"] is False

    def test_unknown_event_id_fails(self):
        pack = _pack(
            period_events=[
                {"event_id": "e_unknown", "evidence_role": "period_event",
                 "inclusion_reasons": ["event_date_in_period"]}
            ]
        )
        v = validate_evidence_pack(pack, _ctx())
        assert v["all_event_ids_exist"] is False

    def test_orphan_source_fails(self):
        pack = _pack(
            sources=[{"source_id": "s1", "is_formal_source": True, "linked_event_ids": []}]
        )
        v = validate_evidence_pack(pack, _ctx())
        assert v["no_orphan_sources"] is False

    def test_blocked_hold_id_fails(self):
        pack = _pack(
            period_events=[
                {"event_id": "e1", "evidence_role": "period_event",
                 "inclusion_reasons": ["event_date_in_period"], "source_ids": ["s1"]}
            ],
            sources=[{"source_id": "s1", "is_formal_source": True, "linked_event_ids": ["e1"]}],
        )
        v = validate_evidence_pack(pack, _ctx(blocked_ids={"e1"}))
        assert v["no_hold_records"] is False
        assert v["evidence_pack_ready"] is False

    def test_state_diff_forbidden_inference_fails(self):
        sd = {
            "status": "changed",
            "state_diff_mode": "structured_comparison",
            "changed_dimensions": [{"dimension": "x", "from": 1, "to": 2}],
            "unchanged_dimensions": [],
            "new_risks": [],
            "resolved_gaps": [],
            "new_gaps": [],
            "confidence_changes": [],
            "evidence_additions": [],
            "evidence_removals": [],
            "胜选概率": 0.8,
        }
        v = validate_evidence_pack(_pack(state_diff=sd), _ctx())
        assert v["state_diff_valid"] is False

    def test_period_beyond_facts_cutoff_is_warning_not_error(self):
        v = validate_evidence_pack(_pack(), _ctx())
        assert v["evidence_pack_ready"] is True
        assert any("facts_cutoff" in w for w in v["warnings"])
        assert v["facts_cutoff_disclosed"] is True

    def test_formal_hashes_changed_fails(self):
        v = validate_evidence_pack(_pack(), _ctx(after_hashes={"a": "h2"}))
        assert v["formal_data_unchanged"] is False
        assert v["evidence_pack_ready"] is False

    def test_poll_trend_without_methodology_fails(self):
        pack = _pack(
            polls=[
                {"poll_id": "p1", "trend_eligible": True, "methodology_complete": False,
                 "recommended_disposition": ""}
            ]
        )
        v = validate_evidence_pack(pack, _ctx(formal_poll_ids={"p1"}))
        assert v["polls_valid"] is False
