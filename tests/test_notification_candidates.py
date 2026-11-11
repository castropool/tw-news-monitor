from datetime import datetime, timezone
import json
import threading
import time

from app.importance import ImportanceResult
from app.international import load_international_config
from app.international_events import cluster_international_articles
from app.international_events import EventCluster
from app.models import Article
from app.notification_candidates import (
    NotificationDedupStore,
    build_notification_candidates,
    deduplicate_notification_candidates,
)


NOW = datetime(2026, 8, 15, 2, tzinfo=timezone.utc)
CONFIG = load_international_config()


def _article(title, source_id="reuters_international", source_name="Reuters", url=None):
    return Article(
        source_id=source_id,
        source_name=source_name,
        category="international",
        title=title,
        url=url or f"https://example.test/{source_id}/{title[:5]}",
        published_at=NOW,
        fetched_at=NOW,
        position=1,
        summary="Public teaser: the administration announced the action for Taiwan.",
        access_level="metadata_only",
    )


def test_normal_relevant_is_word_only_and_important_cluster_has_one_candidate():
    normal = _article("Taiwan company reports routine quarterly sales", url="https://example.test/normal")
    important = _article("US approves arms sales to Taiwan", url="https://example.test/important")
    clusters, _ = cluster_international_articles([normal, important], CONFIG)
    results = [
        (normal, ImportanceResult(score=30, level="normal", reasons=["routine"])),
        (important, ImportanceResult(score=70, level="important", reasons=["arms"])),
    ]
    candidates = build_notification_candidates(
        clusters,
        results,
        {"fresh_articles": [normal, important], "catch_up_urls": set(), "baseline_excluded": []},
        NOW,
    )
    assert len(candidates) == 1
    assert candidates[0].canonical_url == important.url
    assert candidates[0].notifiable is True
    assert candidates[0].importance_level == "important"


def test_baseline_and_duplicate_candidates_are_suppressed():
    article = _article("US approves arms sales to Taiwan", url="https://example.test/baseline")
    clusters, _ = cluster_international_articles([article], CONFIG)
    results = [(article, ImportanceResult(score=90, level="critical", reasons=["arms"]))]
    state = {"fresh_articles": [article], "catch_up_urls": {article.url}, "baseline_excluded": [article]}
    assert build_notification_candidates(clusters, results, state, NOW) == []

    state = {"fresh_articles": [article], "catch_up_urls": set(), "baseline_excluded": []}
    candidate = build_notification_candidates(clusters, results, state, NOW)[0]
    duplicate = build_notification_candidates(clusters, results, state, NOW)[0]
    assert len(deduplicate_notification_candidates([candidate, duplicate])) == 1


def test_old_canonical_with_fresh_coverage_uses_latest_fresh_evidence():
    old = _article(
        "US approves arms sales to Taiwan",
        url="https://example.test/old-canonical",
    )
    old.published_at = datetime(2026, 8, 14, 23, tzinfo=timezone.utc)
    fresh = _article(
        "US approves new arms sales package for Taiwan",
        source_id="ft_alphaville",
        source_name="Financial Times",
        url="https://example.test/fresh-coverage",
    )
    fresh.published_at = datetime(2026, 8, 15, 2, tzinfo=timezone.utc)
    cluster = EventCluster(
        event_id="evt_stable_arms",
        canonical=old,
        members=[old, fresh],
        coverage=[old, fresh],
    )
    results = [
        (old, ImportanceResult(score=90, level="critical", reasons=["arms"])),
        (fresh, ImportanceResult(score=90, level="critical", reasons=["arms"])),
    ]
    candidates = build_notification_candidates(
        [cluster],
        results,
        {"fresh_articles": [fresh], "catch_up_urls": {old.url}},
        NOW,
    )
    assert len(candidates) == 1
    assert candidates[0].canonical_url == old.url
    assert candidates[0].evidence_url == fresh.url
    assert candidates[0].coverage_urls == [old.url, fresh.url]


def test_missing_freshness_state_is_fail_closed():
    article = _article("US approves arms sales to Taiwan", url="https://example.test/missing-state")
    cluster, _ = cluster_international_articles([article], CONFIG)
    results = [(article, ImportanceResult(score=90, level="critical", reasons=["arms"]))]
    assert build_notification_candidates(cluster, results, None, NOW) == []
    assert build_notification_candidates(cluster, results, {}, NOW) == []


def test_any_unclassified_member_is_fail_closed_even_with_fresh_coverage():
    old = _article("US approves arms sales to Taiwan", url="https://example.test/unclassified-old")
    fresh = _article(
        "US approves new arms sales package for Taiwan",
        source_id="ft_alphaville",
        source_name="Financial Times",
        url="https://example.test/classified-fresh",
    )
    cluster = EventCluster(
        event_id="evt_unknown_member",
        canonical=old,
        members=[old, fresh],
        coverage=[old, fresh],
    )
    results = [(fresh, ImportanceResult(score=90, level="critical", reasons=["arms"]))]
    assert build_notification_candidates(
        [cluster], results, {"fresh_articles": [fresh]}, NOW
    ) == []


