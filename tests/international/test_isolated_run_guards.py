from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pytest

from app.main import COLLECTOR_MAP
from app.models import Article
from validation.international_media.run_isolated import (
    load_runs,
    run_isolated_collection,
    validate_isolation_config,
)


ROOT = Path(__file__).resolve().parents[2]
VALIDATION = ROOT / "validation" / "international_media"


def _valid_run(**overrides):
    payload = {
        "schema_version": "1.0",
        "run_id": "isolated-one",
        "generated_at": "2026-08-14T00:00:00+00:00",
        "production_database_used": False,
        "feishu_disabled": True,
        "notifier": "NullNotifier",
        "taiwan_sources_completed": True,
        "failed_sources": [],
        "notification_candidates": [],
        "inserted": 0,
        "duplicate_word_items": 0,
        "real_feishu_calls": 0,
        "per_source": {
            "reuters_international": {
                "fetched": 0,
                "parsed": 0,
                "inserted": 0,
                "fresh": 0,
                "relevant": 0,
                "important": 0,
                "errors": [],
            }
        },
    }
    payload.update(overrides)
    return payload


def _disabled_config():
    return {
        "sources": [
            {"id": source_id, "type": source_type, "url": f"fixture://{source_id}", "category": "international", "enabled": False}
            for source_id, source_type in (
                ("reuters_international", "reuters"),
                ("ft_alphaville", "ft_alphaville"),
                ("wsj_newsletter", "wsj_newsletter"),
                ("bloomberg_newsletter", "bloomberg_newsletter"),
            )
        ]
    }


def _generated_report(tmp_path, name="first"):
    reports = tmp_path / f"{name}_reports"
    result = run_isolated_collection(
        _disabled_config(), tmp_path / f"{name}.db", reports, True
    )
    return reports / f"{result.run_id}.json"


def test_isolated_runner_requires_nonproduction_paths_and_dry_run():
    result = validate_isolation_config(
        "validation/international_media/config.yaml",
        "validation/international_media/news.db",
        "validation/international_media/reports",
        True,
    )
    assert result.ok is True
    assert result.real_feishu_send is False


@pytest.mark.parametrize(
    "config_path,db_path,reports_path,dry_run",
    [
        ("config/sources.yaml", "validation/international_media/news.db", "validation/international_media/reports", True),
        ("validation/international_media/config.yaml", "data/news.db", "validation/international_media/reports", True),
        ("validation/international_media/config.yaml", "validation/international_media/news.db", "data/reports", True),
        ("validation/international_media/config.yaml", "validation/international_media/news.db", "validation/international_media/reports", False),
    ],
)
def test_isolated_runner_rejects_production_or_non_dry_run_paths(config_path, db_path, reports_path, dry_run):
    result = validate_isolation_config(config_path, db_path, reports_path, dry_run)
    assert result.ok is False
    assert result.real_feishu_send is False
    assert result.reason


@pytest.mark.parametrize(
    "field,value",
    [
        ("schema_version", "9.9"),
        ("run_id", ""),
        ("production_database_used", True),
        ("feishu_disabled", False),
        ("notifier", "FeishuNotifier"),
        ("real_feishu_calls", 1),
        ("inserted", -1),
        ("duplicate_word_items", "0"),
        ("per_source", []),
    ],
)
def test_load_runs_rejects_forged_top_level_contract(tmp_path, field, value):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(_valid_run(**{field: value})), encoding="utf-8")
    second.write_text(json.dumps(_valid_run(run_id="isolated-two")), encoding="utf-8")

    with pytest.raises(ValueError):
        load_runs(first, second)


@pytest.mark.parametrize(
    "metrics",
    [
        {"fetched": "0", "parsed": 0, "inserted": 0, "fresh": 0, "relevant": 0, "important": 0, "errors": []},
        {"fetched": 0, "parsed": 0, "inserted": -1, "fresh": 0, "relevant": 0, "important": 0, "errors": []},
        {"fetched": 0, "parsed": 0, "inserted": 0, "fresh": 0, "relevant": 0, "important": 0, "errors": [1]},
        {"fetched": 0, "parsed": 0, "inserted": 0, "fresh": 0, "relevant": 0, "important": 0},
    ],
)
def test_load_runs_rejects_forged_per_source_metrics(tmp_path, metrics):
    first = tmp_path / "first.json"
    second = tmp_path / "second.json"
    first.write_text(json.dumps(_valid_run(per_source={"bad": metrics})), encoding="utf-8")
    second.write_text(json.dumps(_valid_run(run_id="isolated-two")), encoding="utf-8")

    with pytest.raises(ValueError):
        load_runs(first, second)


