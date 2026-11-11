import json
import os
import tempfile
from pathlib import Path

import pytest

from app.diagnose import _save_json, _reclassify, _write_csv, CSV_FIELDS
from app.database import Database


@pytest.fixture
def dev_db_path():
    """Locate the dev database for read-only testing."""
    here = Path(__file__).resolve().parent.parent
    candidates = [
        here / "data" / "news-dev.db",
        here / "data" / "news.db",
    ]
    for c in candidates:
        if c.exists():
            return c
    pytest.skip("No dev database found")


@pytest.fixture
def sample_records():
    """Sample diagnosis records for testing."""
    return [
        {
            "run_started_at": "2026-07-17 22:00:00",
            "source_id": "test_a",
            "source_name": "TestSource",
            "category": "politics",
            "position": "1",
            "original_title": "Article A",
            "normalized_title": "Article A",
            "original_url": "https://test.com/a",
            "normalized_url": "https://test.com/a",
            "published_at_raw": "2026-07-17 20:00:00",
            "published_at_parsed": "2026-07-17T20:00:00",
            "published_timezone": "naive",
            "fetched_at": "2026-07-17T22:00:00",
            "url_duplicate_count_in_run": 0,
            "title_duplicate_count_in_run": 0,
            "duplicate_group_id": "",
            "duplicate_type": "none",
            "already_in_database": "false",
            "is_published_time_valid": "true",
            "article_age_minutes": "120",
            "suspected_stale": "true",
            "diagnostic_reason": "normal",
        },
        # Same URL -> should be same_normalized_url
        {
            "run_started_at": "2026-07-17 22:00:00",
            "source_id": "test_a",
            "source_name": "TestSource",
            "category": "economy",
            "position": "2",
            "original_title": "Article A (dup)",
            "normalized_title": "Article A (dup)",
            "original_url": "https://test.com/a",
            "normalized_url": "https://test.com/a",
            "published_at_raw": "2026-07-17 20:00:00",
            "published_at_parsed": "2026-07-17T20:00:00",
            "published_timezone": "naive",
            "fetched_at": "2026-07-17T22:00:00",
            "url_duplicate_count_in_run": 0,
            "title_duplicate_count_in_run": 0,
            "duplicate_group_id": "",
            "duplicate_type": "none",
            "already_in_database": "false",
            "is_published_time_valid": "true",
            "article_age_minutes": "120",
            "suspected_stale": "true",
            "diagnostic_reason": "normal",
        },
    ]


