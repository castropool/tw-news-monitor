from validation.international_media.rc_manifest import load_rc_manifest


def test_rc_manifest_requires_all_a_to_q_sections_and_four_source_statuses():
    manifest = load_rc_manifest("validation/international_media/rc_manifest.json")
    assert manifest.has_sections(list("ABCDEFGHIJKLMNOPQ"))
    assert set(manifest.source_status) == {
        "reuters_international",
        "ft_alphaville",
        "wsj_newsletter",
        "bloomberg_newsletter",
    }
    assert manifest.event_metrics_status == "pass"


def test_rc_manifest_keeps_production_switches_false():
    manifest = load_rc_manifest("validation/international_media/rc_manifest.json")
    assert all(value is False for value in manifest.production_switches.values())
