import json

from app.assessment.state_diff import diff_snapshots


def _snapshot(**overrides):
    base = {
        "snapshot_id": "s",
        "coverage": {"known_gaps": ["gapA", "gapB"]},
        "structural_lean": {"value": "DPP", "confidence": 0.7},
        "competitiveness": {"value": "x", "confidence": 0.6},
        "dpp_integration": {"formal_status": "formal_complete", "organizational_status": "partial", "confidence": 0.8},
        "kmt_organization": {"status": "nominee_confirmed", "confidence": 0.8},
        "kmt_tpp_cooperation": {"status": "proposed", "formal_agreement": False, "confidence": 0.9},
        "public_poll_assessment": {"status": "chen_leads"},
        "polling_evidence": {"latest_field_end": "2026-03-12", "poll_count": 15},
        "core_issues": [{"issue": "i1", "status": "ongoing"}],
        "key_risks": [{"risk": "r1", "risk_type": "t1"}],
        "supporting_event_ids": ["e1"],
        "supporting_poll_ids": ["p1"],
        "milestone_events": ["e1"],
    }
    base.update(overrides)
    return base


class TestStateDiff:
    def test_changed_dimension_detected(self):
        cur = _snapshot()
        prev = _snapshot()
        prev["competitiveness"] = {"value": "y", "confidence": 0.5}
        d = diff_snapshots(cur, prev)
        assert d["status"] == "changed"
        assert "overall_race_structure" in d["changed_dimensions"]
        dim = next(x for x in d["dimensions"] if x["dimension"] == "overall_race_structure")
        assert "business_state" in dim["change_scope"]
        assert dim["material_for_report"] is True

    def test_unchanged_dimensions_marked(self):
        cur = _snapshot()
        prev = _snapshot()
        d = diff_snapshots(cur, prev)
        assert d["status"] == "unchanged"
        assert "kmt_tpp_cooperation" in d["unchanged_dimensions"]

    def test_confidence_changes(self):
        cur = _snapshot()
        prev = _snapshot()
        prev["dpp_integration"]["confidence"] = 0.7
        d = diff_snapshots(cur, prev)
        dim = next(
            x for x in d["dimensions"] if x["dimension"] == "chen_tingfei_integration"
        )
        assert "confidence" in dim["change_scope"]
        assert dim["material_for_report"] is True

    def test_evidence_additions_and_removals(self):
        cur = _snapshot()
        prev = _snapshot()
        prev["supporting_event_ids"] = ["e0", "e1"]
        cur["supporting_event_ids"] = ["e1", "e2"]
        d = diff_snapshots(cur, prev)
        assert "event:e2" in d["snapshot_evidence_reference_additions"]
        assert "event:e0" in d["snapshot_evidence_reference_removals"]

    def test_no_previous_snapshot_initial_baseline(self):
        d = diff_snapshots(_snapshot(), None)
        assert d["state_diff_mode"] == "initial_baseline"
        assert d["changed_dimensions"] == []

    def test_no_win_probability_generated(self):
        d = diff_snapshots(_snapshot(), _snapshot())
        text = json.dumps(d, ensure_ascii=False)
        assert "win_probability" not in text
        assert "胜选概率" not in text
        assert "support_rate" not in text

    def test_timestamp_only_change_is_metadata(self):
        cur = _snapshot()
        prev = _snapshot()
        prev["generated_at"] = "2026-01-01"
        cur["generated_at"] = "2026-08-01"
        d = diff_snapshots(cur, prev)
        assert d["status"] == "unchanged"
        assert d["changed_dimensions"] == []

    def test_array_order_only_no_change(self):
        cur = _snapshot()
        prev = _snapshot()
        prev["core_issues"] = [{"issue": "i1", "status": "ongoing"}]
        cur["core_issues"] = [{"issue": "i1", "status": "ongoing"}]
        prev["core_issues"] = [{"issue": "i2", "status": "x"}, {"issue": "i1", "status": "ongoing"}]
        cur["core_issues"] = [{"issue": "i1", "status": "ongoing"}, {"issue": "i2", "status": "x"}]
        d = diff_snapshots(cur, prev)
        dim = next(x for x in d["dimensions"] if x["dimension"] == "governance_issues")
        assert dim["change_status"] == "unchanged"

    def test_limitations_change_not_material(self):
        cur = _snapshot()
        prev = _snapshot()
        prev["coverage"]["known_gaps"] = ["gapA"]
        cur["coverage"]["known_gaps"] = ["gapA", "gapB"]
        d = diff_snapshots(cur, prev)
        dim = next(x for x in d["dimensions"] if x["dimension"] == "known_limitations")
        assert "limitations" in dim["change_scope"]
        assert dim["material_for_report"] is False

    def test_evidence_only_change_not_material(self):
        cur = _snapshot()
        prev = _snapshot()
        prev["competitiveness"]["supporting_event_ids"] = ["e1"]
        cur["competitiveness"]["supporting_event_ids"] = ["e1", "e2"]
        d = diff_snapshots(cur, prev)
        dim = next(x for x in d["dimensions"] if x["dimension"] == "overall_race_structure")
        assert "evidence_support" in dim["change_scope"]
        assert dim["material_for_report"] is False
