from app.assessment.report_generation_validator import build_generation_validation


class TestGenerationValidator:
    def test_all_green(self):
        claim_validation = {
            "errors": [],
            "output_schema_valid": True,
            "all_claims_validated": True,
            "all_event_ids_exist": True,
            "all_poll_ids_exist": True,
            "all_source_ids_exist": True,
            "all_gap_ids_exist": True,
            "numeric_claims_grounded": True,
            "date_claims_grounded": True,
            "person_names_grounded": True,
            "organization_names_grounded": True,
            "required_disclosures_complete": True,
            "do_not_infer_compliant": True,
            "no_external_facts": True,
            "no_unsupported_poll_claims": True,
            "no_unsupported_probability": True,
        }
        v = build_generation_validation(
            input_contract_ready=True,
            evidence_pack_ready=True,
            eligibility_respected=True,
            provider_result_valid=True,
            claim_validation=claim_validation,
            final_report_allowed=False,
            generated_mode="draft_with_data_gap",
            formal_unchanged={
                "formal_data_unchanged": True,
                "snapshot_data_unchanged": True,
                "coverage_data_unchanged": True,
                "poll_data_unchanged": True,
                "evidence_package_unchanged": True,
            },
        )
        assert v["report_generation_ready"] is True
        assert v["final_report_allowed"] is False
        assert v["generated_report_mode"] == "draft_with_data_gap"

    def test_claim_errors_flow_through(self):
        v = build_generation_validation(
            input_contract_ready=True,
            evidence_pack_ready=True,
            eligibility_respected=True,
            provider_result_valid=True,
            claim_validation={"errors": ["numeric_claims_grounded: 无依据"]},
            final_report_allowed=False,
            generated_mode="draft_with_data_gap",
            formal_unchanged={"formal_data_unchanged": True},
        )
        assert v["report_generation_ready"] is False

