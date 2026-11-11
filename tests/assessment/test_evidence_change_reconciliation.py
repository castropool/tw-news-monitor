from app.assessment.evidence_change_reconciliation import reconcile_evidence_references


def _recon(**kw):
    defaults = dict(
        current_snapshot_id="cur",
        previous_snapshot_id="prev",
        current_event_references={"e1", "e2"},
        previous_event_references={"e1", "e3"},
        current_poll_references={"p1"},
        previous_poll_references={"p1", "p2"},
        formal_event_ids={"e1", "e2", "e3"},
        formal_poll_ids={"p1", "p2"},
    )
    defaults.update(kw)
    return reconcile_evidence_references(**defaults)


class TestEvidenceChangeReconciliation:
    def test_event_reference_removal_not_deletion(self):
        r = _recon()
        assert r["event_reference_removals"] == ["e3"]
        assert r["formal_events_deleted"] == []
        assert r["removed_event_references_still_exist_formally"] == ["e3"]

    def test_poll_reference_removal_not_deletion(self):
        r = _recon()
        assert r["poll_reference_removals"] == ["p2"]
        assert r["formal_polls_deleted"] == []
        assert r["removed_poll_references_still_exist_formally"] == ["p2"]

    def test_reference_change_promotion_false(self):
        r = _recon()
        assert r["reference_change_promoted_to_data_deletion"] is False
        assert r["reconciliation_ready"] is True

    def test_missing_formal_record_fails(self):
        r = _recon(formal_event_ids={"e1", "e2"})
        assert r["formal_events_deleted"] == ["e3"]
        assert r["reference_change_promoted_to_data_deletion"] is True
        assert r["reconciliation_ready"] is False

    def test_additions_separated_from_formal_records(self):
        r = _recon()
        assert r["event_reference_additions"] == ["e2"]
        assert r["formal_events_deleted"] == []
        assert "formal_records_added" not in r or r.get("formal_records_added") == []