def test_notification_store_suppresses_same_event_across_runs_with_new_coverage(tmp_path):
    old = _article("US approves arms sales to Taiwan", url="https://example.test/old")
    fresh = _article(
        "US approves new arms sales package for Taiwan",
        source_id="ft_alphaville",
        source_name="Financial Times",
        url="https://example.test/new",
    )
    cluster = EventCluster(
        event_id="evt_cross_run",
        canonical=old,
        members=[old, fresh],
        coverage=[old, fresh],
    )
    results = [(fresh, ImportanceResult(score=90, level="critical", reasons=["arms"]))]
    path = tmp_path / "notification-dedup.json"
    first_store = NotificationDedupStore(path)
    first = build_notification_candidates(
        [cluster], results, {"fresh_articles": [fresh], "catch_up_urls": {old.url}}, NOW, dedup_store=first_store
    )
    assert len(first) == 1
    # The delivery owner calls this only after a successful send.
    first_store.mark_sent(first[0].dedup_key, now=NOW)
    second_store = NotificationDedupStore(path)
    second = build_notification_candidates(
        [cluster], results, {"fresh_articles": [fresh], "catch_up_urls": {old.url}}, NOW, dedup_store=second_store
    )
    assert second == []
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["entries"][first[0].dedup_key] == NOW.isoformat()


def test_failed_send_does_not_mark_notification_store(tmp_path):
    article = _article("US approves arms sales to Taiwan", url="https://example.test/fail")
    cluster, _ = cluster_international_articles([article], CONFIG)
    results = [(article, ImportanceResult(score=90, level="critical", reasons=["arms"]))]
    store = NotificationDedupStore(tmp_path / "notification-dedup.json")
    first = build_notification_candidates(
        cluster, results, {"fresh_articles": [article]}, NOW, dedup_store=store
    )
    assert len(first) == 1
    # Simulated send failure: no mark_sent call.
    retry = build_notification_candidates(
        cluster, results, {"fresh_articles": [article]}, NOW, dedup_store=NotificationDedupStore(tmp_path / "notification-dedup.json")
    )
    assert len(retry) == 1


def test_notification_store_ttl_is_at_least_one_day_and_prunes(tmp_path):
    store = NotificationDedupStore(tmp_path / "notification-dedup.json")
    store.mark_sent("event:old", now=datetime(2026, 8, 13, 1, tzinfo=timezone.utc))
    assert store.is_seen("event:old", now=datetime(2026, 8, 13, 23, tzinfo=timezone.utc))
    store.prune(now=datetime(2026, 8, 14, 1, tzinfo=timezone.utc))
    assert not store.is_seen("event:old", now=datetime(2026, 8, 14, 1, tzinfo=timezone.utc))


def test_notification_store_write_failure_is_fail_closed_without_marking_success(tmp_path, monkeypatch):
    path = tmp_path / "notification-dedup.json"
    store = NotificationDedupStore(path)
    monkeypatch.setattr(store, "_atomic_write", lambda _entries: (_ for _ in ()).throw(OSError("disk full")))
    store.mark_sent("event:write-failure", now=NOW)
    assert store.is_seen("event:write-failure", now=NOW)
    assert NotificationDedupStore(path).is_seen("event:another-key", now=NOW)
    assert not path.exists()


def test_notification_store_lock_failure_is_fail_closed(tmp_path, monkeypatch):
    path = tmp_path / "notification-dedup.json"
    store = NotificationDedupStore(path)
    monkeypatch.setattr(store, "_acquire_lock", lambda: None)
    store.mark_sent("event:lock-failure", now=NOW)
    assert store.is_seen("event:lock-failure", now=NOW)
    assert not path.exists()


def test_notification_store_concurrent_instances_preserve_distinct_keys(tmp_path, monkeypatch):
    path = tmp_path / "notification-dedup.json"
    first = NotificationDedupStore(path)
    second = NotificationDedupStore(path)
    original_write = NotificationDedupStore._atomic_write
    first_write_started = threading.Event()
    release_first_write = threading.Event()

    def delayed_write(store, entries):
        if not first_write_started.is_set():
            first_write_started.set()
            assert release_first_write.wait(timeout=2)
        original_write(store, entries)

    monkeypatch.setattr(NotificationDedupStore, "_atomic_write", delayed_write)
    errors = []

    def send(store, key):
        try:
            store.mark_sent(key, now=NOW)
        except Exception as exc:  # pragma: no cover - defensive assertion aid
            errors.append(exc)

    left = threading.Thread(target=send, args=(first, "event:left"))
    right = threading.Thread(target=send, args=(second, "event:right"))
    left.start()
    assert first_write_started.wait(timeout=2)
    right.start()
    time.sleep(0.05)
    release_first_write.set()
    left.join(timeout=2)
    right.join(timeout=2)
    assert not left.is_alive() and not right.is_alive()
    assert errors == []
    loaded = json.loads(path.read_text(encoding="utf-8"))
    assert set(loaded["entries"]) == {"event:left", "event:right"}
