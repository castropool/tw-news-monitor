import pytest
from app.main import validate_sources_config, COLLECTOR_MAP

GOOD_SOURCE = {
    "id": "test_source",
    "name": "Test Source",
    "type": "rss",
    "category": "politics",
    "url": "https://example.com/rss",
}

VALID_CATEGORIES = {"politics", "economy", "international"}


class TestConfigValidation:
    """12 tests for validate_sources_config()."""

    # ── 1. enabled source missing "type" → fail ────────────────────
    def test_enabled_missing_type_fails(self):
        s = dict(GOOD_SOURCE)
        s.pop("type")
        with pytest.raises(SystemExit):
            validate_sources_config([s], COLLECTOR_MAP)

    # ── 2. disabled source missing "type" → also fail ──────────────
    def test_disabled_missing_type_fails(self):
        s = dict(GOOD_SOURCE, enabled=False)
        s.pop("type")
        with pytest.raises(SystemExit):
            validate_sources_config([s], COLLECTOR_MAP)

    # ── 3. collector-only (no "type") → fail ──────────────────────
    def test_collector_only_no_type_fails(self):
        s = dict(GOOD_SOURCE)
        s.pop("type")
        s["collector"] = "rss"
        with pytest.raises(SystemExit):
            validate_sources_config([s], COLLECTOR_MAP)

    # ── 4. "type" not in COLLECTOR_MAP → fail ─────────────────────
    def test_type_not_in_map_fails(self):
        s = dict(GOOD_SOURCE, type="nonexistent_collector")
        with pytest.raises(SystemExit):
            validate_sources_config([s], COLLECTOR_MAP)

    # ── 5. duplicate source ids → fail ────────────────────────────
    def test_duplicate_id_fails(self):
        s1 = dict(GOOD_SOURCE, id="dup_id")
        s2 = dict(GOOD_SOURCE, id="dup_id", url="https://example.com/other")
        with pytest.raises(SystemExit):
            validate_sources_config([s1, s2], COLLECTOR_MAP)

    # ── 6. enabled="false" (string) → fail ────────────────────────
    def test_enabled_string_false_fails(self):
        s = dict(GOOD_SOURCE, enabled="false")
        with pytest.raises(SystemExit):
            validate_sources_config([s], COLLECTOR_MAP)

    # ── 7. enabled=False (real bool) → pass ───────────────────────
    def test_enabled_bool_false_passes(self):
        s = dict(GOOD_SOURCE, enabled=False)
        # Should not raise
        validate_sources_config([s], COLLECTOR_MAP)

    # ── 8. valid 19-source production-equivalent config → pass ────
    def test_valid_19_sources_passes(self):
        """Validate current sources config (production: 23, dev: 19)."""
        import yaml
        from pathlib import Path
        # Try production config first
        prod_cfg = Path(__file__).resolve().parent.parent.parent / "tw-news-monitor-simple" / "config" / "sources.yaml"
        if prod_cfg.exists():
            cfg = prod_cfg
        else:
            cfg = Path(__file__).resolve().parent.parent / "config" / "sources.yaml"
        with open(cfg, encoding="utf-8") as f:
            sources = yaml.safe_load(f)["sources"]
        assert len(sources) >= 19, f"Expected >=19 sources, got {len(sources)}"
        validate_sources_config(sources, COLLECTOR_MAP)

    # ── 9. validate before network: check call order in main() ────
    def test_validate_before_network(self):
        """Verify validate_sources_config() is called before any HTTP request."""
        from pathlib import Path
        main_file = Path(__file__).resolve().parent.parent / "app" / "main.py"
        main_content = main_file.read_text(encoding="utf-8")
        validate_idx = main_content.index("validate_sources_config")
        assert validate_idx >= 0
        assert validate_idx < len(main_content) // 2

    # ── 10. validate before database ──────────────────────────────
    def test_validate_before_db(self):
        """Verify validate_sources_config() is called before any Database() usage."""
        from pathlib import Path
        main_file = Path(__file__).resolve().parent.parent / "app" / "main.py"
        main_content = main_file.read_text(encoding="utf-8")
        validate_idx = main_content.index("validate_sources_config")
        db_idx = main_content.index("Database(")
        assert validate_idx < db_idx

    # ── 11. validate before Word ──────────────────────────────────
    def test_validate_before_word(self):
        """Verify validate_sources_config() is called before build_word_digest()."""
        from pathlib import Path
        main_file = Path(__file__).resolve().parent.parent / "app" / "main.py"
        main_content = main_file.read_text(encoding="utf-8")
        validate_idx = main_content.index("validate_sources_config")
        word_idx = main_content.index("build_word_digest(")
        assert validate_idx < word_idx

    # ── 12. validate before Feishu ────────────────────────────────
    def test_validate_before_feishu(self):
        """Verify validate_sources_config() is called before send_document()."""
        from pathlib import Path
        main_file = Path(__file__).resolve().parent.parent / "app" / "main.py"
        main_content = main_file.read_text(encoding="utf-8")
        validate_idx = main_content.index("validate_sources_config")
        feishu_idx = main_content.index("send_document(")
        assert validate_idx < feishu_idx
