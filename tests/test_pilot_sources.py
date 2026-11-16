"""Program 4 official-adjacent Google News sources are well-formed and off by default."""

from urllib.parse import parse_qs, urlsplit

import yaml

IDS = {"cga_china_coast_guard", "cga_kinmen_waters", "jp_joint_staff", "jp_mod_pla_vessels"}


def _sources():
    data = yaml.safe_load(open("config/sources.yaml", encoding="utf-8"))
    return {s["id"]: s for s in (data if isinstance(data, list) else data.get("sources", []))}


def test_pilot_sources_present_enabled_and_military():
    sources = _sources()
    assert IDS <= set(sources)
    for sid in IDS:
        s = sources[sid]
        assert s["enabled"] is True  # on-topic in the 2026-11-14 server check
        assert "research_only" not in s  # PLA/coast-guard news belongs in the digest
        assert (s["category"], s["type"], s["topic"]) == ("military", "google_news_rss", "military")
        assert s["military_source_type"] in {"commercial_military", "official_military"}


def test_feed_url_carries_the_query():
    for sid in IDS:
        s = _sources()[sid]
        q = parse_qs(urlsplit(s["url"]).query)
        assert q["q"] == [s["query"] + " when:7d"] and q["hl"] == ["zh-TW"]


def test_ids_unique():
    data = yaml.safe_load(open("config/sources.yaml", encoding="utf-8"))
    rows = data if isinstance(data, list) else data.get("sources", [])
    ids = [s["id"] for s in rows]
    assert len(ids) == len(set(ids))


PROGRAM_IDS = {"prog_p1_us_china_taiwan", "prog_p1_congress_taiwan", "prog_p2_mofa", "prog_p2_transit_visits",
               "prog_p3_arms_sales", "prog_p3_allies", "prog_p5_semiconductor", "prog_p5_export_controls",
               "prog_p6_legislature", "prog_p6_party_affairs"}


def test_program_sources_research_only_and_valid():
    import app.main as m

    sources = _sources()
    assert PROGRAM_IDS <= set(sources)
    for sid in PROGRAM_IDS:
        s = sources[sid]
        assert s["enabled"] is (sid != "prog_p2_transit_visits") and s["type"] == "google_news_rss"
        assert s.get("research_only") is True
        assert parse_qs(urlsplit(s["url"]).query)["q"] == [s["query"] + " when:7d"]
    enabled = [dict(s, enabled=True) if s["id"] in PROGRAM_IDS | IDS else s for s in sources.values()]
    m.validate_sources_config(enabled, m.COLLECTOR_MAP)  # would SystemExit on errors


def test_research_only_articles_are_stored_but_not_delivered(tmp_path):
    from datetime import datetime

    from app.database import Database
    from app.main import collect_all
    from app.models import Article

    def art(url, sid):
        return Article(source_id=sid, source_name="中央社", category="politics", title="立法院三讀國防預算",
                       url=url, published_at=datetime(2026, 11, 14, 8), fetched_at=datetime(2026, 11, 14, 8),
                       position=1)

    class Stub:
        def __init__(self, cfg):
            self.cfg = cfg

        def collect(self):
            if self.cfg["id"] == "research":
                return [art("https://x/only-research", "research"), art("https://x/both", "research")]
            return [art("https://x/both", "news")]

        def close(self):
            pass

    sources = [
        {"id": "research", "name": "r", "type": "stub", "category": "politics", "url": "u", "research_only": True},
        {"id": "news", "name": "n", "type": "stub", "category": "politics", "url": "u"},
    ]
    with Database(tmp_path / "n.db") as db:
        collect_all(sources, db, content_filter_config={"enabled": False}, military_config=None,
                    collector_map={"stub": Stub})
        rows = dict(db.conn.execute("SELECT url, filter_reason FROM articles").fetchall())
    assert rows == {"https://x/only-research": "research_only", "https://x/both": None}
