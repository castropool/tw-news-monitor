import copy
import json
from pathlib import Path

import pytest

from app.assessment.claim_evidence_validator import build_evidence_context, validate_structured_report
from app.assessment.evidence_pack_builder import load_yaml
from app.assessment.llm.mock_provider import MockProvider
from app.assessment.llm.provider_output_normalizer import (
    OutputNormalizationError,
    normalize_json_object,
)
from app.assessment.report_output_schema import validate_report_schema


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent.parent
EVIDENCE_DIR = PROJECT_ROOT / "data/reports/tainan_2026/evidence_packages/2026-07-16_2026-07-31"
FAILED_FIXTURE = PROJECT_ROOT / "tests/fixtures/deepseek_live_contract/formal_live_failure_20260808.json"


def _load(name):
    return json.loads((EVIDENCE_DIR / name).read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def formal_objects():
    contract = _load("llm_input_contract.json")
    pack = _load("report_evidence_pack.json")
    config = load_yaml(PROJECT_ROOT / "config/election_assessment.yaml")
    report = MockProvider(fixture="valid_draft_with_gap").generate_structured_report(
        system_prompt="s",
        user_payload=contract,
        output_schema={},
        request_metadata={"attempt": 1},
    ).structured_output
    return contract, pack, config, report


@pytest.mark.parametrize(
    ("case_id", "wrap"),
    [
        ("01_exact_json", lambda value: json.dumps(value, ensure_ascii=False)),
        ("02_outer_whitespace", lambda value: " \n" + json.dumps(value, ensure_ascii=False) + "\r\n "),
        ("03_utf8_bom", lambda value: "\ufeff" + json.dumps(value, ensure_ascii=False)),
        ("04_json_fence", lambda value: "```json\n" + json.dumps(value, ensure_ascii=False) + "\n```"),
        ("05_plain_fence", lambda value: "```\n" + json.dumps(value, ensure_ascii=False) + "\n```"),
        ("06_prose_prefix", lambda value: "结果如下：\n" + json.dumps(value, ensure_ascii=False)),
        ("07_prose_suffix", lambda value: json.dumps(value, ensure_ascii=False) + "\n以上。"),
        ("08_prose_both", lambda value: "结果：\n" + json.dumps(value, ensure_ascii=False) + "\n结束。"),
    ],
)
def test_valid_golden_cases_are_accepted(formal_objects, case_id, wrap):
    report = formal_objects[3]
    normalized = normalize_json_object(wrap(report))
    assert normalized.value == report, case_id
    assert validate_report_schema(normalized.value) == [], case_id


def _mutate(report, case_id):
    value = copy.deepcopy(report)
    if case_id == "09_missing_required":
        value.pop("title")
    elif case_id == "10_extra_top_level":
        value["period_highlights"] = []
    elif case_id == "11_wrong_schema_version":
        value["schema_version"] = "1.0"
    elif case_id == "12_invalid_generation_mode":
        value["generation_mode"] = "finalish"
    elif case_id == "13_invalid_report_status":
        value["report_status"] = "accepted"
    elif case_id == "14_sections_wrong_type":
        value["sections"] = {}
    elif case_id == "15_claims_wrong_type":
        value["claims"] = {}
    elif case_id == "16_missing_data_context":
        value.pop("data_context")
    elif case_id == "17_extra_claim_field":
        value["claims"][0]["fabricated"] = True
    elif case_id == "18_unknown_event_reference":
        value["claims"][0]["supporting_event_ids"] = ["evt_unknown"]
    elif case_id == "19_unknown_source_reference":
        value["claims"][0]["supporting_source_ids"] = ["src_unknown"]
    elif case_id == "20_duplicate_claim_id":
        value["claims"][1]["claim_id"] = value["claims"][0]["claim_id"]
    elif case_id == "21_invalid_section_count":
        value["sections"] = value["sections"][:-1]
    elif case_id == "22_missing_required_disclosure":
        value["required_disclosures"] = []
    else:
        raise AssertionError(case_id)
    return value


@pytest.mark.parametrize(
    "case_id",
    [
        "09_missing_required",
        "10_extra_top_level",
        "11_wrong_schema_version",
        "12_invalid_generation_mode",
        "13_invalid_report_status",
        "14_sections_wrong_type",
        "15_claims_wrong_type",
        "16_missing_data_context",
        "17_extra_claim_field",
        "18_unknown_event_reference",
        "19_unknown_source_reference",
        "20_duplicate_claim_id",
        "21_invalid_section_count",
        "22_missing_required_disclosure",
    ],
)
def test_invalid_golden_cases_are_rejected(formal_objects, case_id):
    contract, pack, config, report = formal_objects
    invalid = _mutate(report, case_id)
    ctx = build_evidence_context(contract, evidence_pack=pack, config=config)
    validation = validate_structured_report(invalid, ctx, expected_mode="draft_with_data_gap")
    assert validation["all_claims_validated"] is False, case_id


@pytest.mark.parametrize(
    ("case_id", "content"),
    [
        ("23_prose_only", "这是一份没有 JSON 的报告。"),
        ("24_multiple_json_objects", '{"a": 1}\n{"b": 2}'),
        ("25_array_root", '[{"a": 1}]'),
        ("26_unclosed_object", '{"a": 1'),
    ],
)
def test_non_object_or_ambiguous_format_is_rejected(case_id, content):
    with pytest.raises(OutputNormalizationError):
        normalize_json_object(content)


def test_phase4_real_failure_fixture_remains_rejected(formal_objects):
    fixture = json.loads(FAILED_FIXTURE.read_text(encoding="utf-8"))
    response = fixture["response"]
    assert validate_report_schema(response)
    contract, pack, config, _ = formal_objects
    ctx = build_evidence_context(contract, evidence_pack=pack, config=config)
    validation = validate_structured_report(response, ctx, expected_mode="draft_with_data_gap")
    assert validation["all_claims_validated"] is False
    assert fixture["expected_normalization_ready"] is False
