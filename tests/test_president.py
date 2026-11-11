import json, os
from datetime import datetime
from zoneinfo import ZoneInfo
TAIPEI = ZoneInfo("Asia/Taipei")

from app.collectors.president import PresidentCollector, parse_president_time
from app.source_registry import is_official_source, get_source_info, get_official_sources

FIXTURE_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "president_press_api_20260718.json")

class TestPresidentTime:
    def test_afternoon(self):
        dt = parse_president_time("2026/7/16 下午 03:36:00")
        assert dt is not None
        assert dt.hour == 15 and dt.minute == 36
        assert dt.tzinfo is TAIPEI

    def test_morning(self):
        dt = parse_president_time("2026/7/16 上午 09:05:00")
        assert dt is not None
        assert dt.hour == 9 and dt.minute == 5

    def test_noon_am12(self):
        dt = parse_president_time("2026/7/16 上午 12:00:00")
        assert dt is not None and dt.hour == 0

    def test_noon_pm12(self):
        dt = parse_president_time("2026/7/16 下午 12:30:00")
        assert dt is not None and dt.hour == 12 and dt.minute == 30

    def test_edge_pm11(self):
        dt = parse_president_time("2026/7/16 下午 11:59:59")
        assert dt is not None and dt.hour == 23 and dt.second == 59

    def test_invalid_returns_none(self):
        assert parse_president_time("") is None
        assert parse_president_time("abc") is None
        assert parse_president_time(None) is None

    def test_aware_datetime(self):
        dt = parse_president_time("2026/7/16 下午 03:36:00")
        assert dt.tzinfo is not None
        assert dt.tzinfo.key == "Asia/Taipei"

class TestPresidentCollector:
    def test_fixture_parse(self):
        with open(FIXTURE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        assert len(data) >= 3, f"Expected >=3 entries, got {len(data)}"
        for entry in data:
            assert "Title" in entry
            assert "URL" in entry
            assert "PublishDate" in entry

    def test_parse_all_times(self):
        with open(FIXTURE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        for entry in data:
            dt = parse_president_time(entry.get("PublishDate"))
            assert dt is not None, f"Failed to parse: {entry.get('PublishDate')}"
            assert dt.tzinfo is not None

    def test_source_registry_president(self):
        assert is_official_source("president_press") is True
        info = get_source_info("president_press")
        assert info["display_name"] == "台湾总统府"
        assert info["document_type"] == "新闻稿"

    def test_media_source_not_official(self):
        assert is_official_source("cna_politics") is False
        assert is_official_source("udn_politics") is False
        assert is_official_source("ebc_politics") is False

    def test_get_official_sources_order(self):
        sources = get_official_sources()
        assert len(sources) >= 1
        assert sources[0][0] == 10  # president_press first
        assert sources[0][1] == "president_press"
