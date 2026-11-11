"""Phase R2 workflow tests: scheduling, run store, human review, delivery, word, recovery."""

from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path
from unittest.mock import patch

import pytest

from app.assessment.r2.period import (
    next_scheduled_datetime,
    period_for_run_date,
    report_run_key,
)
from app.assessment.r2.state import ReportRunStore, new_run_record
from app.assessment.r2.review import approve_report, reject_report
from app.assessment.r2.delivery import deliver_report
from app.assessment.r2.generation import run_generation
from app.assessment.word_report_renderer import extract_word_text


def _minimal_config(tmp_path: Path, *, rotated: bool = False) -> Path:
    cfg = {
        "election": {"election_id": "tainan_mayoral_2026", "display_name": "台南市长选举"},
        "schedule": {"run_days": [9, 22], "periods": {
            "day_9": "previous_month_16_to_end",
            "day_22": "current_month_01_to_15",
        }},
        "pipeline": {"lock_dir": "work/locks"},
        "security": {
            "feishu_credentials_rotated_after_incident": rotated,
            "feishu_rotation_acknowledged_at": "2026-08-09" if rotated else None,
        },
    }
    path = tmp_path / "assessment_config.yaml"
    path.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return path


def _ready_run(store: ReportRunStore, run_key: str, *, report_path: Path | None = None) -> dict:
    run = new_run_record(
        run_id="r2_test_00000001",
        run_key=run_key,
        election_id="tainan_mayoral_2026",
        period_start="2026-07-16",
        period_end="2026-07-31",
        trigger_type="manual",
        scheduled_for="2026-08-09",
    )
    run["generation_status"] = "ready_for_human_review"
    run["human_review_status"] = "awaiting_review"
    run["delivery_status"] = "not_attempted"
    if report_path is not None:
        run["output_path"] = str(report_path)
        import hashlib

        run["report_hash"] = hashlib.sha256(report_path.read_bytes()).hexdigest()
    store.save(run)
    return run


# ---------- scheduling ----------


@pytest.mark.parametrize(
    ("run_date", "expected"),
    [
        (date(2026, 8, 9), (date(2026, 7, 16), date(2026, 7, 31))),
        (date(2026, 8, 22), (date(2026, 8, 1), date(2026, 8, 15))),
        (date(2026, 1, 9), (date(2025, 12, 16), date(2025, 12, 31))),
        (date(2024, 2, 9), (date(2024, 1, 16), date(2024, 1, 31))),
        (date(2024, 2, 22), (date(2024, 2, 1), date(2024, 2, 15))),
        (date(2025, 2, 22), (date(2025, 2, 1), date(2025, 2, 15))),
    ],
)
def test_period_rules(run_date, expected):
    assert period_for_run_date(run_date) == expected


def test_next_scheduled_datetime_is_9_or_22_at_0900_taipei():
    after = datetime(2026, 8, 9, 8, 0, tzinfo=__import__("zoneinfo").ZoneInfo("Asia/Taipei"))
    nxt = next_scheduled_datetime(after)
    assert nxt.hour == 9 and nxt.minute == 0
    assert nxt.day in (9, 22)
    assert nxt.isoformat().endswith("+08:00")


def test_run_key_format():
    assert (
        report_run_key("tainan_mayoral_2026", date(2026, 7, 16), date(2026, 7, 31))
        == "tainan_mayoral_2026__20260716__20260731"
    )


# ---------- run store ----------


def test_run_store_roundtrip_and_append_only(tmp_path):
    store = ReportRunStore(tmp_path)
    run = new_run_record(
        run_id="r1", run_key="k1", election_id="e", period_start="2026-07-16",
        period_end="2026-07-31", trigger_type="manual", scheduled_for="2026-08-09",
    )
    store.save(run)
    assert store.get("k1")["run_id"] == "r1"
    assert store.latest()["run_key"] == "k1"
    store.append_review("k1", {"review_id": "a", "decision": "approve"})
    store.append_review("k1", {"review_id": "b", "decision": "reject"})
    assert [r["review_id"] for r in store.reviews("k1")] == ["a", "b"]
    store.append_delivery("k1", {"delivery_id": "d1"})
    assert len(store.deliveries("k1")) == 1


# ---------- human review ----------


def test_approve_and_reject_append_only(tmp_path):
    store = ReportRunStore(tmp_path)
    report_path = tmp_path / "report.json"
    report_path.write_text('{"ok": true}', encoding="utf-8")
    run = _ready_run(store, "tainan_mayoral_2026__20260716__20260731", report_path=report_path)
    approved = approve_report(store, run["run_key"], reviewer="tester")
    assert approved["run"]["human_review_status"] == "human_approved"
    assert approved["run"]["delivery_status"] == "delivery_pending"
    with pytest.raises(ValueError, match="仅 ready_for_human_review 可批准"):
        approve_report(store, run["run_key"], reviewer="tester")

    run2 = _ready_run(store, "tainan_mayoral_2026__20260801__20260815", report_path=report_path)
    rejected = reject_report(store, run2["run_key"], reviewer="tester", reason="措辞需调整")
    assert rejected["run"]["human_review_status"] == "human_rejected"
    assert len(store.reviews(run2["run_key"])) == 1
    assert store.reviews(run2["run_key"])[0]["reason"] == "措辞需调整"


