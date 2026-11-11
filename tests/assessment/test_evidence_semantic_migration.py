import json
from pathlib import Path

from app.assessment.evidence_semantic_migration import (
    build_migration_report,
    write_migration_report,
)


class TestEvidenceSemanticMigration:
    def test_migration_ready_when_unchanged(self):
        pack = {
            "period_events": [{"event_id": "e1"}],
            "background_events": [],
            "sources": [{"source_id": "s1"}],
            "polls": [],
            "coverage_gaps": [],
            "known_limitations": ["x"],
            "do_not_infer": ["y"],
            "state_diff": {"dimensions": []},
        }
        baseline = {
            "semantic_key_hashes": {
                key: __import__("app.assessment.evidence_pack_builder", fromlist=["canonical_hash"]).canonical_hash(pack[key])
                for key in pack
            }
        }
        report = build_migration_report(pack, baseline)
        assert report["business_evidence_unchanged"] is True
        assert report["data_context_added"] is True
        assert report["migration_ready"] is True

    def test_migration_not_ready_when_changed(self):
        pack = {"period_events": [{"event_id": "e1"}], "background_events": [], "sources": [], "polls": [], "coverage_gaps": [], "known_limitations": [], "do_not_infer": [], "state_diff": {}}
        baseline = {
            "semantic_key_hashes": {
                "period_events": "changed",
                "background_events": "x",
                "sources": "x",
                "polls": "x",
                "coverage_gaps": "x",
                "known_limitations": "x",
                "do_not_infer": "x",
                "state_diff": "x",
            }
        }
        report = build_migration_report(pack, baseline)
        assert report["business_evidence_unchanged"] is False
        assert report["migration_ready"] is False

    def test_write_migration_report(self, tmp_path):
        pack = {"period_events": [], "background_events": [], "sources": [], "polls": [], "coverage_gaps": [], "known_limitations": [], "do_not_infer": [], "state_diff": {}}
        baseline = {"semantic_key_hashes": {k: "x" for k in pack}}
        path = write_migration_report(tmp_path, pack, baseline)
        data = json.loads(path.read_text(encoding="utf-8"))
        assert data["previous_schema_version"] == "1.0"
        assert data["current_schema_version"] == "1.1"
