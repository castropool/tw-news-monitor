import json
from pathlib import Path

import pytest

from validation.international_media.rc_manifest import (
    build_rc_manifest,
    load_rc_manifest,
    verify_artifact_hashes,
)
from validation.international_media.run_isolated import load_runs, run_isolated_collection
from validation.international_media.verify_word_structure import verify_word_document


def test_two_pass_isolation_has_no_duplicate_delivery_and_no_real_notifier_call(tmp_path):
    config = {
        "sources": [
            {"id": source_id, "type": source_type, "url": f"fixture://{source_id}", "category": "international", "enabled": False}
            for source_id, source_type in (
                ("reuters_international", "reuters"),
                ("ft_alphaville", "ft_alphaville"),
                ("wsj_newsletter", "wsj_newsletter"),
                ("bloomberg_newsletter", "bloomberg_newsletter"),
            )
        ]
    }
    first_result = run_isolated_collection(config, tmp_path / "first.db", tmp_path / "first_reports", True)
    second_result = run_isolated_collection(config, tmp_path / "second.db", tmp_path / "second_reports", True)
    first, second = load_runs(
        tmp_path / "first_reports" / f"{first_result.run_id}.json",
        tmp_path / "second_reports" / f"{second_result.run_id}.json",
    )
    assert second.inserted == 0
    assert second.duplicate_word_items == 0
    assert second.real_feishu_calls == 0
    assert first.real_feishu_calls == 0
    assert first.per_source["reuters_international"]["fetched"] >= 0
    assert second.per_source["ft_alphaville"]["errors"] == []


def test_word_report_records_structure_and_operator_render_gate():
    report = json.loads(
        Path("validation/international_media/word_structure_report.json").read_text(
            encoding="utf-8"
        )
    )
    assert report["ooxml"]["document_xml"] is True
    assert report["ooxml"]["international_heading"] is True
    assert report["render"]["status"] in {"verified", "operator_action_required"}
    if report["render"]["status"] == "operator_action_required":
        assert report["render"]["reason"]
    assert report["checks"]["international_heading"] is True
    assert report["checks"]["unicode_text"] is True
    assert report["checks"]["urls_are_http_or_https"] is True
    assert report["checks"]["duplicate_urls"] is True


def test_word_helper_checks_real_docx_without_claiming_pixel_review():
    report = verify_word_document(
        "validation/international_media/wave5_c_docx/台湾新闻监测_2026-08-15_1000.docx",
        verification_date="2026-08-15",
    )
    assert report.ooxml["status"] == "pass"
    assert report.checks["international_item_count"] >= 1
    assert report.render["status"] == "operator_action_required"


def test_rc_manifest_builder_excludes_itself_and_is_recomputable(tmp_path):
    evidence = tmp_path / "evidence.txt"
    evidence.write_text("stable evidence\n", encoding="utf-8")
    output = tmp_path / "rc_manifest.json"
    base = {
        "generated_at": "2026-08-15T00:00:00+08:00",
        "final_status": "INTERNATIONAL_MEDIA_NOT_READY",
        "source_status": {
            "reuters_international": "operator_action_required",
            "ft_alphaville": "operator_action_required",
            "wsj_newsletter": "operator_action_required",
            "bloomberg_newsletter": "operator_action_required",
        },
        "sections": {letter: {"status": "operator_action_required", "evidence": ["pending"]} for letter in "ABCDEFGHIJKLMNOPQ"},
        "event_metrics_status": "pass",
        "production_switches": {
            "reuters_international": False,
            "ft_alphaville": False,
            "wsj_newsletter": False,
            "bloomberg_newsletter": False,
        },
        "blockers": ["live evidence not run by scope"],
    }
    first = build_rc_manifest(output, [evidence, output], base=base)
    assert "rc_manifest.json" not in first["artifact_hashes"]
    assert first["manifest_policy"]["self_excluded"] is True
    assert output.exists()
    with pytest.raises(FileExistsError):
        build_rc_manifest(output, [evidence], base=base)


def test_current_rc_hashes_and_production_protection_are_strict():
    manifest = load_rc_manifest("validation/international_media/rc_manifest.json")
    assert manifest.event_metrics_status == "pass"
    assert all(value is False for value in manifest.production_switches.values())
    assert not verify_artifact_hashes("validation/international_media/rc_manifest.json")
    assert all(
        not Path(path).name.lower().startswith("rc_manifest")
        for path in manifest.artifact_hashes
    )


def test_superseded_v1_runs_cannot_be_used_as_rc_evidence():
    payload = json.loads(Path("validation/international_media/isolated_run_1.json").read_text(encoding="utf-8"))
    assert payload["schema_version"] == "1.0"
    assert payload["schema_version"] != "2.0"