def test_load_runs_accepts_strict_valid_pair(tmp_path):
    first = _generated_report(tmp_path, "first")
    second = _generated_report(tmp_path, "second")

    one, two = load_runs(first, second)
    assert one.run_id != two.run_id
    assert one.real_feishu_calls == two.real_feishu_calls == 0


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload.update(schema_version="1.0"),
        lambda payload: payload["provenance"].update(runner_sha256="0" * 64),
        lambda payload: payload["config"].update(sha256="0" * 64),
        lambda payload: payload["counts"].update(inserted=1),
    ],
)
def test_load_runs_rejects_package_a_tampering(tmp_path, mutator):
    first = _generated_report(tmp_path, "first")
    second = _generated_report(tmp_path, "second")
    payload = json.loads(first.read_text(encoding="utf-8"))
    mutator(payload)
    first.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError):
        load_runs(first, second)


def test_load_runs_rejects_tampered_artifact_bytes(tmp_path):
    first = _generated_report(tmp_path, "first")
    second = _generated_report(tmp_path, "second")
    payload = json.loads(first.read_text(encoding="utf-8"))
    artifact = Path(payload["artifacts"]["config_snapshot"]["path"])
    artifact.write_text(artifact.read_text(encoding="utf-8") + "\n# tampered\n", encoding="utf-8")
    with pytest.raises(ValueError, match="SHA256"):
        load_runs(first, second)


def test_load_runs_rejects_tampered_database_bytes(tmp_path):
    first = _generated_report(tmp_path, "first")
    second = _generated_report(tmp_path, "second")
    payload = json.loads(first.read_text(encoding="utf-8"))
    database = Path(payload["paths"]["database"])
    with database.open("ab") as handle:
        handle.write(b"tampered-database-byte")
    with pytest.raises(ValueError, match="database .*mismatch"):
        load_runs(first, second)


def test_load_runs_rejects_cross_run_artifact_reference(tmp_path):
    first = _generated_report(tmp_path, "first")
    second = _generated_report(tmp_path, "second")
    first_payload = json.loads(first.read_text(encoding="utf-8"))
    second_payload = json.loads(second.read_text(encoding="utf-8"))
    first_payload["artifacts"]["config_snapshot"] = second_payload["artifacts"]["config_snapshot"]
    first_payload["config"]["snapshot_path"] = second_payload["config"]["snapshot_path"]
    first.write_text(json.dumps(first_payload), encoding="utf-8")
    with pytest.raises(ValueError, match="run directory"):
        load_runs(first, second)


@pytest.mark.parametrize(
    "mutator",
    [
        lambda payload: payload["counts"].update(event_count=1),
        lambda payload: payload["counts"].update(coverage_items=1),
        lambda payload: payload["counts"].update(duplicate_word_items=1),
        lambda payload: payload["word"].update(duplicate_items=1),
        lambda payload: payload["translation"].update(articles=1),
    ],
)
def test_load_runs_rejects_inconsistent_derived_counts(tmp_path, mutator):
    first = _generated_report(tmp_path, "first")
    second = _generated_report(tmp_path, "second")
    payload = json.loads(first.read_text(encoding="utf-8"))
    mutator(payload)
    first.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="count|Word|translation"):
        load_runs(first, second)


def test_load_runs_reparses_config_snapshot_and_rejects_enabled_id_drift(tmp_path):
    first = _generated_report(tmp_path, "first")
    second = _generated_report(tmp_path, "second")
    for report in (first, second):
        payload = json.loads(report.read_text(encoding="utf-8"))
        snapshot = Path(payload["config"]["snapshot_path"])
        snapshot_payload = snapshot.read_text(encoding="utf-8").replace("enabled: false", "enabled: true", 1)
        snapshot.write_text(snapshot_payload, encoding="utf-8")
        new_hash = __import__("hashlib").sha256(snapshot.read_bytes()).hexdigest()
        payload["config"]["sha256"] = new_hash
        payload["artifacts"]["config_snapshot"]["sha256"] = new_hash
        report.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="config snapshot"):
        load_runs(first, second)


