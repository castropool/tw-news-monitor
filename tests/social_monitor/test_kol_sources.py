"""KOL social sources: registry, verification, feed collection and delivery."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from docx import Document

from app.database import Database
from app.digest import build_digest
from app.social_monitor import cli
from app.social_monitor.adapters.feed import FeedAdapter
from app.social_monitor.collector import SocialCollector
from app.social_monitor.models import PoliticalPerson, SocialAccount
from app.social_monitor.news_run import (
    collect_due_for_news_run,
    kol_delivery_items,
    load_monitor_config,
    social_items,
)
from app.social_monitor.registry import (
    apply_verification_store,
    collectable_accounts,
    load_all_social_configs,
    load_registry_with_verifications,
    sync_registry,
    validate_social_config,
)
from app.social_monitor.repository import SocialRepository
from app.social_monitor.verify import resolve_youtube_channel_id, verify_account
from app.social_monitor.word_section import append_social_section

ROOT = Path(__file__).resolve().parents[2]
CHANNEL = "UCabcdefghijklmnopqrstuv"  # 24 chars: UC + 22


class Resp:
    def __init__(self, text="", status=200):
        self.text = text
        self.status_code = status
        self.headers = {}


class Client:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    def get(self, url, params=None):
        self.calls.append(url)
        for prefix, response in self.routes.items():
            if url.startswith(prefix):
                return response
        return Resp("", 404)


def _feed(title, entries=(), author=""):
    items = "".join(
        f"<entry><id>yt:video:{vid}</id><title>{t}</title>"
        f"<link rel='alternate' href='https://www.youtube.com/watch?v={vid}'/>"
        f"<published>{pub}</published><summary>{s}</summary></entry>"
        for vid, t, pub, s in entries
    )
    author_xml = f"<author><name>{author}</name></author>" if author else ""
    return f"<?xml version='1.0'?><feed xmlns='http://www.w3.org/2005/Atom'><title>{title}</title>{author_xml}{items}</feed>"


def _db(tmp_path) -> Database:
    db = Database(tmp_path / "news.db")
    db.connect()
    db.create_tables()
    return db


# ---------------------------------------------------------------- registry ---

def test_kol_registry_has_20_valid_kols_none_collectable_by_default(tmp_path):
    config = load_all_social_configs()
    assert validate_social_config(config) == []
    kols = [p for p in config["persons"] if p["person_category"] == "kol"]
    assert len(kols) == 20
    assert len({p["person_id"] for p in kols}) == 20
    assert all(p["person_id"].startswith("kol_") and p["accounts"] for p in kols)
    assert all(a["verification_status"] != "verified" for p in kols for a in p["accounts"])
    political = [p for p in config["persons"] if p["person_category"] == "political"]
    assert political, "political registry must still be loaded"
    db = _db(tmp_path)
    try:
        sync_registry(db, config)
        repo = SocialRepository(db)
        assert collectable_accounts(repo, person_category="kol") == []
        assert repo.get_person("kol_shasha77")["person_category"] == "kol"
    finally:
        db.close()


def test_invalid_category_and_feed_url_rejected():
    errors = validate_social_config({"persons": [{
        "person_id": "x", "canonical_name": "X", "person_category": "celebrity",
        "accounts": [{"account_id": "x_a", "platform": "blog", "account_type": "creator_official",
                      "feed_url": "ftp://bad"}],
    }]})
    assert any("person_category" in e for e in errors)
    assert any("feed_url" in e for e in errors)


def test_verification_store_overlay_rules():
    config = {"persons": [{
        "person_id": "kol_a", "canonical_name": "甲", "person_category": "kol",
        "accounts": [
            {"account_id": "kol_a_youtube", "platform": "youtube", "account_type": "creator_official",
             "verification_status": "pending"},
            {"account_id": "kol_a_x", "platform": "x", "account_type": "creator_official",
             "verification_status": "rejected"},
            {"account_id": "kol_a_fb", "platform": "facebook", "account_type": "creator_official"},
        ],
    }]}
    store = {"accounts": {
        "kol_a_youtube": {"status": "verified", "platform": "youtube", "platform_user_id": CHANNEL,
                          "canonical_url": f"https://www.youtube.com/channel/{CHANNEL}",
                          "checked_at": "2026-11-13T00:00:00+00:00", "method": "m", "evidence_url": "https://e"},
        "kol_a_x": {"status": "verified", "platform": "x"},
        "kol_a_fb": {"status": "verified", "platform": "youtube"},  # platform mismatch
    }}
    merged = apply_verification_store(config, store)
    accounts = {a["account_id"]: a for a in merged["persons"][0]["accounts"]}
    assert accounts["kol_a_youtube"]["verification_status"] == "verified"
    assert accounts["kol_a_youtube"]["platform_user_id"] == CHANNEL
    assert accounts["kol_a_x"]["verification_status"] == "rejected"
    assert accounts["kol_a_fb"].get("verification_status") is None
    # the input config is not mutated
    assert config["persons"][0]["accounts"][0]["verification_status"] == "pending"


# ------------------------------------------------------------- verification ---

PERSON = {"person_id": "kol_a", "canonical_name": "志祺七七", "aliases": ["張志祺"]}


def test_verify_youtube_handle_resolves_and_matches():
    client = Client({
        "https://www.youtube.com/@shasha77": Resp(f'<html><meta itemprop="identifier" content="{CHANNEL}"></html>'),
        "https://www.youtube.com/feeds/videos.xml": Resp(_feed("志祺七七 X 圖文不符")),
    })
    account = {"account_id": "kol_a_youtube", "platform": "youtube",
               "canonical_url": "https://www.youtube.com/@shasha77"}
    record = verify_account(PERSON, account, client=client)
    assert record["status"] == "verified"
    assert record["platform_user_id"] == CHANNEL
    assert record["handle"] == "@shasha77"
    assert record["canonical_url"] == f"https://www.youtube.com/channel/{CHANNEL}"


def test_verify_youtube_name_mismatch_is_not_verified():
    client = Client({
        "https://www.youtube.com/channel/": Resp(""),
        "https://www.youtube.com/feeds/videos.xml": Resp(_feed("Some Other Channel")),
    })
    account = {"account_id": "kol_a_youtube", "platform": "youtube"}
    record = verify_account(PERSON, account, url=f"https://www.youtube.com/channel/{CHANNEL}", client=client)
    assert record["status"] == "failed" and record["reason"] == "name_mismatch"


def test_verify_without_identifier_or_method():
    assert verify_account(PERSON, {"account_id": "a", "platform": "youtube"}, client=Client({}))["reason"] == "no_candidate_identifier"
    fb = verify_account(PERSON, {"account_id": "b", "platform": "facebook"}, client=Client({}))
    assert fb["status"] == "failed" and fb["reason"] == "no_collectable_method_for_facebook"


def test_verify_feed_account_by_author_and_fetch_failure():
    client = Client({"https://bridge.test/fb.xml": Resp(_feed("Facebook feed", author="張志祺"))})
    record = verify_account(PERSON, {"account_id": "c", "platform": "facebook"},
                            url="https://bridge.test/fb.xml", client=client)
    assert record["status"] == "verified" and record["feed_url"] == "https://bridge.test/fb.xml"
    broken = verify_account(PERSON, {"account_id": "d", "platform": "blog", "feed_url": "https://gone.test/x.xml"},
                            client=Client({}))
    assert broken["status"] == "failed" and broken["reason"].startswith("fetch_failed")


def test_resolve_channel_direct_forms():
    assert resolve_youtube_channel_id(f"https://www.youtube.com/channel/{CHANNEL}", Client({}))[0] == CHANNEL
    assert resolve_youtube_channel_id(CHANNEL, Client({}))[0] == CHANNEL
    page = Client({"https://www.youtube.com/@abc": Resp("no id here")})
    assert resolve_youtube_channel_id("abc", page)[0] is None


def test_cli_verify_writes_store_and_list(tmp_path, monkeypatch, capsys):
    store = tmp_path / "verifications.json"
    client = Client({
        "https://www.youtube.com/@shasha77": Resp(f'"channelId":"{CHANNEL}"'),
        "https://www.youtube.com/feeds/videos.xml": Resp(_feed("志祺七七")),
    })
    parser = cli.build_parser()
    args = parser.parse_args(["--store", str(store), "verify", "--account-id", "kol_shasha77_youtube"])
    assert cli._cmd_verify(args, client=client) == 0
    saved = json.loads(store.read_text(encoding="utf-8"))
    assert saved["accounts"]["kol_shasha77_youtube"]["status"] == "verified"
    merged = load_registry_with_verifications(store_path=store)
    account = next(a for p in merged["persons"] for a in p["accounts"] if a["account_id"] == "kol_shasha77_youtube")
    assert account["verification_status"] == "verified" and account["platform_user_id"] == CHANNEL
    capsys.readouterr()
    args = parser.parse_args(["--store", str(store), "list", "--category", "kol"])
    assert cli._cmd_list(args) == 0
    rows = json.loads(capsys.readouterr().out)
    assert len({r["person_id"] for r in rows}) == 20
    assert {r["verification_status"] for r in rows if r["account_id"] == "kol_shasha77_youtube"} == {"verified"}


def test_cli_verify_dry_run_does_not_write(tmp_path):
    store = tmp_path / "verifications.json"
    args = cli.build_parser().parse_args(["--store", str(store), "verify", "--account-id", "kol_mindi_youtube", "--dry-run"])
    assert cli._cmd_verify(args, client=Client({})) == 1
    assert not store.exists()


# --------------------------------------------------------------- collection ---

def _seed_kol(repo, *, person_id="kol_a", account_id="kol_a_blog", platform="blog",
              feed_url="https://feed.test/a.xml", verified="verified", category="kol",
              last_checked_at=None):
    repo.upsert_person(PoliticalPerson(person_id=person_id, canonical_name="評論員甲", person_category=category))
    repo.upsert_account(SocialAccount(
        account_id=account_id, person_id=person_id, platform=platform, feed_url=feed_url,
        account_type="creator_official", verification_status=verified, monitoring_priority=3,
        last_checked_at=last_checked_at,
    ))


RECENT = (datetime.now(timezone.utc) - timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
OLD = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%dT%H:%M:%SZ")
FEED_XML = _feed("評論員甲", entries=[
    ("v1", "兩岸情勢與國防預算解析", RECENT, "&lt;p&gt;談國防與兩岸&lt;/p&gt;"),
    ("v2", "我的生日旅遊", RECENT, "家人生活"),
    ("v3", "立法院法案回顧", OLD, "質詢重點"),
])


def test_feed_adapter_normalizes_entries():
    adapter = FeedAdapter({"account_id": "a", "platform": "facebook", "feed_url": "https://feed.test/a.xml"},
                          client=Client({"https://feed.test/a.xml": Resp(FEED_XML)}))
    posts = adapter.fetch_recent_posts()
    assert len(posts) == 3
    normalized = adapter.normalize_post(posts[0])
    assert normalized["platform"] == "facebook"
    assert normalized["title"] == "兩岸情勢與國防預算解析"
    assert normalized["text"] == "談國防與兩岸"
    assert normalized["published_at"].endswith("+00:00")
    assert posts[0]["platform_post_id"] == adapter.fetch_recent_posts()[0]["platform_post_id"]


def test_news_run_collects_only_due_verified_kol_accounts(tmp_path):
    db = _db(tmp_path)
    try:
        repo = SocialRepository(db)
        _seed_kol(repo)
        _seed_kol(repo, person_id="kol_b", account_id="kol_b_blog", feed_url="https://feed.test/b.xml",
                  last_checked_at=datetime.now(timezone.utc).isoformat())  # not due yet
        _seed_kol(repo, person_id="kol_c", account_id="kol_c_blog", feed_url="https://feed.test/c.xml",
                  verified="pending")
        _seed_kol(repo, person_id="pol_d", account_id="pol_d_blog", feed_url="https://feed.test/d.xml",
                  category="political")
        client = Client({"https://feed.test/": Resp(FEED_XML)})

        class _Feed(FeedAdapter):
            def __init__(self, account, *, client_=client, **_):
                super().__init__(account, client=client_)

        collector = SocialCollector(db, raw_root=tmp_path / "raw", adapters={"feed": _Feed})
        result = collect_due_for_news_run(
            db, config=load_monitor_config(), registry={"persons": []}, collector=collector,
        )
        assert result["status"] == "ok"
        assert result["accounts_attempted"] == 1 and result["posts_new"] == 3
        assert client.calls == ["https://feed.test/a.xml"]
        assert {p["account_id"] for p in repo.list_posts()} == {"kol_a_blog"}
        # second run: account is no longer due, nothing collected
        again = collect_due_for_news_run(db, config=load_monitor_config(), registry={"persons": []}, collector=collector)
        assert again["accounts_attempted"] == 0

        items = social_items(db)
        assert {i["person_category"] for i in items} == {"kol"}
        delivered = kol_delivery_items(items, config=load_monitor_config())
        assert [i["title"] for i in delivered] == ["兩岸情勢與國防預算解析"]  # personal + old excluded
    finally:
        db.close()


def test_news_run_disabled_and_failure_safe(tmp_path):
    cfg = load_monitor_config()
    cfg["news_run"] = dict(cfg["news_run"], enabled=False)
    assert collect_due_for_news_run(object(), config=cfg)["status"] == "disabled"
    assert collect_due_for_news_run(object(), config=load_monitor_config(), registry={"persons": []})["status"] == "error"


def test_monitor_config_defaults_when_missing(tmp_path):
    cfg = load_monitor_config(tmp_path / "missing.yaml")
    assert cfg["news_run"]["categories"] == ["kol"]
    assert cfg["kol_delivery"]["window_hours"] == 24


# ----------------------------------------------------------------- delivery ---

def _item(category, name, title, text, hours_ago=1, platform="youtube"):
    return {
        "post_id": f"{name}-{title}", "person_category": category, "person_display_name": name,
        "platform": platform, "title": title, "text": text, "normalized_text": text,
        "published_at": (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat(),
        "canonical_url": f"https://www.youtube.com/watch?v={abs(hash(title)) % 10000}",
    }


def test_word_renders_separate_political_and_kol_sections(tmp_path):
    doc = Document()
    append_social_section(doc, [
        _item("political", "賴清德", "", "國防政策說明"),
        _item("kol", "八炯", "統戰影片解析", "兩岸統戰手法"),
    ])
    texts = [p.text for p in doc.paragraphs]
    political_at = texts.index("政治人物社群动态")
    kol_at = texts.index("KOL 社群动态")
    assert political_at < kol_at
    political_part, kol_part = texts[political_at:kol_at], texts[kol_at:]
    assert any(t.startswith("【賴清德｜YouTube｜") for t in political_part)
    assert not any("八炯" in t for t in political_part)
    assert any(t.startswith("【八炯｜YouTube｜") for t in kol_part)
    assert "标题：統戰影片解析" in kol_part
    assert not any(t.startswith("标题：") for t in political_part)


def test_feishu_text_digest_kol_block():
    items = [_item("kol", "敏迪", "國際局勢一次看懂：美中關係\n第二行", "")]
    text = build_digest([], datetime(2026, 11, 13, 9, 0), kol_items=items)
    assert "本轮未发现新增新闻。" in text
    assert "KOL动态｜1条" in text
    assert "1. 【敏迪｜YouTube】國際局勢一次看懂：美中關係" in text
    assert "第二行" not in text
    assert "KOL动态" not in build_digest([], datetime(2026, 11, 13, 9, 0))


def test_kol_delivery_respects_switch_window_and_limit():
    cfg = load_monitor_config()
    items = [_item("kol", f"K{i}", f"兩岸議題{i}", "兩岸", hours_ago=i) for i in range(1, 15)]
    items.append(_item("political", "P", "兩岸政策", "兩岸"))
    feishu = kol_delivery_items(items, config=cfg, for_feishu=True)
    assert len(feishu) == cfg["kol_delivery"]["feishu_max_items"]
    assert feishu[0]["person_display_name"] == "K1"  # newest first
    assert all(i["person_category"] == "kol" for i in feishu)
    cfg["kol_delivery"] = dict(cfg["kol_delivery"], word_enabled=False)
    assert kol_delivery_items(items, config=cfg) == []