def test_approve_blocks_when_report_hash_changed(tmp_path):
    store = ReportRunStore(tmp_path)
    report_path = tmp_path / "report.json"
    report_path.write_text('{"ok": true}', encoding="utf-8")
    run = _ready_run(store, "k1", report_path=report_path)
    report_path.write_text('{"ok": false}', encoding="utf-8")
    with pytest.raises(ValueError, match="BLOCKED_REPORT_CHANGED"):
        approve_report(store, run["run_key"], reviewer="tester")


# ---------- delivery ----------


def test_delivery_requires_approval(tmp_path):
    store = ReportRunStore(tmp_path)
    run = _ready_run(store, "k1")
    with pytest.raises(ValueError, match="DELIVERY_REQUIRES_APPROVAL"):
        deliver_report(store=store, run_key=run["run_key"], config_path=_minimal_config(tmp_path))


def test_delivery_success_and_idempotency(tmp_path):
    store = ReportRunStore(tmp_path)
    report_path = tmp_path / "report.json"
    report_path.write_text('{"ok": true}', encoding="utf-8")
    word_path = tmp_path / "word.docx"
    word_path.write_bytes(b"docx")
    run = _ready_run(store, "k1", report_path=report_path)
    run["word_path"] = str(word_path)
    import hashlib

    run["word_hash"] = hashlib.sha256(b"docx").hexdigest()
    store.save(run)
    approve_report(store, run["run_key"], reviewer="tester")

    result = deliver_report(
        store=store, run_key=run["run_key"], config_path=_minimal_config(tmp_path),
        provider="mock", mode="development",
    )
    assert result["status"] == "delivered"
    assert store.get("k1")["delivery_status"] == "delivered"

    second = deliver_report(
        store=store, run_key=run["run_key"], config_path=_minimal_config(tmp_path),
        provider="mock", mode="development",
    )
    assert second["status"] == "already_delivered"
    assert second["delivery_idempotent"] is True
    assert len(store.deliveries("k1")) == 1


def test_delivery_failure_then_retry(tmp_path):
    store = ReportRunStore(tmp_path)
    report_path = tmp_path / "report.json"
    report_path.write_text('{"ok": true}', encoding="utf-8")
    word_path = tmp_path / "word.docx"
    word_path.write_bytes(b"docx")
    run = _ready_run(store, "k1", report_path=report_path)
    run["word_path"] = str(word_path)
    import hashlib

    run["word_hash"] = hashlib.sha256(b"docx").hexdigest()
    store.save(run)
    approve_report(store, run["run_key"], reviewer="tester")

    config = _minimal_config(tmp_path)
    with patch("app.assessment.r2.delivery.create_delivery") as mk:
        from app.assessment.delivery.base_delivery import DeliveryResult

        mk.return_value.deliver.return_value = DeliveryResult(
            provider="mock", delivery_mode="mock", success=False, warnings=["boom"]
        )
        failed = deliver_report(
            store=store, run_key=run["run_key"], config_path=config,
            provider="mock", mode="development",
        )
    assert failed["status"] == "delivery_failed"
    assert store.get("k1")["delivery_status"] == "delivery_failed"

    ok = deliver_report(
        store=store, run_key=run["run_key"], config_path=config,
        provider="mock", mode="development",
    )
    assert ok["status"] == "delivered"


def test_feishu_gate_blocks_when_rotation_false(tmp_path):
    store = ReportRunStore(tmp_path)
    report_path = tmp_path / "report.json"
    report_path.write_text('{"ok": true}', encoding="utf-8")
    word_path = tmp_path / "word.docx"
    word_path.write_bytes(b"docx")
    run = _ready_run(store, "k1", report_path=report_path)
    run["word_path"] = str(word_path)
    import hashlib

    run["word_hash"] = hashlib.sha256(b"docx").hexdigest()
    store.save(run)
    approve_report(store, run["run_key"], reviewer="tester")
    result = deliver_report(
        store=store, run_key=run["run_key"],
        config_path=_minimal_config(tmp_path, rotated=False),
        provider="feishu", mode="production",
    )
    assert result["status"] == "blocked"
    assert result["blocker"] == "MANUAL_FEISHU_CREDENTIAL_ROTATION_REQUIRED"


# ---------- word ----------


