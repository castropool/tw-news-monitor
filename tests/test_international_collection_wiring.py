from pathlib import Path
from datetime import datetime, timezone
import pytest

from app.database import Database
from app.main import COLLECTOR_MAP, collect_all, load_sources, validate_sources_config
from app.newsletter_ingestion.gmail_client import GmailMailboxClient
from app.newsletter_ingestion.models import NewsletterMessage
from app.newsletter_ingestion.oauth import AuthContext, MAILBOX_AUTH_REQUIRED
from app.source_health import SourceHealthStore


ROOT = Path(__file__).resolve().parents[1]


def test_newsletter_collectors_are_registered_and_source_isolation_keeps_other_sources(tmp_path):
    assert "wsj_newsletter" in COLLECTOR_MAP
    assert "bloomberg_newsletter" in COLLECTOR_MAP

    class BrokenCollector:
        def __init__(self, source):
            raise RuntimeError("constructor failure")

    from app.models import Article
    from datetime import datetime, timezone

    class GoodCollector:
        def __init__(self, source):
            self.source = source
        def collect(self):
            return [Article("good", "Good", "international", "ok", "https://example.com/a", None, datetime.now(timezone.utc), 1)]
        def close(self):
            pass

    sources = [
        {"id": "broken", "name": "Broken", "type": "broken", "category": "international", "url": "https://example.com/b", "enabled": True},
        {"id": "good", "name": "Good", "type": "good", "category": "international", "url": "https://example.com/a", "enabled": True},
    ]
    collector_map = {"broken": BrokenCollector, "good": GoodCollector}
    validate_sources_config(sources, collector_map)
    db = Database(tmp_path / "news.db")
    db.connect(); db.create_tables()
    result = collect_all(sources, db, collector_map=collector_map)
    assert result[0]
    assert result[3] == ["broken"]
    db.close()


def test_production_sources_are_all_disabled():
    sources = load_sources(ROOT / "config" / "sources.yaml")
    by_id = {s["id"]: s for s in sources}
    for source_id in ("reuters_international", "ft_alphaville", "wsj_newsletter", "bloomberg_newsletter", "wsj_international"):
        assert by_id[source_id]["enabled"] is False


def _newsletter_source(source_id="wsj_newsletter"):
    return {
        "id": source_id,
        "name": "Wall Street Journal" if source_id == "wsj_newsletter" else "Bloomberg",
        "type": source_id,
        "category": "international",
        "language": "en",
        "access_level": "newsletter",
        "url": "https://example.invalid/newsletters",
        "mailbox_label": "InternationalNews",
        "sender_allowlist": ["wsj.com" if source_id == "wsj_newsletter" else "bloomberg.com"],
        "article_allowlist": ["wsj.com" if source_id == "wsj_newsletter" else "bloomberg.com"],
        "enabled": True,
    }


@pytest.mark.parametrize("source_id", ["wsj_newsletter", "bloomberg_newsletter"])
def test_enabled_newsletter_without_auth_is_failed_and_degraded(tmp_path, monkeypatch, source_id):
    import app.main as main_module

    unauth = AuthContext(None, None, False, MAILBOX_AUTH_REQUIRED)
    monkeypatch.setattr(
        main_module,
        "_build_newsletter_mailbox",
        lambda _source: GmailMailboxClient(None, auth=unauth),
    )
    db = Database(tmp_path / "news.db")
    db.connect()
    db.create_tables()
    health = SourceHealthStore(tmp_path / "health.json")
    inserted, _total, _dup, failed, *_ = collect_all(
        [_newsletter_source(source_id)], db, health_store=health
    )
    assert inserted == []
    assert failed == [source_id]
    record = health.get(source_id)
    assert record.status == "degraded"
    assert record.last_error_code == "auth"
    assert record.parse_errors == 0
    db.close()


class _AuthorizedMailbox:
    def __init__(self, source_id):
        self.source_id = source_id

    def list_messages(self, label, sender_allowlist, since):
        domain = "wsj.com" if self.source_id == "wsj_newsletter" else "bloomberg.com"
        return [
            NewsletterMessage(
                "m1",
                f"brief@{domain}",
                datetime(2026, 8, 15, tzinfo=timezone.utc),
                "What's News",
                f'<h2><a href="https://www.{domain}/articles/a?utm_source=x">Taiwan update</a></h2>',
                None,
                label,
            )
        ]


@pytest.mark.parametrize("source_id", ["wsj_newsletter", "bloomberg_newsletter"])
def test_enabled_newsletter_injects_authorized_mailbox_and_collects(tmp_path, monkeypatch, source_id):
    import app.main as main_module

    monkeypatch.setattr(main_module, "_build_newsletter_mailbox", lambda _source: _AuthorizedMailbox(source_id))
    db = Database(tmp_path / "news.db")
    db.connect()
    db.create_tables()
    inserted, total, _dup, failed, *_ = collect_all(
        [_newsletter_source(source_id)], db
    )
    assert len(inserted) == 1
    assert total == 1
    assert failed == []
    assert inserted[0].access_level == "newsletter"
    db.close()
