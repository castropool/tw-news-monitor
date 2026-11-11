from datetime import date

from app.assessment.generation_eligibility import (
    ALLOWED_MODES,
    build_generation_eligibility,
)


class TestGenerationEligibility:
    def test_cutoff_before_end_draft(self):
        e = build_generation_eligibility(
            evidence_pack_ready=True,
            facts_cutoff="2026-07-27",
            period_end=date(2026, 7, 31),
            uncovered_date_range=["2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31"],
        )
        assert e["final_report_allowed"] is False
        assert e["allowed_generation_mode"] == "draft_with_data_gap"

    def test_cutoff_equal_end_final(self):
        e = build_generation_eligibility(
            evidence_pack_ready=True,
            facts_cutoff="2026-07-31",
            period_end=date(2026, 7, 31),
            uncovered_date_range=[],
        )
        assert e["final_report_allowed"] is True
        assert e["allowed_generation_mode"] == "final"

    def test_cutoff_after_end_final(self):
        e = build_generation_eligibility(
            evidence_pack_ready=True,
            facts_cutoff="2026-08-10",
            period_end=date(2026, 7, 31),
            uncovered_date_range=[],
        )
        assert e["final_report_allowed"] is True

    def test_uncovered_dates_listed(self):
        e = build_generation_eligibility(
            evidence_pack_ready=True,
            facts_cutoff="2026-07-27",
            period_end=date(2026, 7, 31),
            uncovered_date_range=["2026-07-28"],
        )
        assert any("2026-07-28" in d for d in e["required_disclosures"])

    def test_uncovered_not_written_as_no_events(self):
        e = build_generation_eligibility(
            evidence_pack_ready=True,
            facts_cutoff="2026-07-27",
            period_end=date(2026, 7, 31),
            uncovered_date_range=["2026-07-28"],
        )
        assert any("没有重要事件" in d for d in e["required_disclosures"])

    def test_mode_enum_valid(self):
        for cutoff, end in (("2026-07-27", date(2026, 7, 31)), ("2026-07-31", date(2026, 7, 31))):
            e = build_generation_eligibility(
                evidence_pack_ready=True,
                facts_cutoff=cutoff,
                period_end=end,
                uncovered_date_range=[],
            )
            assert e["allowed_generation_mode"] in ALLOWED_MODES

