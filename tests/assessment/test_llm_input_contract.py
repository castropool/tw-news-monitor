import copy
from datetime import date

from app.assessment.llm_input_contract import (
    ALLOWED_TOP_LEVEL_FIELDS,
    SCHEMA_VERSION,
    build_llm_input_contract,
    validate_llm_input_contract,
)


def _pack():
    return {
        "schema_version": "1.1",
        "election_id": "e",
        "election_name": "n",
        "report_period": {"period_start": "2026-07-16", "period_end": "2026-07-31"},
        "data_status": {"facts_cutoff": "2026-07-27"},
        "generation_eligibility": {
            "evidence_pack_ready": True,
            "report_period_fully_covered_by_facts": False,
            "final_report_allowed": False,
            "allowed_generation_mode": "draft_with_data_gap",
            "required_disclosures": ["a", "b", "c"],
        },
        "current_snapshot": {"snapshot_id": "cur"},
        "previous_snapshot": {"snapshot_id": "prev"},
        "state_diff": {"state_diff_mode": "structured_comparison"},
        "period_events": [{"event_id": "e1", "source_ids": ["s1"]}],
        "background_events": [],
        "sources": [{"source_id": "s1"}],
        "polls": [],
        "theme_status": [],
        "coverage_gaps": [],
        "active_research_tasks": [{"research_task_id": "RT05"}],
        "known_limitations": ["k"],
        "do_not_infer": ["d"],
        "evidence_statistics": {},
    }


def _validate(contract, **kw):
    defaults = dict(
        formal_event_ids={"e1"},
        formal_source_ids={"s1"},
        formal_poll_ids=set(),
        formal_link_pairs={("e1", "s1")},
        authoritative_active_task_ids=["RT05"],
        facts_cutoff="2026-07-27",
        period_end=date(2026, 7, 31),
    )
    defaults.update(kw)
    return validate_llm_input_contract(contract, **defaults)


class TestLlmInputContract:
    def test_valid_contract_ready(self):
        contract = build_llm_input_contract(_pack())
        v = _validate(contract)
        assert v["llm_input_contract_ready"] is True
        assert v["errors"] == []

    def test_schema_version_1_1(self):
        contract = build_llm_input_contract(_pack())
        assert contract["schema_version"] == "1.1"

    def test_prohibited_field_fails(self):
        pack = _pack()
        contract = build_llm_input_contract(pack)
        contract["run_id"] = "abc"  # 模拟被篡改注入的禁止字段
        v = _validate(contract)
        assert v["llm_input_contract_ready"] is False
        assert any("禁止字段" in e for e in v["errors"])

    def test_absolute_path_fails(self):
        pack = _pack()
        pack["known_limitations"] = ["D:\\secret\\path"]
        contract = build_llm_input_contract(pack)
        v = _validate(contract)
        assert v["llm_input_contract_ready"] is False
        assert any("绝对路径" in e for e in v["errors"])

    def test_run_id_fails(self):
        pack = _pack()
        pack["current_snapshot"] = {"snapshot_id": "cur", "run_id": "r1"}
        contract = build_llm_input_contract(pack)
        v = _validate(contract)
        assert v["llm_input_contract_ready"] is False

    def test_reference_integrity(self):
        contract = build_llm_input_contract(_pack())
        v = _validate(contract, formal_event_ids=set())
        assert v["llm_input_contract_ready"] is False
        assert v["reference_integrity_check"] == "failed"

    def test_active_tasks_consistency(self):
        contract = build_llm_input_contract(_pack())
        v = _validate(contract, authoritative_active_task_ids=["RT05", "RT06"])
        assert v["llm_input_contract_ready"] is False

    def test_generation_eligibility_present(self):
        pack = _pack()
        del pack["generation_eligibility"]
        contract = build_llm_input_contract(pack)
        v = _validate(contract)
        assert v["llm_input_contract_ready"] is False
        assert "缺少必需字段" in "; ".join(v["errors"])

    def test_allowed_top_level_fields_fixed(self):
        assert ALLOWED_TOP_LEVEL_FIELDS == [
            "schema_version",
            "contract_version",
            "election_id",
            "election_name",
            "report_period",
            "data_status",
            "generation_eligibility",
            "current_snapshot",
            "previous_snapshot",
            "state_diff",
            "period_events",
            "background_events",
            "sources",
            "polls",
            "theme_status",
            "coverage_gaps",
            "active_research_tasks",
            "known_limitations",
            "do_not_infer",
            "evidence_statistics",
        ]
