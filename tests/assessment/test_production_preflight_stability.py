from app.assessment.production_preflight import build_preflight


def _kwargs(pass_count):
    return dict(
        schedule_days=[9, 22],
        period_definition="natural_half_month",
        schedule_definition="delayed_generation",
        calendar_lag_semantics_valid=True,
        full_preparation_days_semantics_valid=True,
        default_provider="deepseek",
        default_model="deepseek-v4-pro",
        credentials_present=True,
        live_deepseek_test="passed",
        json_output_valid=True,
        local_schema_valid=True,
        claim_evidence_valid=True,
        do_not_infer_valid=True,
        required_disclosures_complete=True,
        real_token_usage_available=True,
        cost_estimation_status="estimated",
        cache_reuse_valid=True,
        api_key_exposure_detected=False,
        reasoning_content_persisted=False,
        formal_data_unchanged=True,
        evidence_package_unchanged=True,
        formal_live_validation_pass_count=pass_count,
        required_formal_live_validation_passes=2,
        formal_live_input_business_hash="frozen-input-hash",
        formal_live_response_ids=[f"response-{i}" for i in range(pass_count)],
    )


def test_first_formal_live_pass_is_not_enough():
    result = build_preflight(**_kwargs(1))
    assert result["formal_live_stability_ready"] is False
    assert result["production_llm_ready"] is False
    assert any("1/2" in error for error in result["errors"])


def test_two_formal_live_passes_unlock_llm_gate():
    result = build_preflight(**_kwargs(2))
    assert result["formal_live_stability_ready"] is True
    assert result["production_llm_ready"] is True
    assert result["preflight_ready"] is True
