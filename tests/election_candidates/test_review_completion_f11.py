"""Phase F1.1 regression: --update-facts-cutoff must actually write in
production (non-test-mode) runs and must report applied truthfully."""

from __future__ import annotations

import json

from app.election_candidates.review_completion import complete_review_through

from .publication_helpers import make_publication_config, open_candidate_repo
from .test_phase_f1_review_completion import ELECTION, _set_cursor


def _setup(tmp_path):
    config = make_publication_config(tmp_path)
    config.raw["test_mode"] = False
    repo = open_candidate_repo(config)
    return config, repo


def test_update_facts_cutoff_writes_files_in_production_mode(tmp_path):
    config, repo = _setup(tmp_path)
    preflight = config.path("coverage_root") / "coverage_preflight.json"
    preflight.write_text(
        json.dumps({"facts_cutoff": "2026-07-27", "coverage_status": "partial"}),
        encoding="utf-8",
    )
    validation = config.path("coverage_root") / "coverage_validation.json"
    validation.write_text(json.dumps({"facts_cutoff": "2026-07-27"}), encoding="utf-8")
    _set_cursor(repo)

    result = complete_review_through(
        repo,
        config,
        election_id=ELECTION,
        through_date="2026-07-28",
        reviewer="local_reviewer",
        from_date="2026-07-27",
        update_facts_cutoff=True,
        status_path=tmp_path / "missing_status.json",
    )

    assert result["facts_cutoff_applied"] is True
    assert result["facts_cutoff_after"] == "2026-07-28"
    assert json.loads(preflight.read_text(encoding="utf-8"))["facts_cutoff"] == "2026-07-28"
    assert json.loads(validation.read_text(encoding="utf-8"))["facts_cutoff"] == "2026-07-28"
    repo.close()


def test_update_facts_cutoff_reports_not_applied_when_no_files(tmp_path):
    config, repo = _setup(tmp_path)
    empty_coverage = tmp_path / "empty_coverage"
    empty_coverage.mkdir()
    config.raw["paths"]["coverage_root"] = str(empty_coverage)
    _set_cursor(repo)

    result = complete_review_through(
        repo,
        config,
        election_id=ELECTION,
        through_date="2026-07-28",
        reviewer="local_reviewer",
        from_date="2026-07-27",
        update_facts_cutoff=True,
        status_path=tmp_path / "missing_status.json",
    )

    assert result["facts_cutoff_applied"] is False
    assert result["facts_cutoff_after"] == "2026-07-28"
    repo.close()
