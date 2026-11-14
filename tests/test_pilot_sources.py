"""Program 4 official-adjacent Google News sources are well-formed and off by default."""

from urllib.parse import parse_qs, urlsplit

import yaml

IDS = {"cga_china_coast_guard", "cga_kinmen_waters", "jp_joint_staff", "jp_mod_pla_vessels"}


def _sources():
    data = yaml.safe_load(open("config/sources.yaml", encoding="utf-8"))
    return {s["id"]: s for s in (data if isinstance(data, list) else data.get("sources", []))}


def test_pilot_sources_present_disabled_and_military():
    sources = _sources()
    assert IDS <= set(sources)
    for sid in IDS:
        s = sources[sid]
        assert s["enabled"] is False
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
