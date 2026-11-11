from app.assessment.claim_plan_schema import validate_stage2_draft_schema


def valid_stage2():
    return {
        "stage2_draft_version": "1.0",
        "report_writer_stage2_contract_version": "1.0",
        "validated_claim_plan_hash": "plan-hash",
        "title": "台南选情研判",
        "title_claim_ids": ["CP_S01_001"],
        "overall_judgment_claim_ids": ["CP_S01_001"],
        "sections": [
            {
                "section_id": f"S{i:02d}",
                "heading": f"section {i}",
                "claim_ids": ["CP_S01_001"] if i == 1 else [],
                "section_purpose": f"purpose_{i}",
            }
            for i in range(1, 9)
        ],
        "claim_renderings": [
            {"claim_id": "CP_S01_001", "rendered_text": "基于正式事实，研判选情结构仍待观察。"}
        ],
    }


def test_stage2_schema_accepts_fixed_eight_sections():
    assert validate_stage2_draft_schema(valid_stage2()) == []


def test_stage2_schema_rejects_evidence_fields_and_section_reordering():
    value = valid_stage2()
    value["claim_renderings"][0]["source_ids"] = ["s1"]
    value["sections"] = list(reversed(value["sections"]))
    errors = validate_stage2_draft_schema(value)
    assert any("额外" in item for item in errors)
    assert any("S01" in item for item in errors)
