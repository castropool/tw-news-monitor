from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

import pytest

from app.election_candidates.build_candidate_queue import run_pipeline
from app.election_candidates.candidate_repository import CandidateRepository

from .conftest import create_formal_db, create_match_db, create_news_db, make_config
from .test_build_candidate_queue_cli import _args


def _news_rows():
    return [
        {
            "id": 1, "title": "陳亭妃宣布參選台南市長", "url": "https://www.cna.com.tw/news/aipl/99999901.aspx",
            "source_name": "中央社", "category": "politics", "published_at": "2026-07-10T09:00:00+08:00",
            "summary": "",
        },
        {
            "id": 2, "title": "民進黨正式提名陳亭妃參選台南市長", "url": "https://www.cna.com.tw/news/aipl/99999902.aspx",
            "source_name": "中央社", "category": "politics", "published_at": "2026-07-12T10:00:00+08:00",
            "summary": "",
        },
        {
            "id": 3, "title": "阿富汗洪災20死", "url": "https://www.cna.com.tw/news/aopl/99999903.aspx",
            "source_name": "中央社", "category": "international", "published_at": "2026-07-21T02:17:04+08:00",
            "summary": "",
        },
        {
            "id": 4, "title": "謝龍介質疑沒為毒油道歉", "url": "https://udn.com/news/story/6656/99999904",
            "source_name": "聯合新聞網", "category": "politics", "published_at": "2026-07-14T16:34:00+08:00",
            "summary": "",
        },
    ]


def _setup(tmp_path):
    create_news_db(tmp_path / "news.db", _news_rows())
    create_match_db(tmp_path / "election_watch.db", [])
    create_formal_db(tmp_path / "election_context.db")
    (tmp_path / "release.zip").write_bytes(b"frozen-release")
    config = make_config(tmp_path)
    # These cursor tests use a fixed July 2026 fixture independent of the
    # host calendar date; keep the production 45-day default unchanged.
    config.raw["scan"]["initial_scan_days"] = 365
    return config


def _file_sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _old_run(root: Path, name: str, mtime: int) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "payload.jsonl").write_text(name, encoding="utf-8")
    os.utime(path, (mtime, mtime))
    return path


def test_full_pipeline_build_and_outputs(tmp_path):
    config = _setup(tmp_path)
    news_before = _file_sha(config.path("news_db"))
    formal_before = _file_sha(config.path("formal_db"))
    match_before = _file_sha(config.path("match_db"))

    manifest = run_pipeline(config, _args())
    assert manifest["articles_examined"] == 4
    assert manifest["articles_matched"] >= 3
    assert manifest["total_candidate_status_counts"]["review_required"] >= 1
    assert manifest["formal_write_method_call_count"] == 0
    assert manifest["inputs_unchanged"]["news_db_unchanged"] is True
    assert manifest["inputs_unchanged"]["formal_data_unchanged"] is True
    assert manifest["inputs_unchanged"]["frozen_release_unchanged"] is True

    run_dir = Path(manifest["output_paths"]["review_queue"]).parent
    for name in [
        "candidate_events.jsonl", "candidate_event_articles.jsonl",
        "candidate_assertions.jsonl", "candidate_sources.jsonl",
        "formal_duplicate_suggestions.jsonl", "review_queue.json", "review_queue.md",
        "hold_queue.json", "duplicate_queue.json", "auto_reject_summary.json",
        "candidate_validation.json", "run_manifest.json", "run_idempotency.json",
    ]:
        assert (run_dir / name).exists(), name

    assert _file_sha(config.path("news_db")) == news_before
    assert _file_sha(config.path("formal_db")) == formal_before
    assert _file_sha(config.path("match_db")) == match_before


def test_default_output_enforces_run_retention(tmp_path):
    config = _setup(tmp_path)
    config.raw["run_retention"] = {
        "enabled": True,
        "keep_latest": 2,
        "max_total_mb": 40,
    }
    runs_root = config.path("output_root") / "runs"
    oldest = _old_run(runs_root, "run_001", 1)
    middle = _old_run(runs_root, "run_002", 2)
    newest = _old_run(runs_root, "run_003", 3)

    manifest = run_pipeline(config, _args())

    current = Path(manifest["output_paths"]["review_queue"]).parent
    remaining = {path.name for path in runs_root.iterdir() if path.is_dir()}
    assert remaining == {newest.name, current.name}
    assert not oldest.exists()
    assert not middle.exists()


def test_explicit_output_root_does_not_prune_configured_runs(tmp_path):
    config = _setup(tmp_path)
    config.raw["run_retention"] = {
        "enabled": True,
        "keep_latest": 1,
        "max_total_mb": 1,
    }
    runs_root = config.path("output_root") / "runs"
    first = _old_run(runs_root, "run_001", 1)
    second = _old_run(runs_root, "run_002", 2)

    run_pipeline(config, _args(output_root=str(tmp_path / "manual")))

    assert first.exists()
    assert second.exists()


