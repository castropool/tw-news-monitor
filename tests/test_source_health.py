from datetime import datetime, timedelta, timezone

from app.source_health import HealthRecord, SourceHealthStore, SourceOutcome, ValidEmptyFeed


def test_valid_empty_feed_is_success_then_stale_after_three_runs(tmp_path):
    store = SourceHealthStore(tmp_path / "health.json")
    for _ in range(3):
        store.update("reuters_international", ValidEmptyFeed())
    record = store.get("reuters_international")
    assert record.status == "stale"
    assert record.last_success is not None
    assert record.items_fetched == 0
    assert record.consecutive_failures == 0


def test_nonempty_success_is_healthy_and_failure_counter_resets(tmp_path):
    store = SourceHealthStore(tmp_path / "health.json")
    store.update("ft_alphaville", SourceOutcome(500, False, 0))
    store.update("ft_alphaville", SourceOutcome(200, True, 2))
    record = store.get("ft_alphaville")
    assert record.status == "healthy"
    assert record.items_fetched == 2
    assert record.consecutive_failures == 0
    assert record.parse_errors == 0


def test_three_schema_failures_become_broken_and_keep_last_success(tmp_path):
    store = SourceHealthStore(tmp_path / "health.json")
    first = store.update("reuters_international", SourceOutcome(200, True, 1))
    for _ in range(3):
        current = store.update("reuters_international", SourceOutcome(200, False, 0))
    assert current.status == "broken"
    assert current.last_success == first.last_success
    assert current.parse_errors == 3
    assert current.consecutive_failures == 3


def test_stale_when_last_item_is_older_than_48_hours(tmp_path):
    store = SourceHealthStore(tmp_path / "health.json")
    old = datetime.now(timezone.utc) - timedelta(hours=49)
    store.update("reuters_international", SourceOutcome(200, True, 1), now=old)
    current = store.update("reuters_international", SourceOutcome(200, True, 0))
    assert current.status == "stale"


def test_stale_when_source_has_been_valid_but_empty_for_48_hours(tmp_path):
    store = SourceHealthStore(tmp_path / "health.json")
    first_check = datetime(2026, 8, 13, 0, 0, tzinfo=timezone.utc)
    store.update("ft_alphaville", ValidEmptyFeed(), now=first_check)

    current = store.update(
        "ft_alphaville",
        ValidEmptyFeed(),
        now=first_check + timedelta(hours=48),
    )

    assert current.last_item_at is None
    assert current.status == "stale"


def test_sidecar_write_is_atomic_and_disabled_is_explicit(tmp_path):
    path = tmp_path / "nested" / "health.json"
    store = SourceHealthStore(path)
    record = store.disable("wsj_newsletter")
    assert record.status == "disabled"
    assert path.exists()
    assert not list(path.parent.glob("*.tmp"))
    assert HealthRecord.from_dict(store.get("wsj_newsletter").to_dict()).status == "disabled"


def test_health_error_codes_separate_http_timeout_auth_and_parse(tmp_path):
    store = SourceHealthStore(tmp_path / "health.json")
    store.update("reuters_international", SourceOutcome(503, False, 0, "http"))
    http_record = store.get("reuters_international")
    assert http_record.last_error_code == "http"
    assert http_record.parse_errors == 0

    store.update("reuters_international", SourceOutcome(0, False, 0, "timeout"))
    timeout_record = store.get("reuters_international")
    assert timeout_record.last_error_code == "timeout"
    assert timeout_record.parse_errors == 0

    store.update("reuters_international", SourceOutcome(0, False, 0, "auth"))
    auth_record = store.get("reuters_international")
    assert auth_record.last_error_code == "auth"
    assert auth_record.parse_errors == 0

    store.update("reuters_international", SourceOutcome(200, False, 0, "parse"))
    parse_record = store.get("reuters_international")
    assert parse_record.last_error_code == "parse"
    assert parse_record.parse_errors == 1
    assert parse_record.updated_at is not None


def test_old_sidecar_migrates_updated_at_from_last_success(tmp_path):
    path = tmp_path / "health.json"
    path.write_text(
        '{"reuters_international": {'
        '"source_id": "reuters_international", "status": "healthy", '
        '"last_success": "2026-08-14T00:00:00+00:00", '
        '"last_item_at": null, "items_fetched": 0, "parse_errors": 2, '
        '"consecutive_failures": 0, "zero_item_runs": 1}}',
        encoding="utf-8",
    )
    record = SourceHealthStore(path).get("reuters_international")
    assert record.updated_at == datetime(2026, 8, 14, tzinfo=timezone.utc)
    assert record.last_error_code is None
    assert record.parse_errors == 2
