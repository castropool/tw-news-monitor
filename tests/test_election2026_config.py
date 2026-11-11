"""配置加载与结构校验（fail-closed）。"""

import pytest

from app.election2026.config import (
    load_election_config,
    load_entities,
    DISABLED_CONFIG,
    DISABLED_ENTITIES,
)


class TestLoadElectionConfig:
    def test_real_config_enabled(self):
        cfg = load_election_config()
        assert cfg["enabled"] is True
        assert cfg["thresholds"]["direct"] == 60
        assert cfg["thresholds"]["review"] == 40
        # 词表齐全
        assert len(cfg["strong_terms"]) > 10
        assert len(cfg["auxiliary_terms"]) > 10
        assert len(cfg["negative_terms"]) >= 10
        assert len(cfg["national_scenes"]) > 5
        # 22 县市 aliases
        assert len(cfg["regions"]["aliases"]) >= 22
        # 合并组
        assert "新竹縣市" in cfg["regions"]["merge_groups"]
        assert cfg["regions"]["merge_groups"]["新竹縣市"] == ["新竹縣", "新竹市"]
        # 展示顺序含全局动向
        assert cfg["regions"]["display_order"][0] == "全局動向"

    def test_event_type_map_complete(self):
        cfg = load_election_config()
        expected = {
            "nomination", "polling", "campaign", "party_coordination",
            "alliance", "faction", "attack_defense", "controversy",
            "legal", "policy", "personnel", "other",
        }
        assert set(cfg["event_type_map"].keys()) == expected

    def test_missing_file_fails_closed(self, tmp_path):
        cfg = load_election_config(tmp_path / "nope.yaml")
        assert cfg["enabled"] is False
        # 所有引用键存在（不会 KeyError）
        assert cfg["strong_terms"] == []
        assert cfg["regions"]["aliases"] == {}

    def test_invalid_yaml_fails_closed(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("not: [valid\n  yaml::", encoding="utf-8")
        cfg = load_election_config(bad)
        assert cfg["enabled"] is False

    def test_disabled_shape_stable(self):
        # 直接引用 DISABLED_CONFIG 不改坏
        cfg = DISABLED_CONFIG
        assert cfg["enabled"] is False


class TestLoadEntities:
    def test_real_entities(self):
        ent = load_entities()
        candidates = ent["candidates"]
        assert "陳亭妃" in candidates
        assert "謝龍介" in candidates
        assert "蘇巧慧" in candidates
        assert candidates["陳亭妃"]["regions"] == ["台南市"]
        assert candidates["蘇巧慧"]["regions"] == ["新北市"]

    def test_missing_file_empty(self, tmp_path):
        ent = load_entities(tmp_path / "nope.yaml")
        assert ent["candidates"] == {}

    def test_invalid_yaml_empty(self, tmp_path):
        bad = tmp_path / "bad.yaml"
        bad.write_text("{{{{", encoding="utf-8")
        ent = load_entities(bad)
        assert ent["candidates"] == {}
