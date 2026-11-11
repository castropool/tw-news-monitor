from pathlib import Path
from copy import deepcopy

import yaml

from app.main import COLLECTOR_MAP, load_sources, validate_sources_config


ROOT = Path(__file__).resolve().parents[1]


def test_four_exact_international_ids_are_independent_and_disabled():
    ids = {source["id"]: source for source in load_sources(ROOT / "config" / "sources.yaml")}
    for source_id in (
        "reuters_international",
        "ft_alphaville",
        "wsj_newsletter",
        "bloomberg_newsletter",
    ):
        assert ids[source_id]["enabled"] is False
    assert ids["wsj_international"]["enabled"] is False


def test_international_entries_have_required_access_and_language_fields():
    sources = {source["id"]: source for source in load_sources(ROOT / "config" / "sources.yaml")}
    assert sources["reuters_international"]["access_level"] == "metadata_only"
    assert sources["ft_alphaville"]["access_level"] == "public"
    for source_id in ("wsj_newsletter", "bloomberg_newsletter"):
        assert sources[source_id]["access_level"] == "newsletter"
        assert sources[source_id]["language"] == "en"
    validate_sources_config(list(sources.values()), COLLECTOR_MAP)


def test_malformed_sources_file_fails_closed(tmp_path):
    path = tmp_path / "sources.yaml"
    path.write_text("sources: [not-a-mapping]", encoding="utf-8")
    try:
        load_sources(path)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("malformed source config must fail closed")


def test_international_logic_config_bad_enabled_is_disabled(tmp_path):
    from app.international import load_international_config

    path = tmp_path / "international_media.yaml"
    path.write_text("enabled: 'true'\ndisplay_names: {}\nsource_bonus: {}", encoding="utf-8")
    assert load_international_config(path)["enabled"] is False


def test_partial_international_source_set_fails_closed():
    sources = load_sources(ROOT / "config" / "sources.yaml")
    partial = [source for source in sources if source["id"] != "bloomberg_newsletter"]
    import pytest

    with pytest.raises(SystemExit):
        validate_sources_config(partial, COLLECTOR_MAP)


def test_duplicate_exact_international_id_fails_closed():
    sources = load_sources(ROOT / "config" / "sources.yaml")
    duplicate = deepcopy(next(s for s in sources if s["id"] == "reuters_international"))
    sources.append(duplicate)
    import pytest

    with pytest.raises(SystemExit):
        validate_sources_config(sources, COLLECTOR_MAP)


def test_frozen_wsj_rss_cannot_be_enabled_or_retyped():
    sources = load_sources(ROOT / "config" / "sources.yaml")
    frozen = next(s for s in sources if s["id"] == "wsj_international")
    frozen["enabled"] = True
    import pytest

    with pytest.raises(SystemExit):
        validate_sources_config(sources, COLLECTOR_MAP)


def test_non_string_source_fields_fail_closed_instead_of_leaking_type_error():
    import pytest

    malformed = {
        "id": ["not-hashable"],
        "name": "Malformed",
        "type": ["rss"],
        "category": ["politics"],
        "url": "https://example.invalid/feed",
        "enabled": False,
    }
    with pytest.raises(SystemExit):
        validate_sources_config([malformed], COLLECTOR_MAP)
