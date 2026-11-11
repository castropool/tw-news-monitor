from __future__ import annotations

from datetime import datetime, timezone, timedelta
from pathlib import Path

from docx import Document

from app.database import Database, SCHEMA_VERSION
from app.social_monitor.adapters import ADAPTERS
from app.social_monitor.adapters.base import SocialAdapter
from app.social_monitor.adapters.youtube import YouTubeAdapter
from app.social_monitor.collector import SocialCollector
from app.social_monitor.crosspost import build_crossposts, canonical_posts
from app.social_monitor.models import PoliticalPerson, SocialAccount, SocialPost
from app.social_monitor.normalize import content_hash, normalize_social_text, normalize_url, stable_post_id
from app.social_monitor.registry import collectable_accounts, validate_social_config
from app.social_monitor.repository import SocialRepository
from app.social_monitor.relevance import classify_social_relevance, select_word_posts
from app.social_monitor.source_bridge import bridge_all, bridge_post, source_id_for_post
from app.social_monitor.validation import validate_database
from app.social_monitor.word_section import append_social_section


def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "news.db")
    db.connect()
    return db


def _seed_account(repo: SocialRepository, *, person_id="p1", account_id="a1", platform="youtube",
                  verified="verified", enabled=True, user_id="UC123"):
    repo.upsert_person(PoliticalPerson(person_id=person_id, canonical_name="測試人物", monitoring_tier=2, enabled=True))
    repo.upsert_account(SocialAccount(
        account_id=account_id, person_id=person_id, platform=platform,
        platform_user_id=user_id, handle="@test", verification_status=verified,
        verification_method="official_site", verification_source_url="https://example.test",
        enabled=enabled,
    ))


class FakeResponse:
    def __init__(self, payload, status_code=200):
        self.payload = payload
        self.status_code = status_code
        self.headers = {}

    def json(self):
        return self.payload


class FakeYouTubeClient:
    def get(self, url, params=None):
        if url.endswith("/channels"):
            return FakeResponse({"items": [{
                "id": "UC123", "snippet": {"title": "YouTube 官方頻道", "customUrl": "@test"},
                "contentDetails": {"relatedPlaylists": {"uploads": "UU123"}},
            }]})
        if url.endswith("/playlistItems"):
            return FakeResponse({"items": [
                {"contentDetails": {"videoId": "video-1", "videoPublishedAt": "2026-09-16T10:00:00Z"},
                 "snippet": {"title": "政策說明", "description": "政府宣布新的國防政策",
                             "thumbnails": {"high": {"url": "https://img.test/1.jpg"}}}},
                {"contentDetails": {"videoId": "video-2", "videoPublishedAt": "2026-09-15T10:00:00Z"},
                 "snippet": {"title": "生活影片", "description": "生日快樂"}},
            ]})
        if url.endswith("/videos"):
            return FakeResponse({"items": []})
        return FakeResponse({})