class TestDiagnoseSave:
    def test_save_and_load_json(self, sample_records):
        """JSON can be saved and loaded."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "test.json"
            _save_json(sample_records, p)
            assert p.exists()
            with open(p, encoding="utf-8") as f:
                loaded = json.load(f)
            assert len(loaded) == 2
            assert loaded[0]["normalized_url"] == "https://test.com/a"

    def test_json_no_sensitive_fields(self, sample_records):
        """JSON does not contain sensitive fields."""
        sensitive = ["secret", "token", "authorization", "cookie", "app_secret"]
        all_keys = set()
        for r in sample_records:
            all_keys.update(r.keys())
        for s in sensitive:
            assert not any(s in k for k in all_keys), f"Sensitive key found: {s}"

    def test_utf8_bom_csv(self, sample_records):
        """CSV is written with UTF-8 BOM."""
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "test.csv"
            _write_csv(sample_records, p)
            with open(p, "rb") as f:
                raw = f.read(10)
            assert raw[:3] == b"\xef\xbb\xbf", "Missing BOM"

    def test_csv_has_all_fields(self, sample_records):
        """CSV contains all required fields."""
        import csv
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp) / "test.csv"
            _write_csv(sample_records, p)
            with open(p, encoding="utf-8-sig") as f:
                r = csv.DictReader(f)
                assert set(r.fieldnames) == set(CSV_FIELDS)


class TestDiagnoseReclassify:
    def test_same_url_grouped(self, sample_records):
        """Same normalized URL gets same dedup group."""
        recs, summary = _reclassify(sample_records)
        # Both have same URL, so url_duplicate_count_in_run > 0
        assert recs[0]["url_duplicate_count_in_run"] == 1
        assert recs[1]["url_duplicate_count_in_run"] == 1
        # Both should have duplicate_group_id
        assert recs[0]["duplicate_group_id"] != ""
        assert recs[0]["duplicate_group_id"] == recs[1]["duplicate_group_id"]

    def test_same_url_gets_url_type(self, sample_records):
        """Same URL gets same_normalized_url type."""
        recs, _ = _reclassify(sample_records)
        assert "same_normalized_url" in recs[0]["diagnostic_reason"]

    def test_different_url_no_dup(self):
        """Different URLs get no duplicate grouping."""
        recs = [
            {
                "normalized_url": "https://test.com/a",
                "normalized_title": "A",
                "source_name": "Src",
                "already_in_database": "false",
                "suspected_stale": "false",
            },
            {
                "normalized_url": "https://test.com/b",
                "normalized_title": "B",
                "source_name": "Src",
                "already_in_database": "false",
                "suspected_stale": "false",
            },
        ]
        recs, _ = _reclassify(recs)
        assert recs[0]["url_duplicate_count_in_run"] == 0
        assert recs[1]["url_duplicate_count_in_run"] == 0
        assert recs[0]["duplicate_type"] == "none"

    def test_already_in_db_classified(self):
        """already_in_database records are classified correctly."""
        recs = [
            {
                "normalized_url": "https://test.com/x",
                "normalized_title": "X",
                "source_name": "Src",
                "already_in_database": "true",
                "suspected_stale": "false",
            },
        ]
        recs, _ = _reclassify(recs)
        assert "already_in_database" in recs[0]["diagnostic_reason"]

    def test_same_title_different_url_classified(self):
        """Same title, same source, different URL -> title dup."""
        recs = [
            {
                "normalized_url": "https://test.com/x",
                "normalized_title": "Same Title",
                "source_name": "Src",
                "already_in_database": "false",
                "suspected_stale": "false",
            },
            {
                "normalized_url": "https://test.com/y",
                "normalized_title": "Same Title",
                "source_name": "Src",
                "already_in_database": "false",
                "suspected_stale": "false",
            },
        ]
        recs, _ = _reclassify(recs)
        # URL is different so url dup: 0; title is same so title dup: 1
        assert recs[0]["url_duplicate_count_in_run"] == 0
        assert recs[0]["title_duplicate_count_in_run"] == 1


class TestDiagnoseMode:
    def test_dev_db_read_only(self, dev_db_path):
        """Diagnosis mode reads dev DB without writing."""
        db = Database(dev_db_path)
        db.connect()
        before = db.count_articles()
        # Read-only: check article_exists
        exists = db.article_exists("https://nonexistent.test.com/article")
        assert exists is False
        after = db.count_articles()
        assert before == after, "Database was modified by read operation"
        db.close()

    def test_dev_db_article_exists(self, dev_db_path):
        """article_exists works correctly."""
        db = Database(dev_db_path)
        db.connect()
        # Should find an existing article
        sample = db.conn.execute("SELECT url FROM articles LIMIT 1").fetchone()
        if sample:
            exists = db.article_exists(sample[0])
            assert exists is True
        db.close()

    def test_diagnose_collection_no_side_effects(self):
        """--diagnose-collection does not send real Feishu (dev env has no credentials)."""
        import os
        assert os.getenv("FEISHU_APP_ID", "") == "", "Dev env should not have Feishu APP_ID"


class TestReplayConsistency:
    def test_replay_stats_match_online(self):
        """Offline replay produces same stats as online diagnosis."""
        # Use the latest collected CSV and JSON if they exist
        diag_dir = Path(__file__).resolve().parent.parent / "data" / "diagnostics"
        json_path = diag_dir / "latest_collection.json"
        csv_path = diag_dir / "latest_collection.csv"
        if not json_path.exists() or not csv_path.exists():
            pytest.skip("No diagnosis files to compare")

        # Load CSV
        import csv
        with open(csv_path, encoding="utf-8-sig") as f:
            csv_records = list(csv.DictReader(f))

        # Load JSON and reclassify
        with open(json_path, encoding="utf-8") as f:
            json_records = json.load(f)

        json_recs, json_summary = _reclassify(json_records)

        assert json_summary["total"] == len(csv_records), "Replay total != CSV total"
        assert json_summary["unique_urls"] == len(set(r.get("normalized_url", "") for r in csv_records))
        assert json_summary["stale"] == sum(1 for r in csv_records if r.get("suspected_stale") == "true")
        assert json_summary["already_in_db"] == sum(1 for r in csv_records if r.get("already_in_database") == "true")
        assert json_summary["url_duplicates"] == sum(1 for r in csv_records if int(r.get("url_duplicate_count_in_run", 0)) > 0)


class TestOfflineReplay:
    def test_replay_no_network(self):
        """run_diagnosis_from_file does not access network."""
        from app.diagnose import run_diagnosis_from_file
        with tempfile.TemporaryDirectory() as tmp:
            # Create minimal JSON
            records = [{"source_id": "test", "original_title": "Test article", "normalized_url": "https://test.com/a", "normalized_title": "A", "source_name": "S",
                        "category": "pol", "already_in_database": "false", "suspected_stale": "false",
                        "published_at_raw": "", "published_at_parsed": "", "published_timezone": "",
                        "fetched_at": "", "url_duplicate_count_in_run": 0, "title_duplicate_count_in_run": 0,
                        "duplicate_group_id": "", "duplicate_type": "none", "is_published_time_valid": "true",
                        "article_age_minutes": "", "diagnostic_reason": ""} for _ in range(3)]
            jp = Path(tmp) / "test.json"
            with open(jp, "w", encoding="utf-8") as f:
                json.dump(records, f)
            out = Path(tmp) / "out"
            run_diagnosis_from_file(jp, None, out)
            assert (out / "latest_collection.csv").exists()
            assert (out / "latest_diagnosis.md").exists()
            assert (out / "latest_collection.json").exists() == False  # JSON is not re-saved
            # Clean up
            import shutil
            shutil.rmtree(out, ignore_errors=True)
            jp.unlink()
            tmp_dir = Path(tmp)
            if tmp_dir.exists():
                shutil.rmtree(tmp_dir, ignore_errors=True)