def test_package_a_payload_matches_schema_property_contract(tmp_path):
    schema = json.loads(
        (VALIDATION / "isolated_run_schema.json").read_text(encoding="utf-8")
    )
    report = _generated_report(tmp_path)
    payload = json.loads(report.read_text(encoding="utf-8"))
    assert set(payload) == set(schema["required"])
    assert schema["additionalProperties"] is False
    assert schema["properties"]["provenance"]["additionalProperties"] is False
    assert schema["properties"]["artifacts"]["$ref"] == "#/$defs/artifacts"
    assert schema["$defs"]["artifacts"]["additionalProperties"] is False
    assert set(payload["artifacts"]) == {
        "config_snapshot", "health_snapshot", "dedup_snapshot", "database_metadata", "word"
    }
    assert all(set(item) == {"path", "sha256"} for item in payload["artifacts"].values())


def test_load_runs_rejects_superseded_v1_and_old_v2(tmp_path):
    first = tmp_path / "v1.json"
    second = tmp_path / "v2.json"
    first.write_text(json.dumps(_valid_run()), encoding="utf-8")
    old_v2 = _valid_run(schema_version="2.0")
    old_v2["provenance"] = {"code_handoff": "wave6-b"}
    second.write_text(json.dumps(old_v2), encoding="utf-8")
    with pytest.raises(ValueError, match="superseded"):
        load_runs(first, second)


def test_constructor_failure_is_isolated_and_does_not_call_close(monkeypatch, tmp_path):
    close_calls = []

    class ConstructorFailure:
        def __init__(self, _source):
            raise RuntimeError("constructor failed")

    class GoodCollector:
        def __init__(self, _source):
            pass

        def collect(self):
            now = datetime.now(timezone.utc)
            return [Article("good", "Good", "international", "Taiwan update", "https://example.test/good", now, now, 1)]

        def close(self):
            close_calls.append("good")

    monkeypatch.setitem(COLLECTOR_MAP, "constructor_failure", ConstructorFailure)
    monkeypatch.setitem(COLLECTOR_MAP, "good_wave0", GoodCollector)
    result = run_isolated_collection(
        {
            "sources": [
                {
                    "id": "reuters_international",
                    "type": "constructor_failure",
                    "url": "https://example.test/reuters",
                    "category": "international",
                    "enabled": True,
                },
                {
                    "id": "ft_alphaville",
                    "type": "good_wave0",
                    "url": "https://example.test/ft",
                    "category": "international",
                    "enabled": True,
                },
                {
                    "id": "wsj_newsletter",
                    "type": "wsj_newsletter",
                    "url": "mailbox://InternationalNews/wsj",
                    "category": "international",
                    "enabled": False,
                },
                {
                    "id": "bloomberg_newsletter",
                    "type": "bloomberg_newsletter",
                    "url": "mailbox://InternationalNews/bloomberg",
                    "category": "international",
                    "enabled": False,
                },
            ]
        },
        tmp_path / "news.db",
        tmp_path / "reports",
        True,
    )

    assert result.failed_sources == ["reuters_international"]
    assert "constructor failed" in result.per_source["reuters_international"]["errors"][0]
    assert result.per_source["ft_alphaville"]["inserted"] == 1
    assert close_calls == ["good"]


@pytest.mark.parametrize("config", [{}, {"sources": []}])
def test_runner_rejects_missing_or_empty_sources_before_database(tmp_path, config):
    with pytest.raises(ValueError, match=r"CONFIG_INVALID.*sources"):
        run_isolated_collection(
            config,
            tmp_path / "missing_sources.db",
            tmp_path / "reports",
            True,
        )
    assert not (tmp_path / "missing_sources.db").exists()
    assert not (tmp_path / "reports").exists()