def test_two_runs_business_idempotent(tmp_path):
    config = _setup(tmp_path)
    m1 = run_pipeline(config, _args(output_root=str(tmp_path / "run1")))
    m2 = run_pipeline(config, _args(output_root=str(tmp_path / "run2")))
    assert m1["business_output_hash"] == m2["business_output_hash"]
    ids1 = json.loads((Path(m1["output_paths"]["review_queue"]).parent / "run_idempotency.json").read_text(encoding="utf-8"))["candidate_ids"]
    ids2 = json.loads((Path(m2["output_paths"]["review_queue"]).parent / "run_idempotency.json").read_text(encoding="utf-8"))["candidate_ids"]
    assert sorted(ids1) == sorted(ids2)


def test_incremental_cursor_advances_and_does_not_duplicate(tmp_path):
    config = _setup(tmp_path)
    run_pipeline(config, _args(since_last_success=True, output_root=str(tmp_path / "inc1")))
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    cursor = repo.get_scan_cursor("TW-2026-TNN-MAYOR", "news_article_id")
    assert cursor is not None
    assert cursor["last_article_id"] == 4
    repo.close()

    m2 = run_pipeline(config, _args(since_last_success=True, output_root=str(tmp_path / "inc2")))
    assert m2["articles_examined"] == 0
    assert m2["articles_matched"] == 0
    assert m2["total_candidate_status_counts"] == m1_statuses(tmp_path)


def m1_statuses(tmp_path):
    repo = CandidateRepository(tmp_path / "candidate_test.db")
    repo.connect()
    counts = repo.count_candidates_by_status()
    repo.close()
    return counts


def test_failed_run_does_not_advance_cursor(tmp_path):
    config = _setup(tmp_path)
    run_pipeline(config, _args(since_last_success=True, output_root=str(tmp_path / "ok")))
    bad = make_config(tmp_path)
    bad.raw["paths"]["news_db"] = str(tmp_path / "missing.db")
    with pytest.raises(Exception):
        run_pipeline(bad, _args(since_last_success=True, output_root=str(tmp_path / "bad")))
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    cursor = repo.get_scan_cursor("TW-2026-TNN-MAYOR", "news_article_id")
    repo.close()
    assert cursor["last_article_id"] == 4


def test_history_run_does_not_advance_incremental_cursor(tmp_path):
    config = _setup(tmp_path)
    run_pipeline(config, _args(since_last_success=True, output_root=str(tmp_path / "inc")))
    run_pipeline(config, _args(date_from="2026-07-01", date_to="2026-07-31", output_root=str(tmp_path / "hist")))
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    cursor = repo.get_scan_cursor("TW-2026-TNN-MAYOR", "news_article_id")
    repo.close()
    assert cursor["last_article_id"] == 4


def test_new_article_attaches_to_existing_candidate(tmp_path):
    config = _setup(tmp_path)
    m1 = run_pipeline(config, _args(output_root=str(tmp_path / "run1")))
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    before = {c["candidate_id"]: c for c in repo.list_candidates(limit=1000)}
    repo.close()

    conn = __import__("sqlite3").connect(config.path("news_db"))
    conn.execute(
        "INSERT INTO articles (id, source_id, source_name, category, title, url, "
        "published_at, fetched_at, position, summary) VALUES "
        "(5,'cna','中央社','politics','陳亭妃宣布參選台南市長 後續報導',"
        "'https://www.cna.com.tw/news/aipl/99999905.aspx','2026-07-10T10:00:00+08:00',"
        "'2026-07-10T10:00:00+08:00',5,'')"
    )
    conn.commit()
    conn.close()

    m2 = run_pipeline(config, _args(output_root=str(tmp_path / "run2")))
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    after = {c["candidate_id"]: c for c in repo.list_candidates(limit=1000)}
    repo.close()
    assert set(before) <= set(after)
    assert len(after) == len(before)
    assert any(a["article_count"] == 2 for a in after.values())


def test_no_duplicate_article_links_after_rerun(tmp_path):
    config = _setup(tmp_path)
    run_pipeline(config, _args())
    run_pipeline(config, _args())
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    dup = repo.conn.execute(
        "SELECT candidate_id, news_article_id, COUNT(*) c FROM candidate_event_articles "
        "GROUP BY candidate_id, news_article_id HAVING c > 1"
    ).fetchall()
    repo.close()
    assert dup == []


def test_candidate_db_reopenable_and_queryable(tmp_path):
    config = _setup(tmp_path)
    run_pipeline(config, _args())
    repo = CandidateRepository(config.path("candidate_db"))
    repo.connect()
    candidates = repo.list_candidates(status="review_required")
    assert len(candidates) >= 1
    cid = candidates[0]["candidate_id"]
    assert repo.get_candidate(cid)["candidate_id"] == cid
    assert repo.get_articles(cid)
    assert repo.get_assertions(cid)
    repo.close()
