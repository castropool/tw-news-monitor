from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from app.main import COLLECTOR_MAP
from app.models import Article
from validation.international_media.run_isolated import run_isolated_collection


def test_live_config_has_only_reuters_and_ft_enabled():
    import yaml

    payload = yaml.safe_load(
        (Path("validation/international_media") / "config.live.yaml").read_text(
            encoding="utf-8"
        )
    )
    enabled = {item["id"] for item in payload["sources"] if item["enabled"]}
    assert enabled == {"reuters_international", "ft_alphaville"}
    assert all(item["enabled"] is False for item in payload["sources"] if item["id"] in {"wsj_newsletter", "bloomberg_newsletter"})
    assert payload["notifier"]["type"] == "NullNotifier"
    assert payload["notifier"]["dry_run"] is True
    assert payload["catch_up"]["enabled"] is False
    assert set(payload["paths"]) == {"database", "reports", "health", "dedup"}


def test_fake_pipeline_computes_events_word_and_candidates(monkeypatch, tmp_path):
    now = datetime.now(timezone.utc)
    rows = {
        "reuters_international": ("Reuters", "China launches military drills near Taiwan 中國宣布軍事演習威脅台灣", "https://example.test/w6-r"),
        "ft_alphaville": ("Financial Times", "China holds military exercises near Taiwan 中國宣布軍事演習威脅台灣", "https://example.test/w6-f"),
        "wsj_newsletter": ("Wall Street Journal", "China begins military drills near Taiwan 中國宣布軍事演習威脅台灣", "https://example.test/w6-w"),
        "bloomberg_newsletter": ("Bloomberg", "China starts military drills near Taiwan 中國宣布軍事演習威脅台灣", "https://example.test/w6-b"),
    }

    class FakeCollector:
        def __init__(self, source):
            self.source = source
            self.last_outcome = None

        def collect(self):
            name, title, url = rows[self.source["id"]]
            return [Article(self.source["id"], name, "international", title, url, now, now, 1, summary="軍方公布演習安排。")]

        def close(self):
            return None

    monkeypatch.setitem(COLLECTOR_MAP, "wave6_fake", FakeCollector)
    config = {
        "sources": [
            {"id": source_id, "name": name, "type": "wave6_fake", "url": "fixture://wave6", "category": "international", "enabled": True}
            for source_id, (name, _title, _url) in rows.items()
        ]
    }
    result = run_isolated_collection(config, tmp_path / "isolated.db", tmp_path / "reports", True)
    payload = result.to_dict()

    assert payload["taiwan_sources_completed"] is False
    assert payload["taiwan_sources"]["status"] == "not_run"
    assert payload["counts"]["fetched"] == 4
    assert payload["counts"]["inserted"] == 4
    assert payload["counts"]["fresh"] == 4
    assert payload["counts"]["relevant"] == 4
    assert payload["counts"]["important"] == 4
    assert payload["counts"]["event_count"] == 1
    assert payload["counts"]["canonical_count"] == 1
    assert payload["counts"]["coverage_items"] == 4
    assert payload["duplicate_word_items"] == 3
    assert payload["word"]["status"] == "generated"
    assert Path(payload["word"]["path"]).is_file()
    assert payload["translation"]["status"] == "fallback"
    assert payload["translation"]["body_fetch_count"] == 0
    assert len(payload["notification_candidates"]) == 1
    assert payload["real_feishu_calls"] == 0
    assert all(metrics["inserted"] == 1 for metrics in payload["per_source"].values())


def test_generated_evidence_does_not_use_placeholder_counts(monkeypatch, tmp_path):
    """A source failure remains visible and is not replaced with zero fixtures."""

    class FailingCollector:
        def __init__(self, _source):
            raise RuntimeError("fixture failure")

    monkeypatch.setitem(COLLECTOR_MAP, "wave6_failure", FailingCollector)
    config = {
        "sources": [
            {"id": "reuters_international", "name": "Reuters", "type": "wave6_failure", "url": "fixture://failure", "category": "international", "enabled": True},
            {"id": "ft_alphaville", "name": "Financial Times", "type": "wave6_failure", "url": "fixture://failure-ft", "category": "international", "enabled": False},
            {"id": "wsj_newsletter", "name": "Wall Street Journal", "type": "wave6_failure", "url": "fixture://failure-w", "category": "international", "enabled": False},
            {"id": "bloomberg_newsletter", "name": "Bloomberg", "type": "wave6_failure", "url": "fixture://failure-b", "category": "international", "enabled": False},
        ]
    }
    result = run_isolated_collection(config, tmp_path / "isolated.db", tmp_path / "reports", True)
    report = json.loads((tmp_path / "reports" / f"{result.run_id}.json").read_text(encoding="utf-8"))
    assert report["failed_sources"] == ["reuters_international"]
    assert report["per_source"]["reuters_international"]["errors"]
    assert report["counts"]["inserted"] == 0
    assert report["word"]["status"] == "not_run"
    assert report["counts"]["duplicate_word_items"] == 0
