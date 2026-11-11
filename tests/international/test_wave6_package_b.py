from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from app.main import COLLECTOR_MAP
from app.models import Article
from app.notification_candidates import NotificationDedupStore
from validation.international_media.run_isolated import (
    _deliver_event_candidates,
    load_runs,
    run_isolated_collection,
)


NOW = datetime.now(timezone.utc) - timedelta(minutes=1)


def _config(notifier="RecordingNotifier"):
    return {
        "notifier": {
            "type": notifier,
            "dry_run": True,
            "disable_feishu_send": True,
        },
        "sources": [
            {
                "id": source_id,
                "name": name,
                "type": "wave6_package_b_fake",
                "url": f"fixture://{source_id}",
                "category": "international",
                "enabled": True,
            }
            for source_id, name in (
                ("reuters_international", "Reuters"),
                ("ft_alphaville", "Financial Times"),
                ("wsj_newsletter", "Wall Street Journal"),
                ("bloomberg_newsletter", "Bloomberg"),
            )
        ],
    }


def test_package_b_persists_notification_dedup_across_runs_with_new_coverage(
    monkeypatch, tmp_path
):
    calls = 0

    class FakeCollector:
        def __init__(self, source):
            self.source = source

        def collect(self):
            nonlocal calls
            calls += 1
            suffix = calls
            source_id = self.source["id"]
            source_name = self.source["name"]
            return [
                Article(
                    source_id,
                    source_name,
                    "international",
                        "China launches military drills near Taiwan 中國宣布軍事演習威脅台灣",
                    f"https://example.test/package-b/{source_id}/{suffix}",
                    NOW,
                    NOW,
                    1,
                    summary="The military announced exercises near Taiwan.",
                )
            ]

        def close(self):
            return None

    monkeypatch.setitem(COLLECTOR_MAP, "wave6_package_b_fake", FakeCollector)
    config = _config()
    first = run_isolated_collection(config, tmp_path / "news.db", tmp_path / "reports", True)
    second = run_isolated_collection(config, tmp_path / "news.db", tmp_path / "reports", True)

    first_payload = first.to_dict()
    second_payload = second.to_dict()
    assert first_payload["notifier"] == "RecordingNotifier"
    assert first_payload["real_feishu_calls"] == 0
    assert len(first_payload["notification_candidates"]) == 1
    assert second.inserted == 4
    assert second_payload["notification_candidates"] == []
    assert second_payload["counts"]["notification_candidate_count"] == 0
    assert first_payload["paths"]["dedup"] == second_payload["paths"]["dedup"]
    dedup_path = tmp_path / "reports" / "notification_dedup.json"
    assert dedup_path.is_file()
    assert json.loads(dedup_path.read_text(encoding="utf-8"))["entries"]

    first_report = tmp_path / "reports" / f"{first.run_id}.json"
    second_report = tmp_path / "reports" / f"{second.run_id}.json"
    loaded_first, loaded_second = load_runs(first_report, second_report)
    assert loaded_first.real_feishu_calls == loaded_second.real_feishu_calls == 0


def test_package_b_failed_send_does_not_mark_store(tmp_path):
    class Candidate:
        dedup_key = "event:package-b-failure"

    class FailingNotifier:
        def send_event_candidates(self, _candidates):
            return False

    store = NotificationDedupStore(tmp_path / "dedup.json")
    assert _deliver_event_candidates(FailingNotifier(), [Candidate()], store, NOW) is False
    assert store.is_seen("event:package-b-failure", now=NOW) is False


def test_package_b_word_is_run_specific_and_source_failure_is_real(monkeypatch, tmp_path):
    class Collector:
        def __init__(self, source):
            self.source = source

        def collect(self):
            if self.source["id"] == "ft_alphaville":
                raise RuntimeError("fixture source failure")
            return [
                Article(
                    self.source["id"],
                    self.source["name"],
                    "international",
                    "China launches military drills near Taiwan 中國宣布軍事演習威脅台灣",
                    f"https://example.test/package-b/{self.source['id']}",
                    NOW,
                    NOW,
                    1,
                    summary="The military announced exercises near Taiwan.",
                )
            ]

        def close(self):
            return None

    monkeypatch.setitem(COLLECTOR_MAP, "wave6_package_b_failure", Collector)
    config = _config(notifier="NullNotifier")
    for source in config["sources"]:
        source["type"] = "wave6_package_b_failure"
    result = run_isolated_collection(config, tmp_path / "news.db", tmp_path / "reports", True)
    payload = result.to_dict()
    assert payload["failed_sources"] == ["ft_alphaville"]
    assert payload["per_source"]["ft_alphaville"]["errors"]
    assert payload["word"]["status"] == "generated"
    word_path = payload["word"]["path"]
    assert word_path.endswith(f"{result.run_id}_word.docx")
    assert payload["counts"]["duplicate_word_items"] == result.duplicate_word_items
