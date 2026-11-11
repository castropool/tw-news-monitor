import json

from app.assessment.report_writer_stage2 import build_stage2_request
from app.assessment.validated_claim_store import build_stage2_input


def test_stage2_request_excludes_evidence_identifier_fields():
    store = {
        "claim_plan_business_hash": "hash",
        "accepted_claims": [{
            "claim_id": "CP_S01_001", "target_section_id": "S01",
            "claim_type": "current_assessment", "claim_strength": "bounded_inference",
            "claim_text": "研判选情仍待观察。", "evidence_reasoning_summary": "两项事实",
            "confidence": "medium", "limitations": [], "material_for_report": True,
            "applies_to_period": True, "event_ids": ["e1"], "source_ids": ["s1"],
        }],
        "raw_claim_plan": {"data_limitations": []},
    }
    stage2_input = build_stage2_input(store, data_context={"facts_cutoff": "2026-07-27"})
    request = build_stage2_request(stage2_input)
    serialized = json.dumps(request, ensure_ascii=False)
    assert '"event_ids"' not in serialized
    assert '"source_ids"' not in serialized
    assert '"poll_ids"' not in serialized
    assert request["output_contract"]["schema_version"] == "1.0"

