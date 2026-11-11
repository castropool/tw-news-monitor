from __future__ import annotations

from pathlib import Path

from app.election_candidates.input_inspector import (
    assess_merge_function,
    inspect_election_watch,
    inspect_formal_repository,
    inspect_news_db,
    run_inspection,
)

from .conftest import create_formal_db, create_match_db, create_news_db, make_config


def test_news_db_inspection_reports_real_schema(tmp_path):
    rows = [
        {"id": 1, "title": "測試", "url": "https://a.com/x", "source_name": "中央社",
         "category": "politics", "published_at": "2026-07-01T10:00:00", "summary": ""}
    ]
    create_news_db(tmp_path / "news.db", rows)
    config = make_config(tmp_path)
    result = inspect_news_db(config)
    assert result["exists"] is True
    assert result["primary_table"] == "articles"
    assert result["missing_required_fields"] == []
    assert result["table_schemas"]["articles"]["row_count"] == 1


def test_election_watch_inspection_reports_empty_matches(tmp_path):
    create_match_db(tmp_path / "election_watch.db", [])
    config = make_config(tmp_path)
    result = inspect_election_watch(config)
    assert result["match_table_exists"] is True
    assert result["match_row_count"] == 0
    assert result["sufficient_for_tainan_selection"] is False


def test_merge_function_assessment_reusable_without_side_effects():
    result = assess_merge_function()
    assert result["importable"] is True
    assert result["deterministic"] is True
    assert result["memory_only"] is True
    assert result["mutates_input"] is False
    assert result["groups_by_title_only"] is True


def test_formal_repository_capabilities_list_forbidden_writes():
    config = make_config(Path("."))
    result = inspect_formal_repository(config)
    assert "save_event" in result["forbidden_write_methods"]
    assert "save_source" in result["forbidden_write_methods"]
    assert "get_event" in result["allowed_read_only_methods_for_candidate_pipeline"]


def test_run_inspection_writes_all_reports(tmp_path):
    create_news_db(tmp_path / "news.db", [])
    create_match_db(tmp_path / "election_watch.db", [])
    create_formal_db(tmp_path / "election_context.db")
    config = make_config(tmp_path)
    result = run_inspection(config, output_root=tmp_path / "inspect")
    out = Path(result["output_root"])
    for name in [
        "news_db_schema.json",
        "election_watch_capabilities.json",
        "merge_function_assessment.json",
        "formal_repository_capabilities.json",
        "source_field_mapping.json",
        "input_inspection_summary.md",
    ]:
        assert (out / name).exists()