def test_unknown_enabled_collector_is_config_invalid_before_construction(
    monkeypatch, tmp_path
):
    constructor_calls = []

    class MustNotConstruct:
        def __init__(self, _source):
            constructor_calls.append("constructed")

    monkeypatch.setitem(COLLECTOR_MAP, "unknown_wave0", MustNotConstruct)
    config = {
        "sources": [
            {
                "id": "reuters_international",
                "type": "not_in_collector_map",
                "url": "https://example.test/reuters",
                "category": "international",
                "enabled": True,
            },
            {
                "id": "ft_alphaville",
                "type": "ft_alphaville",
                "url": "https://example.test/ft",
                "category": "international",
                "enabled": False,
            },
            {
                "id": "wsj_newsletter",
                "type": "wsj_newsletter",
                "url": "mailbox://InternationalNews/wsj",
                "category": "international",
                "enabled": False,
            },
            {
                "id": "bloomberg_newsletter",
                "type": "bloomberg_newsletter",
                "url": "mailbox://InternationalNews/bloomberg",
                "category": "international",
                "enabled": False,
            },
        ]
    }

    with pytest.raises(ValueError, match=r"CONFIG_INVALID.*collector type"):
        run_isolated_collection(
            config,
            tmp_path / "unknown_type.db",
            tmp_path / "reports",
            True,
        )
    assert constructor_calls == []
    assert not (tmp_path / "unknown_type.db").exists()
    assert not (tmp_path / "reports").exists()


def test_cli_unknown_collector_is_nonzero_without_success_record():
    config_path = VALIDATION / "cli_invalid_config.yaml"
    db = VALIDATION / "cli_invalid_news.db"
    reports = VALIDATION / "cli_invalid_reports"
    config = {
        "sources": [
            {
                "id": "reuters_international",
                "type": "not_in_collector_map",
                "url": "https://example.test/reuters",
                "category": "international",
                "enabled": True,
            },
            {
                "id": "ft_alphaville",
                "type": "ft_alphaville",
                "url": "https://example.test/ft",
                "category": "international",
                "enabled": False,
            },
            {
                "id": "wsj_newsletter",
                "type": "wsj_newsletter",
                "url": "mailbox://InternationalNews/wsj",
                "category": "international",
                "enabled": False,
            },
            {
                "id": "bloomberg_newsletter",
                "type": "bloomberg_newsletter",
                "url": "mailbox://InternationalNews/bloomberg",
                "category": "international",
                "enabled": False,
            },
        ]
    }
    before = set(VALIDATION.glob("isolated_run_*.json"))
    config_path.write_text(json.dumps(config), encoding="utf-8")
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(VALIDATION / "run_isolated.py"),
                "--config",
                str(config_path),
                "--db",
                str(db),
                "--reports",
                str(reports),
                "--dry-run",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "DISABLE_FEISHU_SEND": "true"},
            check=False,
        )
        assert completed.returncode == 1
        assert "CONFIG_INVALID" in completed.stdout
        assert set(VALIDATION.glob("isolated_run_*.json")) == before
        assert not db.exists()
        assert not reports.exists()
    finally:
        config_path.unlink(missing_ok=True)
        for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
            path.unlink(missing_ok=True)
        if reports.exists():
            for path in reports.glob("*"):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            reports.rmdir()


def test_cli_smoke_uses_four_disabled_sources_and_isolated_paths():
    db = VALIDATION / "cli_smoke_news.db"
    reports = VALIDATION / "cli_smoke_reports"
    before = set(VALIDATION.glob("isolated_run_*.json"))
    try:
        completed = subprocess.run(
            [
                sys.executable,
                str(VALIDATION / "run_isolated.py"),
                "--config",
                str(VALIDATION / "config.yaml"),
                "--db",
                str(db),
                "--reports",
                str(reports),
                "--dry-run",
            ],
            cwd=ROOT,
            capture_output=True,
            text=True,
            env={**os.environ, "DISABLE_FEISHU_SEND": "true"},
            check=False,
        )
        assert completed.returncode == 0, completed.stdout + completed.stderr
        run_records = set(VALIDATION.glob("isolated_run_*.json")) - before
        assert len(run_records) == 1
        payload = json.loads(next(iter(run_records)).read_text(encoding="utf-8"))
        assert set(payload["per_source"]) == {
            "reuters_international",
            "ft_alphaville",
            "wsj_newsletter",
            "bloomberg_newsletter",
        }
        assert payload["production_database_used"] is False
        assert payload["feishu_disabled"] is True
        assert payload["real_feishu_calls"] == 0
    finally:
        for path in set(VALIDATION.glob("isolated_run_*.json")) - before:
            path.unlink(missing_ok=True)
        for path in (db, db.with_name(db.name + "-wal"), db.with_name(db.name + "-shm")):
            path.unlink(missing_ok=True)
        if reports.exists():
            for path in reports.glob("*"):
                if path.is_dir():
                    shutil.rmtree(path)
                else:
                    path.unlink(missing_ok=True)
            reports.rmdir()
