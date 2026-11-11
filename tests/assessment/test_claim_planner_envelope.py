from app.assessment.claim_planner import build_planner_envelope
from tests.assessment.two_stage_fixtures import contract_fixture


def test_planner_envelope_derives_exact_source_allow_lists():
    envelope = build_planner_envelope(
        contract_fixture(), formal_state_hash="formal-hash", evidence_pack_hash="pack-hash"
    )
    events = {item["event_id"]: item for item in envelope["events"]}
    polls = {item["poll_id"]: item for item in envelope["polls"]}
    assert events["e1"]["allowed_source_ids"] == ["s1"]
    assert events["e2"]["allowed_source_ids"] == ["s2"]
    assert polls["p1"]["allowed_poll_source_ids"] == ["s3"]
    assert "source_ids" not in events["e1"]
    assert envelope["input_contract_version"] == "1.0"