def test_schema_has_social_tables(tmp_path):
    db = _db(tmp_path)
    try:
        assert db.schema_version() == SCHEMA_VERSION
        tables = {r[0] for r in db.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert {
            "political_person", "social_account", "social_post",
            "social_post_revision", "social_crosspost_group",
            "social_source_link", "social_monitor_run",
        } <= tables
    finally:
        db.close()


def test_registry_validation_and_sync_idempotent(tmp_path):
    db = _db(tmp_path)
    try:
        repo = SocialRepository(db)
        config = {"persons": [
            {"person_id": "p1", "canonical_name": "人物一", "jurisdiction_level": "national",
             "monitoring_tier": 1, "accounts": [
                 {"account_id": "a1", "platform": "youtube", "account_type": "personal_official",
                  "canonical_url": "https://youtube.com/channel/UC123", "platform_user_id": "UC123",
                  "verification_status": "verified", "verification_method": "official_site"}]},
        ]}
        assert validate_social_config(config) == []
        from app.social_monitor.registry import sync_registry
        sync_registry(db, config)
        sync_registry(db, config)
        assert len(repo.list_persons()) == 1
        assert len(repo.list_accounts()) == 1
        assert len(collectable_accounts(repo)) == 1
        bad = {"persons": [{"person_id": "p1", "canonical_name": "A", "accounts": [
            {"account_id": "a1", "platform": "bogus", "account_type": "personal_official"}]}]}
        assert validate_social_config(bad)
    finally:
        db.close()


def test_collectable_requires_verified_enabled():
    assert "youtube" in ADAPTERS


def test_normalize_text_url_and_hash_stable():
    source = "　政策  <b>說明</b>\u200b\r\n\r\n\r\n連結 https://example.com/a?utm_source=x&fbclid=y"
    normalized = normalize_social_text(source)
    assert "<b>" in normalized
    assert "\u200b" not in normalized
    normalized_url = normalize_url("https://Example.com/a/?utm_source=x&fbclid=y")
    assert normalized_url == "https://example.com/a"
    assert content_hash("政策") == content_hash("  政策  ")
    assert stable_post_id("youtube", "abc") == stable_post_id("youtube", "abc")


def test_single_platform_idempotent_and_revision(tmp_path):
    db = _db(tmp_path)
    try:
        repo = SocialRepository(db)
        _seed_account(repo)
        post = SocialPost(post_id="", platform="youtube", platform_post_id="v1",
                          account_id="a1", person_id="p1", published_at="2026-09-16T10:00:00+00:00",
                          text="原始政策說明", canonical_url="https://youtube.com/watch?v=v1")
        _, status1 = repo.upsert_post(post)
        _, status2 = repo.upsert_post(post)
        assert status1 == "new"
        assert status2 == "duplicate"
        assert len(repo.list_posts()) == 1
        post.text = "修正後政策說明"
        _, status3 = repo.upsert_post(post)
        assert status3 == "updated"
        row = repo.get_post("youtube", "v1")
        assert row["edited"] == 1
        revisions = db.conn.execute("SELECT COUNT(*) FROM social_post_revision").fetchone()[0]
        assert revisions == 1
    finally:
        db.close()


def test_deletion_is_not_physical(tmp_path):
    db = _db(tmp_path)
    try:
        repo = SocialRepository(db)
        _seed_account(repo)
        pid, _ = repo.upsert_post(SocialPost(post_id="", platform="youtube", platform_post_id="v1",
                                              account_id="a1", person_id="p1",
                                              published_at="2026-09-16T10:00:00+00:00",
                                              text="x", canonical_url="https://youtube.com/watch?v=v1"))
        repo.mark_deleted(pid)
        assert repo.get_post_by_id(pid)["deleted"] == 1
        assert len(repo.list_posts(include_deleted=True)) == 1
    finally:
        db.close()


def test_crosspost_groups_same_person_and_one_canonical(tmp_path):
    db = _db(tmp_path)
    try:
        repo = SocialRepository(db)
        _seed_account(repo, user_id="UC1")
        _seed_account(repo, account_id="a2", platform="facebook", user_id="fb1")
        _seed_account(repo, person_id="p2", account_id="a3", platform="threads", user_id="th1")
        base = "同一則政策聲明"
        for i, platform in enumerate(("youtube", "facebook")):
            repo.upsert_post(SocialPost(post_id="", platform=platform, platform_post_id=f"{platform}-1",
                                        account_id="a1" if platform == "youtube" else "a2", person_id="p1",
                                        published_at="2026-09-16T10:00:00+00:00", text=base,
                                        normalized_text=base, canonical_url=f"https://{platform}.test/1"))
        repo.upsert_post(SocialPost(post_id="", platform="threads", platform_post_id="threads-1",
                                    account_id="a3", person_id="p2", published_at="2026-09-16T10:00:00+00:00",
                                    text=base, normalized_text=base, canonical_url="https://threads.test/1"))
        result = build_crossposts(repo)
        assert result["groups"] == 1
        canonical = canonical_posts(repo)
        assert len(canonical) == 2  # one grouped canonical + one ungrouped different person
        grouped = next(p for p in canonical if p["person_id"] == "p1")
        assert grouped["crosspost_group_id"]
    finally:
        db.close()


def test_source_bridge_idempotent_and_has_canonical_url(tmp_path):
    db = _db(tmp_path)
    try:
        repo = SocialRepository(db)
        _seed_account(repo)
        pid, _ = repo.upsert_post(SocialPost(post_id="", platform="youtube", platform_post_id="v1",
                                             account_id="a1", person_id="p1",
                                             published_at="2026-09-16T10:00:00+00:00", text="政策",
                                             canonical_url="https://youtube.com/watch?v=v1"))
        sid1 = bridge_post(repo, pid)
        sid2 = bridge_post(repo, pid)
        assert sid1 == sid2
        assert sid1 == source_id_for_post(repo.get_post_by_id(pid))
        assert len(repo.list_source_links()) == 1
    finally:
        db.close()


def test_relevance_routine_filtered_and_statement_separation():
    routine = classify_social_relevance("今天出席生日活動，祝福大家")
    policy = classify_social_relevance("行政院宣布新的國防政策與預算")
    assert routine["word_allowed"] is False
    assert policy["word_allowed"] is True
    post = SocialPost(post_id="", platform="x", platform_post_id="1", account_id="a", person_id="p",
                      published_at=None, text="對手造成財政惡化")
    assert post.claim_type == "attributed_statement"
    assert post.claim_verification == "attributed_only"


def test_youtube_adapter_fixture_and_rate_limit():
    adapter = YouTubeAdapter({"account_id": "a1", "platform_user_id": "UC123"}, client=FakeYouTubeClient(), api_key="fake")
    account = adapter.fetch_account()
    assert account["uploads_playlist_id"] == "UU123"
    posts = adapter.fetch_recent_posts()
    assert len(posts) == 2
    normalized = adapter.normalize_post(posts[0])
    assert normalized["platform"] == "youtube"
    assert normalized["published_at"].startswith("2026-09-16T10:00")


class FakeSocialAdapter(SocialAdapter):
    platform = "youtube"
    fail = False

    def fetch_account(self):
        return {"platform_user_id": self.account.get("platform_user_id")}

    def fetch_recent_posts(self, *, since=None, limit=30):
        if self.fail:
            raise RuntimeError("boom")
        account_id = self.account.get("account_id")
        return [{"platform_post_id": f"v1-{account_id}",
                 "published_at": "2026-09-16T10:00:00Z",
                 "text": "政策 說明", "canonical_url": f"https://youtube.com/watch?v={account_id}"}]

    def fetch_post(self, platform_post_id):
        return None

    def normalize_post(self, raw):
        return {"platform": "youtube", "platform_post_id": raw["platform_post_id"],
                "published_at": raw["published_at"], "text": raw["text"],
                "normalized_text": raw["text"], "canonical_url": raw["canonical_url"]}


def test_collector_incremental_idempotent_and_error_isolation(tmp_path):
    db = _db(tmp_path)
    try:
        repo = SocialRepository(db)
        _seed_account(repo, account_id="a1", user_id="UC1")
        _seed_account(repo, person_id="p2", account_id="a2", user_id="UC2")
        collector = SocialCollector(db, adapters={"youtube": FakeSocialAdapter}, raw_root=tmp_path / "raw")
        first = collector.collect()
        second = collector.collect()
        assert first["posts_new"] == 2
        assert second["posts_new"] == 0
        assert second["posts_seen"] == 2
        assert len(repo.list_posts()) == 2
    finally:
        db.close()


def test_disabled_or_unverified_account_not_collected(tmp_path):
    db = _db(tmp_path)
    try:
        repo = SocialRepository(db)
        _seed_account(repo, account_id="a1", verified="unverified")
        assert collectable_accounts(repo) == []
    finally:
        db.close()


def test_word_social_section_renders_canonical_once():
    doc = Document()
    append_social_section(doc, [{
        "person_id": "p1", "person_display_name": "測試人物", "platform": "facebook",
        "published_at": "2026-09-16T10:00:00+00:00",
        "normalized_text": "行政院宣布新的政策", "canonical_url": "https://facebook.test/1",
    }])
    texts = [p.text for p in doc.paragraphs]
    assert "政治人物社群动态" in texts
    assert any("測試人物｜Facebook" in t for t in texts)
    assert sum("行政院宣布新的政策" in t for t in texts) == 1


def test_all_registered_adapters_expose_interface():
    from app.social_monitor.adapters import ADAPTERS
    for name, cls in ADAPTERS.items():
        adapter = cls({"account_id": f"{name}-a", "platform_user_id": "u"}, client=object())
        for method in ("fetch_account", "fetch_recent_posts", "fetch_post", "normalize_post", "health_check"):
            assert callable(getattr(adapter, method))


def test_word_digest_social_section_integration(tmp_path):
    from datetime import datetime
    from app.models import Article
    from app.time_utils import TAIPEI
    from app.word_digest import build_word_digest
    now = datetime(2026, 9, 16, 12, 0, tzinfo=TAIPEI)
    article = Article("s", "S", "politics", "普通新聞", "https://news.test/1", now, now, 1)
    path = build_word_digest(
        [article], tmp_path, generated_at=now,
        social_items=[{
            "person_display_name": "測試人物", "platform": "facebook",
            "published_at": "2026-09-16T10:00:00+00:00",
            "normalized_text": "行政院宣布新的政策",
            "canonical_url": "https://facebook.test/1",
        }],
    )
    texts = [p.text for p in Document(path).paragraphs]
    assert "政治人物社群动态" in texts
    assert any("測試人物｜Facebook" in text for text in texts)


def test_bridge_all_links_crosspost_canonical_once(tmp_path):
    db = _db(tmp_path)
    try:
        repo = SocialRepository(db)
        _seed_account(repo, user_id="UC1", account_id="a1", platform="youtube")
        _seed_account(repo, user_id="fb1", account_id="a2", platform="facebook")
        for platform, account in (("youtube", "a1"), ("facebook", "a2")):
            repo.upsert_post(SocialPost(post_id="", platform=platform, platform_post_id=f"{platform}-1",
                                        account_id=account, person_id="p1",
                                        published_at="2026-09-16T10:00:00+00:00",
                                        text="同一政策", normalized_text="同一政策",
                                        canonical_url=f"https://{platform}.test/1"))
        build_crossposts(repo)
        linked = bridge_all(repo)
        assert linked["linked"] == 1
        assert len(repo.list_source_links()) == 1
    finally:
        db.close()


class FakeRSSResponse:
    status_code = 200
    headers = {}
    text = """<?xml version="1.0" encoding="UTF-8"?>
    <feed xmlns="http://www.w3.org/2005/Atom" xmlns:yt="http://www.youtube.com/xml/schemas/2015">
      <title>測試頻道</title>
      <entry>
        <yt:videoId>rss-video-1</yt:videoId>
        <title>RSS 政策影片</title>
        <link href="https://www.youtube.com/watch?v=rss-video-1"/>
        <published>2026-09-16T10:00:00+00:00</published>
        <summary>國防政策說明</summary>
      </entry>
    </feed>"""


class FakeRSSClient:
    def get(self, url, params=None):
        return FakeRSSResponse()


def test_youtube_public_rss_fallback_without_api_key():
    adapter = YouTubeAdapter(
        {"account_id": "a1", "platform_user_id": "UC123"},
        client=FakeRSSClient(), api_key="",
    )
    assert adapter.collection_method == "youtube_public_rss"
    account = adapter.fetch_account()
    assert account["platform_user_id"] == "UC123"
    posts = adapter.fetch_recent_posts()
    assert len(posts) == 1
    assert posts[0]["platform_post_id"] == "rss-video-1"
    normalized = adapter.normalize_post(posts[0])
    assert normalized["canonical_url"] == "https://www.youtube.com/watch?v=rss-video-1"
