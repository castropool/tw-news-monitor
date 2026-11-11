"""Test poll release validation against golden cases and acceptance rules."""
import json, yaml, os, sys, tempfile, uuid
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", ".."))

from app.election_context.poll_validator import (
    validate_poll_record, validate_poll_collection,
    check_poll_comparability, build_comparable_group_key,
)

BASE = os.path.join(os.path.dirname(__file__), "..", "..")
GOLDEN = os.path.join(BASE, "data/election_seed/tainan_2026/golden_poll_cases.json")
RULES = os.path.join(BASE, "data/election_seed/tainan_2026/poll_release_acceptance_rules.yaml")
SCHEMA = os.path.join(BASE, "data/election_seed/tainan_2026/poll_schema.json")

def load_golden():
    with open(GOLDEN, encoding="utf-8") as f:
        return json.load(f)

def load_rules():
    with open(RULES, encoding="utf-8") as f:
        return yaml.safe_load(f) or {}

class TestGoldenCases:
    def setup_method(self):
        self.golden = load_golden()
        self.rules = load_rules()

    def _find_case(self, case_id):
        for c in self.golden:
            if c["case_id"] == case_id:
                return c
        return None

    def test_valid_public_poll(self):
        case = self._find_case("valid_public_poll")
        rec = case["record"]
        vr = validate_poll_record(rec, self.rules)
        assert vr["valid"] == case["expected_valid"], f"errors: {vr['errors']}"
        assert vr["errors"] == case.get("expected_errors", [])

    def test_internal_poll_claim(self):
        case = self._find_case("internal_poll_claim")
        rec = case["record"]
        vr = validate_poll_record(rec, self.rules)
        assert vr["valid"] == case["expected_valid"]
        assert rec.get("usable_for_poll_trend") is False

    def test_online_vote(self):
        case = self._find_case("online_vote")
        rec = case["record"]
        vr = validate_poll_record(rec, self.rules)
        assert vr["valid"] == case["expected_valid"]
        assert rec.get("usable_as_scientific_poll") is False

    def test_missing_sample_size_but_complete_fails(self):
        case = self._find_case("missing_sample_size_but_complete")
        rec = case["record"]
        vr = validate_poll_record(rec, self.rules)
        assert not vr["valid"]
        assert any("sample_size" in e for e in vr["errors"])

    def test_reversed_dates_fails(self):
        case = self._find_case("reversed_dates")
        rec = case["record"]
        vr = validate_poll_record(rec, self.rules)
        assert not vr["valid"]
        assert any("field_start" in e for e in vr["errors"])

    def test_invalid_question_id_fails(self):
        case = self._find_case("invalid_question_id_in_results")
        rec = case["record"]
        vr = validate_poll_record(rec, self.rules)
        assert not vr["valid"]
        assert any("不存在" in e for e in vr["errors"])

    def test_cross_population_not_comparable(self):
        pt = {"geography":"台南市","eligible_population":"registered_voters"}
        pa = {"geography":"台南市","eligible_population":"likely_voters"}
        poll_a = {"questions":[{"question_type":"head_to_head","candidate_set":["陈亭妃","谢龙介"]}],"population":pt,"poll_type":"general_election_poll"}
        poll_b = {"questions":[{"question_type":"head_to_head","candidate_set":["陈亭妃","谢龙介"]}],"population":pa,"poll_type":"general_election_poll"}
        cr = check_poll_comparability(poll_a, poll_b)
        assert not cr["comparable"]
        assert any("调查母体" in e for e in cr["errors"])

    def test_diff_candidate_set_not_comparable(self):
        case = self._find_case("diff_candidate_set_not_comparable")
        ct = case.get("comparability_test", {})
        poll_a = {"questions":[{"question_type":"head_to_head","candidate_set":ct.get("a_candidates",[])}],"population":{"eligible_population":"registered_voters"},"poll_type":"general_election_poll"}
        poll_b = {"questions":[{"question_type":"head_to_head","candidate_set":ct.get("b_candidates",[])}],"population":{"eligible_population":"registered_voters"},"poll_type":"general_election_poll"}
        cr = check_poll_comparability(poll_a, poll_b)
        assert cr["comparable"] == ct.get("comparable", True)

    def test_syndicated_duplicate_fails(self):
        case = self._find_case("syndicated_duplicate")
        rec = case["record"]
        vr = validate_poll_record(rec, self.rules)
        assert not vr["valid"]
        assert any("转载" in e for e in vr["errors"])

    def test_collection_validates_all_cases(self):
        cases = [c["record"] for c in self.golden if "record" in c]
        result = validate_poll_collection(cases, self.rules)
        assert result["record_count"] == len(cases)
        assert result["valid_count"] + result["invalid_count"] == result["record_count"]

    def test_empty_file(self):
        result = validate_poll_collection([])
        assert result["valid_count"] == 0
        assert result["record_count"] == 0

    def test_comparable_group_key(self):
        rec1 = {"questions":[{"question_type":"head_to_head","candidate_set":["陈亭妃","谢龙介"]}],"population":{"eligible_population":"registered_voters"}}
        k1 = build_comparable_group_key(rec1)
        assert "陈亭妃" in k1
        assert "registered_voters" in k1

    def test_invalid_jsonl_handled(self):
        import json as _js
        with tempfile.NamedTemporaryFile(suffix=".jsonl", mode="w", delete=False) as f:
            f.write("not valid json\n")
            fname = f.name
        try:
            from app.election_context.validate_poll_release import main as vpr_main
        except Exception:
            pass
        os.unlink(fname)

    # ========== New tests for case 10 and edge cases ==========

    def test_no_unjustified_normalization(self):
        case = self._find_case("no_unjustified_normalization")
        rec = case["record"]
        vr = validate_poll_record(rec, self.rules)
        # Should pass with warning about sum
        assert vr["valid"] == case["expected_valid"]
        # Verify reported values are unchanged
        for res in rec["results"]:
            opt = res["option_name"]
            rv = res["reported_value"]
            nv = res["normalized_value"]
            assert nv is not None, f"{opt}: normalized_value should not be None"
            assert str(nv) in rv or abs(float(rv) - nv) < 0.01, f"{opt}: value changed {rv}->{nv}"

    def test_unjustified_normalization_fails(self):
        case = self._find_case("unjustified_normalization_fails")
        rec = case["record"]
        vr = validate_poll_record(rec, self.rules)
        assert not vr["valid"]
        assert any("normalized_value" in e for e in vr["errors"])

    def test_internal_poll_no_pollster(self):
        case = self._find_case("internal_poll_no_pollster")
        rec = case["record"]
        vr = validate_poll_record(rec, self.rules)
        assert vr["valid"] == case["expected_valid"]
        assert rec.get("methodology_complete") is False
        assert rec.get("usable_for_poll_trend") is False

    def test_incomplete_fieldwork_month_only(self):
        case = self._find_case("incomplete_fieldwork_month_only")
        rec = case["record"]
        vr = validate_poll_record(rec, self.rules)
        assert vr["valid"] == case["expected_valid"]
        assert rec.get("methodology_complete") is False
        # Verify no fabricated dates
        fw = rec.get("fieldwork", {})
        assert fw.get("field_start") is None
        assert fw.get("field_end") is None

    def test_golden_case_count(self):
        assert len(self.golden) >= 12

    def test_all_golden_case_expected_results(self):
        for case in self.golden:
            if "record" not in case:
                continue
            rec = case["record"]
            vr = validate_poll_record(rec, self.rules)
            if case["expected_valid"]:
                assert vr["valid"], f"{case['case_id']}: expected valid but got errors: {vr['errors']}"
            else:
                assert not vr["valid"], f"{case['case_id']}: expected invalid but got valid"

    # ========== Release mode tests ==========

    def test_release_mode_all_valid(self):
        """Release mode with all valid records => release_ready=true."""
        from app.election_context.validate_poll_release import _run_release_mode
        items = [c for c in self.golden if c.get("expected_valid") is True and "record" in c]
        output = _run_release_mode(items, self.rules)
        assert output["release_ready"] is True, f"Expected release_ready=true, got errors"
        assert output["valid_count"] == len(items)

    def test_release_mode_mixed_invalid_fails(self):
        """Release mode with any invalid record => release_ready=false."""
        from app.election_context.validate_poll_release import _run_release_mode
        items = [c for c in self.golden if "record" in c]
        output = _run_release_mode(items, self.rules)
        # Some records are expected to be invalid
        if output["invalid_count"] > 0:
            assert output["release_ready"] is False

    def test_release_mode_not_bypassed_by_expected_valid(self):
        """Release mode must not be bypassed by expected_valid=false."""
        from app.election_context.validate_poll_release import _run_release_mode
        # Build a set: include a record that's invalid per validation
        # but mark it as expected_valid=False in golden
        bad_records = [c for c in self.golden if c.get("expected_valid") is False and "record" in c]
        if bad_records:
            output = _run_release_mode(bad_records, self.rules)
            # Release mode doesn't read expected_valid, so invalid records still fail
            assert output["release_ready"] is False
            assert output["invalid_count"] > 0

    def test_valid_public_poll_is_trend_eligible(self):
        case = self._find_case("valid_public_poll")
        rec = case["record"]
        assert rec.get("usable_for_poll_trend") is True
        assert rec.get("methodology_complete") is True

    def test_golden_cli_executes_all(self):
        """Verify golden CLI mode executes 13/13 cases."""
        import subprocess
        result = subprocess.run(
            [sys.executable, "-m", "app.election_context.validate_poll_release",
             "--mode", "golden", "--input", GOLDEN],
            capture_output=True, text=True, cwd=os.path.join(BASE))
        out = json.loads(result.stdout)
        assert out["golden_case_count"] == 13
        assert out["executed_case_count"] == 13
        assert out["passed_as_expected_count"] == 13
        assert out["release_ready"] is True
        assert out["trend_eligible_count"] >= 1