def _minimal_report() -> dict:
    return {
        "schema_version": "1.1",
        "report_id": "test",
        "election_id": "tainan_mayoral_2026",
        "report_period": {"period_start": "2026-07-16", "period_end": "2026-07-31"},
        "generation_mode": "final",
        "report_status": "generated",
        "title": "台南市长选情研判报告",
        "title_claim_ids": [],
        "overall_judgment_claim_ids": [],
        "required_disclosures": [],
        "sections": [
            {
                "section_id": "S01",
                "heading": "一、总体判断",
                "section_purpose": "",
                "claim_ids": ["c1"],
            },
            {
                "section_id": "S02",
                "heading": "二、本期关键变化",
                "section_purpose": "",
                "claim_ids": ["c2"],
            },
            {
                "section_id": "S03",
                "heading": "三、陈亭妃整合进展",
                "section_purpose": "",
                "claim_ids": ["c3"],
            },
            {
                "section_id": "S04",
                "heading": "四、谢龙介组织及竞选动作",
                "section_purpose": "",
                "claim_ids": ["c4"],
            },
            {
                "section_id": "S05",
                "heading": "五、蓝白合作变化",
                "section_purpose": "",
                "claim_ids": ["c5"],
            },
            {
                "section_id": "S06",
                "heading": "六、民调与治理议题",
                "section_purpose": "",
                "claim_ids": ["c6"],
            },
            {
                "section_id": "S07",
                "heading": "七、未来半月走势",
                "section_purpose": "",
                "claim_ids": ["c7"],
            },
            {
                "section_id": "S08",
                "heading": "八、证据限制",
                "section_purpose": "",
                "claim_ids": ["c8"],
            },
        ],
        "claims": [
            {"claim_id": f"c{i}", "claim_text": f"第{i}节判断内容。", "claim_type": "current_assessment"}
            for i in range(1, 9)
        ],
        "do_not_infer_compliance": [],
        "report_statistics": {},
        "data_context": {
            "facts_cutoff": "2026-08-08",
            "poll_cutoff": "2026-03-12",
            "period_start": "2026-07-16",
            "period_end": "2026-07-31",
            "uncovered_date_range": [],
        },
    }


def test_word_renders_eight_sections_without_internal_ids(tmp_path):
    from app.assessment.word_report_renderer import render_word_report

    report = _minimal_report()
    render_word_report(report, output_dir=tmp_path, mode="development")
    docx = next(tmp_path.glob("*.docx"))
    text = extract_word_text(docx)
    for heading in (
        "一、总体判断",
        "二、本期关键变化",
        "三、陈亭妃整合进展",
        "四、谢龙介组织及竞选动作",
        "五、蓝白合作变化",
        "六、民调与治理议题",
        "七、未来半月走势",
        "八、证据限制",
    ):
        assert heading in text
    for leaked in ("claim_id", "C_INT_", "evt_tnn_", "src_", "business_hash", "schema_version"):
        assert leaked not in text


# ---------- recovery / idempotency ----------


def test_scheduled_entry_skips_existing_generated_run(tmp_path):
    config = _minimal_config(tmp_path)
    store = ReportRunStore(tmp_path / "runs")
    run = _ready_run(store, "tainan_mayoral_2026__20260716__20260731")
    result = run_generation(
        config_path=config,
        runs_root=tmp_path / "runs",
        as_of=date(2026, 8, 9),
        period_start=date(2026, 7, 16),
        period_end=date(2026, 7, 31),
        trigger_type="scheduled",
        check_only=False,
        force_regenerate=False,
    )
    assert result["code"] == "SKIPPED_ALREADY_GENERATED"
    assert result["run_id"] == run["run_id"]


def test_period_gate_blocks_when_facts_cutoff_insufficient(tmp_path):
    config = _minimal_config(tmp_path)
    fake_frozen = {
        "input_hash": "h",
        "facts_cutoff": "2026-08-08",
        "poll_cutoff": "2026-03-12",
        "coverage_version": "fact_coverage_test_v1",
    }
    with patch("app.assessment.r2.generation._freeze_production_input", return_value=fake_frozen):
        result = run_generation(
            config_path=config,
            runs_root=tmp_path / "runs",
            as_of=date(2026, 8, 22),
            period_start=date(2026, 8, 1),
            period_end=date(2026, 8, 15),
            trigger_type="scheduled",
            check_only=False,
            force_regenerate=False,
        )
    assert result["code"] == "SKIPPED_PERIOD_NOT_READY"


def test_check_only_passes_period_gate(tmp_path):
    config = _minimal_config(tmp_path)
    fake_frozen = {
        "input_hash": "h",
        "facts_cutoff": "2026-08-08",
        "poll_cutoff": "2026-03-12",
        "coverage_version": "fact_coverage_test_v1",
    }
    with patch("app.assessment.r2.generation._freeze_production_input", return_value=fake_frozen):
        result = run_generation(
            config_path=config,
            runs_root=tmp_path / "runs",
            as_of=date(2026, 8, 9),
            period_start=date(2026, 7, 16),
            period_end=date(2026, 7, 31),
            trigger_type="controlled",
            check_only=True,
            force_regenerate=False,
        )
    assert result["code"] == "CHECK_OK"
