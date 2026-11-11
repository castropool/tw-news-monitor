from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from app.election_candidates.build_candidate_queue import run_pipeline
from app.election_candidates.candidate_repository import CandidateRepository
from app.election_candidates.config import load_config
from app.lock import InstanceLock

from .conftest import create_formal_db, create_match_db, create_news_db, make_config


def _args(**over):
    data = {
        "config": "config/election_candidate_pipeline.yaml",
        "election_id": None,
        "date_from": "2026-07-01",
        "date_to": "2026-07-31",
        "since_last_success": False,
        "output_root": None,
        "candidate_db": None,
        "match_db": None,
        "match_mode": "inline_classifier",
        "validate_only": False,
        "rebuild_preview": False,
        "reset_test_cursor": False,
        "test_mode": False,
    }
    data.update(over)
    return argparse.Namespace(**data)


def _setup(tmp_path: Path):
    rows = [
        {
            "id": 1, "source_name": "中央社", "category": "politics",
            "title": "陳亭妃宣布參選台南市長",
            "url": "https://www.cna.com.tw/news/aipl/1.aspx",
            "published_at": "2026-07-10T10:00:00+08:00",
            "summary": "",
        },
        {
            "id": 2, "source_name": "聯合新聞網", "category": "politics",
            "title": "謝龍介掃街拜票",
            "url": "https://udn.com/news/story/2.aspx",
            "published_at": "2026-07-11T10:00:00+08:00",
            "summary": "",
        },
        {
            "id": 3, "source_name": "東森新聞", "category": "politics",
            "title": "台南市長選舉民調公布",
            "url": "https://news.ebc.net.tw/news/3.aspx",
            "published_at": "2026-07-12T10:00:00+08:00",
            "summary": "",
        },
        {
            "id": 4, "source_name": "中央社", "category": "politics",
            "title": "賴清德為陳亭妃站台",
            "url": "https://www.cna.com.tw/news/aipl/4.aspx",
            "published_at": "2026-07-13T10:00:00+08:00",
            "summary": "",
        },
    ]
    create_news_db(tmp_path / "news.db", rows)
    create_match_db(tmp_path / "election_watch.db", [])
    create_formal_db(tmp_path / "election_context.db")
    config = make_config(tmp_path)
    # This fixture is pinned to July 2026; do not let the host date change
    # which articles the first incremental scan can see.
    config.raw["scan"]["initial_scan_days"] = 365
    return config


def _cursor(config, repo):
    return repo.get_scan_cursor("TW-2026-TNN-MAYOR", "news_article_id")


def test_production_config_resolution(tmp_path):
    config = load_config("config/election_candidate_pipeline.yaml")
    assert config.get("deployment.scheduler_mode") == "since-last-success"
    log = config.get("deployment.log_path")
    assert Path(log).is_absolute()
    assert config.path("news_db").is_absolute()
    assert config.path("candidate_db").is_absolute()


def test_since_last_success_cursor_does_not_regress(tmp_path):
    config = _setup(tmp_path)
    m1 = run_pipeline(config, _args(since_last_success=True, output_root=str(tmp_path / "inc1")))
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    cursor = _cursor(config, repo)
    assert cursor is not None
    assert cursor["last_article_id"] == 4
    assert cursor["last_published_at"] == "2026-07-13T10:00:00+08:00"
    repo.close()

    m2 = run_pipeline(config, _args(since_last_success=True, output_root=str(tmp_path / "inc2")))
    assert m2["articles_examined"] == 0
    repo.connect()
    cursor = _cursor(config, repo)
    assert cursor["last_article_id"] == 4  # must not regress to 0
    assert cursor["last_published_at"] == "2026-07-13T10:00:00+08:00"
    repo.close()
    assert m2["cursor_after"] == "4"


def test_history_bootstrap_sets_cursor_when_missing(tmp_path):
    config = _setup(tmp_path)
    m = run_pipeline(
        config,
        _args(date_from="2026-07-01", date_to="2026-07-31", output_root=str(tmp_path / "hist")),
    )
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    cursor = _cursor(config, repo)
    repo.close()
    assert cursor is not None
    assert cursor["last_article_id"] == 4
    assert m["scan_mode"] == "explicit_history"


def test_history_run_does_not_touch_existing_cursor(tmp_path):
    config = _setup(tmp_path)
    run_pipeline(config, _args(since_last_success=True, output_root=str(tmp_path / "inc")))
    run_pipeline(
        config,
        _args(date_from="2026-07-01", date_to="2026-07-31", output_root=str(tmp_path / "hist")),
    )
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    cursor = _cursor(config, repo)
    repo.close()
    assert cursor["last_article_id"] == 4


def test_failed_run_does_not_advance_cursor(tmp_path):
    config = _setup(tmp_path)
    run_pipeline(config, _args(since_last_success=True, output_root=str(tmp_path / "ok")))
    bad = make_config(tmp_path)
    bad.raw["paths"]["news_db"] = str(tmp_path / "missing.db")
    with pytest.raises(Exception):
        run_pipeline(bad, _args(since_last_success=True, output_root=str(tmp_path / "bad")))
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    cursor = _cursor(config, repo)
    repo.close()
    assert cursor["last_article_id"] == 4


def test_single_instance_lock_blocks_concurrent_run(tmp_path):
    config = _setup(tmp_path)
    lock = InstanceLock(
        config.path("lock_root") / "candidate_pipeline_TW-2026-TNN-MAYOR.lock"
    )
    assert lock.acquire() is True
    try:
        result = run_pipeline(
            config, _args(since_last_success=True, output_root=str(tmp_path / "blocked"))
        )
        assert result["status"] == "blocked"
        assert "another candidate pipeline instance" in result["reason"]
    finally:
        lock.release()
    result = run_pipeline(
        config, _args(since_last_success=True, output_root=str(tmp_path / "ok"))
    )
    assert "run_id" in result
    assert result["articles_examined"] == 4


def test_run_log_written_with_required_fields(tmp_path):
    config = _setup(tmp_path)
    config.raw["deployment"]["log_path"] = str(tmp_path / "logs" / "candidate_pipeline.jsonl")
    run_pipeline(config, _args(since_last_success=True, output_root=str(tmp_path / "run")))
    log_path = Path(config.raw["deployment"]["log_path"])
    assert log_path.exists()
    line = json.loads(log_path.read_text(encoding="utf-8").strip().splitlines()[-1])
    for key in (
        "run_id", "started_at", "finished_at", "cursor_before", "cursor_after",
        "articles_examined", "articles_matched", "candidate_events_created",
        "duplicate_candidate_count", "review_required_count", "hold_count",
        "auto_reject_count", "context_only_count", "status", "error_summary",
    ):
        assert key in line
    assert line["status"] == "success"
