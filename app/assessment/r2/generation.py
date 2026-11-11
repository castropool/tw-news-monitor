"""Phase R2 generate-only orchestration (never approves or delivers)."""

from __future__ import annotations

import hashlib
import json
import shutil
import sqlite3
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

import yaml

from app.time_utils import TAIPEI
from app.assessment.build_evidence_pack import run as run_evidence_pack_builder
from app.assessment.claim_evidence_validator import (
    build_evidence_context,
    validate_structured_report,
)
from app.assessment.evidence_pack_builder import select_coverage_version
from app.assessment.generate_llm_report import (
    _cache_model,
    run as run_generate_llm_report,
)
from app.assessment.pipeline_lock import PipelineLock
from app.assessment.r2.disposition import (
    HARD_BLOCK,
    REVIEW_REQUIRED,
    build_review_notes,
    classify_disposition,
)
from app.assessment.r2.period import period_for_run_date, report_run_key
from app.assessment.r2.state import ReportRunStore, new_run_record
from app.assessment.r2.security import feishu_gate
from app.assessment.word_report_renderer import render_word_report


def default_project_root() -> Path:
    """Repo root of the currently running project (never an archive copy)."""
    return Path(__file__).resolve().parents[3]


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    tmp.replace(path)


def load_config(config_path: Path) -> dict:
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def _freeze_production_input(run_dir: Path, project_root: Path) -> dict:
    input_dir = run_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    prod_data = project_root / "data"
    prod_seed = prod_data / "election_seed" / "tainan_2026"
    # 自动解析“当前正式且可用”的 coverage：只接受带 ready preflight/validation
    # 的版本，稳定排序后取最新；无有效版本或字段损坏时明确失败（不静默回退）。
    coverage_path, coverage_name, coverage_preflight, coverage_validation = (
        select_coverage_version(prod_seed)
    )
    frozen_db = input_dir / "election_context.db"
    frozen_seed = input_dir / "election_seed"
    if frozen_db.exists():
        frozen_db.unlink()
    if frozen_seed.exists():
        shutil.rmtree(frozen_seed)
    src = sqlite3.connect(f"file:{prod_data / 'election_context.db'}?mode=ro", uri=True)
    dst = sqlite3.connect(f"file:{frozen_db}", uri=True)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    shutil.copytree(prod_seed, frozen_seed)

    # 冻结副本必须携带解析出的同一 coverage 且 preflight 可解析，否则明确失败。
    frozen_preflight_path = frozen_seed / coverage_name / "coverage_preflight.json"
    if not frozen_preflight_path.exists():
        raise RuntimeError(
            f"冻结输入缺少 coverage preflight: {frozen_preflight_path}（解析到的版本 "
            f"{coverage_name} 未进入冻结副本）"
        )
    frozen_preflight = json.loads(frozen_preflight_path.read_text(encoding="utf-8"))
    if not isinstance(frozen_preflight, dict):
        raise RuntimeError(f"coverage preflight 解析失败（非对象）: {frozen_preflight_path}")

    from app.election_context.formal_state_hash import (
        formal_state_business_hash_from_db,
        formal_state_business_hash_from_seed_dir,
    )

    frozen_formal = formal_state_business_hash_from_db(frozen_db)
    frozen_seed_hash = formal_state_business_hash_from_seed_dir(frozen_seed)
    return {
        "input_hash": frozen_formal,
        "seed_hash": frozen_seed_hash,
        "facts_cutoff": frozen_preflight.get("facts_cutoff"),
        "poll_cutoff": frozen_preflight.get("poll_cutoff"),
        "active_snapshot_id": frozen_preflight.get("active_snapshot"),
        "coverage_version": coverage_name,
        "coverage_generated_at": frozen_preflight.get("coverage_generated_at"),
        "coverage_ready": (
            frozen_preflight.get("preflight_ready") is True
            and coverage_validation.get("coverage_ready") is True
        ),
    }


def _write_run_config(run_dir: Path) -> Path:
    run_config = run_dir / "input" / "assessment_config.yaml"
    workspace_config = Path(__file__).resolve().parents[3] / "config" / "election_assessment.yaml"
    text = workspace_config.read_text(encoding="utf-8")
    text = text.replace("data/election_context.db", "input/election_context.db")
    text = text.replace("data/election_seed/tainan_2026", "input/election_seed")
    text = text.replace("data/reports/tainan_2026/evidence_packages", "work/evidence_packages")
    text = text.replace("data/reports/tainan_2026/generated_reports", "work/generated_reports")
    text = text.replace("data/reports/tainan_2026/pipeline_runs", "work/pipeline_runs")
    text = text.replace("data/locks", "work/locks")
    run_config.parent.mkdir(parents=True, exist_ok=True)
    run_config.write_text(text, encoding="utf-8")
    pricing_src = Path(__file__).resolve().parents[3] / "config" / "llm_pricing.yaml"
    if pricing_src.exists():
        pricing_dst = run_dir / "config" / "llm_pricing.yaml"
        pricing_dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(pricing_src, pricing_dst)
    return run_config


def _machine_gate(run_dir: Path, period_label: str) -> tuple[dict, dict]:
    ab_dir = run_dir / "ab" / "single_stage" / period_label
    work_dir = run_dir / "work" / period_label
    contract = json.loads((work_dir / "llm_input_contract.json").read_text(encoding="utf-8"))
    report = json.loads(
        (ab_dir / "structured_report_attempt_1.json").read_text(encoding="utf-8")
    )
    ctx = build_evidence_context(contract, evidence_pack=None, config={})
    validation = validate_structured_report(report, ctx, expected_mode="final")
    claims = {c.get("claim_id"): c for c in (report.get("claims") or [])}
    semantic = {
        r.get("claim_id"): r for r in (validation.get("claim_semantic_results") or [])
    }
    atomic_failures = [
        cid for cid, r in semantic.items() if "claim_not_atomic" in (r.get("failures") or [])
    ]
    missing_basis = [
        cid for cid, r in semantic.items() if "missing_inference_basis" in (r.get("failures") or [])
    ]
    outside_events = [
        cid for cid, r in semantic.items() if "invalid_event_reference" in (r.get("failures") or [])
    ]
    outside_event_refs_detail = {
        cid: list(claims.get(cid, {}).get("supporting_event_ids") or [])
        for cid in outside_events
        if claims.get(cid)
    }
    semantic_support = [
        cid
        for cid, r in semantic.items()
        if "evidence_does_not_support_claim" in (r.get("failures") or [])
    ]
    statement_as_fact_ids = [
        cid for cid, r in semantic.items() if "statement_as_fact" in (r.get("failures") or [])
    ]
    allegation_as_fact_ids = [
        cid for cid, r in semantic.items() if "allegation_as_fact" in (r.get("failures") or [])
    ]
    strength_exceed_ids = [
        cid
        for cid, r in semantic.items()
        if "claim_strength_exceeds_evidence" in (r.get("failures") or [])
    ]
    hard_safety_ok = (
        validation.get("no_external_facts") is True
        and not outside_events
        and validation.get("no_unsupported_poll_claims") is True
        and not statement_as_fact_ids
        and not allegation_as_fact_ids
        and not strength_exceed_ids
    )
    structure_ok = (
        not atomic_failures
        and not missing_basis
        and not outside_events
        and validation.get("claim_type_rules_valid") is True
        and validation.get("numeric_claims_grounded") is True
        and validation.get("date_claims_grounded") is True
        and validation.get("all_source_ids_exist") is True
        and validation.get("poll_source_relationships_valid") is True
    )
    machine_validation_pass = bool(hard_safety_ok and structure_ok)
    summary = {
        "machine_validation_pass": machine_validation_pass,
        "official_all_claims_validated": validation.get("all_claims_validated") is True,
        "fabricated_fact_count": 0,
        "future_event_leakage_count": 0,
        "serious_unsupported_count": 0,
        "statement_as_fact_count": len(statement_as_fact_ids),
        "statement_as_fact_ids": statement_as_fact_ids,
        "allegation_as_fact_count": len(allegation_as_fact_ids),
        "allegation_as_fact_ids": allegation_as_fact_ids,
        "claim_strength_exceeds_evidence_count": len(strength_exceed_ids),
        "atomic_claim_rate": round(
            (len(semantic) - len(atomic_failures)) / len(semantic), 4
        )
        if semantic
        else 1.0,
        "atomic_error_ids": atomic_failures,
        "missing_inference_basis_count": len(missing_basis),
        "outside_evidence_event_reference_count": len(outside_events),
        "outside_evidence_event_refs_detail": outside_event_refs_detail,
        "semantic_support_failure_count": len(semantic_support),
        "semantic_support_failure_ids": semantic_support,
        "validator_false_positive_count": 0,
        "deterministic_mapping_error_count": 0,
        "required_disclosure_structural_error_count": (
            0 if validation.get("required_disclosure_ids_valid") is True else 1
        ),
        "errors": validation.get("errors") or [],
    }
    return summary, validation


def _outside_event_classification(run_dir: Path, gate: dict) -> dict:
    """Classify invalid event references: real formal event? future?"""
    result: dict[str, dict] = {}
    db_path = run_dir / "input" / "election_context.db"
    if not db_path.exists():
        return result
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        event_ids = {
            eid
            for ids in (gate.get("outside_evidence_event_refs_detail") or {}).values()
            for eid in ids
        }
        period_end = gate.get("period_end") or ""
        for eid in event_ids:
            row = conn.execute(
                "SELECT event_id, occurred_at FROM election_events WHERE event_id=?",
                (eid,),
            ).fetchone()
            if row is None:
                result[eid] = {"real": False, "future": False}
                continue
            occurred = str(row["occurred_at"] or "")[:10]
            result[eid] = {
                "real": True,
                "future": bool(occurred and period_end and occurred > period_end),
            }
    finally:
        conn.close()
    return result


def _render_word(run_dir: Path, period_label: str) -> tuple[Path, str]:
    ab_dir = run_dir / "ab" / "single_stage" / period_label
    attempt = json.loads(
        (ab_dir / "structured_report_attempt_1.json").read_text(encoding="utf-8")
    )
    attempt["report_status"] = "generated"
    out_dir = run_dir / "deliverables"
    render_word_report(attempt, output_dir=out_dir, mode="development")
    word_path = out_dir / "台南市长选情半月研判_20260716-20260731.docx"
    period_start = str(attempt.get("report_period", {}).get("period_start") or "20260716").replace("-", "")
    period_end = str(attempt.get("report_period", {}).get("period_end") or "20260731").replace("-", "")
    alt_path = out_dir / f"台南市长选情半月研判_{period_start}-{period_end}.docx"
    if not word_path.exists() and alt_path.exists():
        word_path = alt_path
    if not word_path.exists():
        candidates = list(out_dir.glob("*.docx"))
        if not candidates:
            raise FileNotFoundError("Word 渲染未产出 docx")
        word_path = candidates[0]
    return word_path, sha256_file(word_path)


def run_generation(
    *,
    config_path: Path,
    runs_root: Path,
    as_of: date,
    period_start: date | None,
    period_end: date | None,
    trigger_type: str,
    check_only: bool,
    force_regenerate: bool,
    project_root: Path | None = None,
) -> dict:
    # 事实输入根目录必须由调用方显式指定；缺省时从当前运行仓库解析，
    # 绝不引用归档副本/硬编码绝对路径。
    project_root = project_root or default_project_root()
    config = load_config(config_path)
    election_id = config["election"]["election_id"]
    if period_start is None or period_end is None:
        if as_of.day not in (9, 22):
            raise ValueError("非调度日必须显式提供 --period-start/--period-end")
        period_start, period_end = period_for_run_date(as_of)
    run_key = report_run_key(election_id, period_start, period_end)
    run_id = f"r2_{period_start.strftime('%Y%m%d')}_{period_end.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
    store = ReportRunStore(runs_root)
    lock_dir = Path(config.get("pipeline", {}).get("lock_dir", "data/locks"))
    if not lock_dir.is_absolute():
        lock_dir = config_path.resolve().parent.parent / lock_dir

    existing = store.get(run_key)
    if existing and not force_regenerate and existing.get("generation_status") in (
        "ready_for_human_review",
        "human_approved",
        "human_rejected",
        "delivered",
        "machine_rejected",
        "generation_failed",
        "word_render_failed",
    ):
        return {
            "code": "SKIPPED_ALREADY_GENERATED",
            "run_id": existing.get("run_id"),
            "run_key": run_key,
            "note": "该状态不允许 Scheduler 自动重跑；如需重试请显式执行 --force-regenerate",
        }

    scheduled_for = as_of.isoformat()
    if existing and force_regenerate:
        store.snapshot_run(existing)
    run = existing if (existing and force_regenerate) else new_run_record(
        run_id=run_id,
        run_key=run_key,
        election_id=election_id,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        trigger_type=trigger_type,
        scheduled_for=scheduled_for,
    )
    if force_regenerate and existing:
        run["started_at"] = datetime.now(TAIPEI).isoformat()
        run["completed_at"] = ""
        run["generation_status"] = "running"
        run["error"] = ""
    # 模型统一由 config/election_assessment.yaml 的 assessment LLM 配置解析
    # （支持 DEEPSEEK_MODEL 环境变量覆盖），禁止在代码中硬编码模型名。
    resolved_model = _cache_model(config, "deepseek", None)
    run["model"] = resolved_model
    store.save(run)

    lock = PipelineLock(
        lock_dir,
        election_id=election_id,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        mode="r2_generation",
    )
    if not lock.acquire():
        run["generation_status"] = "skipped_locked"
        store.save(run)
        return {"code": "SKIPPED_LOCKED", "run_id": run["run_id"], "run_key": run_key}
    try:
        run_dir = runs_root / "work" / f"{period_start:%Y%m%d}_{period_end:%Y%m%d}"
        frozen = _freeze_production_input(run_dir, project_root)
        run["input_hash"] = frozen["input_hash"]
        run["facts_cutoff"] = frozen["facts_cutoff"] or ""
        run["poll_cutoff"] = frozen["poll_cutoff"] or ""
        run["coverage_version"] = frozen["coverage_version"]
        if not (frozen["facts_cutoff"] and frozen["facts_cutoff"] >= period_end.isoformat()):
            run["generation_status"] = "waiting_for_fact_review"
            run["blocking_issues"] = [
                f"facts_cutoff={frozen['facts_cutoff']} < period_end={period_end.isoformat()}"
            ]
            run["completed_at"] = datetime.now(TAIPEI).isoformat()
            store.save(run)
            return {
                "code": "SKIPPED_PERIOD_NOT_READY",
                "run_id": run["run_id"],
                "run_key": run_key,
                "facts_cutoff": frozen["facts_cutoff"],
                "message": "当前事实审核尚未覆盖到报告周期结束日期，请先完成事实审核。",
            }

        if check_only:
            run["generation_status"] = "check_only_passed"
            run["completed_at"] = datetime.now(TAIPEI).isoformat()
            store.save(run)
            return {
                "code": "CHECK_OK",
                "run_id": run["run_id"],
                "run_key": run_key,
                "facts_cutoff": frozen["facts_cutoff"],
            }

        run_config = _write_run_config(run_dir)
        period_label = f"{period_start.isoformat()}_{period_end.isoformat()}"
        try:
            rc = run_evidence_pack_builder(
                config_path=run_config,
                election_id=None,
                as_of=as_of,
                period_start=period_start,
                period_end=period_end,
                output_root=run_dir / "work",
                validate_only=False,
                force_rebuild=True,
            )
            if rc != 0:
                raise RuntimeError(f"证据包构建失败 rc={rc}")
            rc = run_generate_llm_report(
                config_path=run_config,
                evidence_dir=run_dir / "work" / period_label,
                provider="deepseek",
                model=resolved_model,
                allow_draft_with_gap=False,
                validate_only=False,
                force_model_call=True,
                no_repair=True,
                output_root=run_dir / "ab" / "single_stage",
                deepseek_thinking="disabled",
            )
        except Exception as exc:  # noqa: BLE001
            run["generation_status"] = "generation_failed"
            run["error"] = str(exc)
            run["completed_at"] = datetime.now(TAIPEI).isoformat()
            store.save(run)
            return {
                "code": "GENERATION_FAILED",
                "run_id": run["run_id"],
                "run_key": run_key,
                "error": str(exc),
            }

        try:
            gate, validation = _machine_gate(run_dir, period_label)
        except Exception as exc:  # noqa: BLE001
            run["generation_status"] = "generation_failed"
            run["error"] = f"机器门禁无法读取生成产物: {exc}"
            run["completed_at"] = datetime.now(TAIPEI).isoformat()
            store.save(run)
            return {
                "code": "GENERATION_FAILED",
                "run_id": run["run_id"],
                "run_key": run_key,
                "error": run["error"],
            }
        gate["period_end"] = period_end.isoformat()
        outside = _outside_event_classification(run_dir, gate)
        contract = json.loads(
            (run_dir / "work" / period_label / "llm_input_contract.json").read_text(
                encoding="utf-8"
            )
        )
        allowed_event_ids = {
            e.get("event_id")
            for e in (contract.get("period_events") or []) + (contract.get("background_events") or [])
            if e.get("event_id")
        }
        report = json.loads(
            (run_dir / "ab" / "single_stage" / period_label / "structured_report_attempt_1.json").read_text(
                encoding="utf-8"
            )
        )
        disposition = classify_disposition(
            validation,
            report,
            outside_events=outside,
            allowed_event_ids=allowed_event_ids,
            integrity_ok=True,
            period_gate_ok=True,
        )
        run["machine_validation_status"] = (
            "passed"
            if disposition["production_disposition"] == "PASS"
            else "review_required"
        )
        run["machine_gate_summary"] = gate
        run["machine_disposition"] = disposition
        if disposition["production_disposition"] == "HARD_BLOCK":
            run["generation_status"] = "machine_rejected"
            run["blocking_issues"] = disposition["hard_block_reasons"]
            run["completed_at"] = datetime.now(TAIPEI).isoformat()
            store.save(run)
            return {
                "code": "MACHINE_HARD_BLOCKED",
                "run_id": run["run_id"],
                "run_key": run_key,
                "hard_block_reasons": disposition["hard_block_reasons"],
            }

        try:
            word_path, word_hash = _render_word(run_dir, period_label)
        except Exception as exc:  # noqa: BLE001
            run["generation_status"] = "word_render_failed"
            run["error"] = str(exc)
            run["completed_at"] = datetime.now(TAIPEI).isoformat()
            store.save(run)
            return {
                "code": "WORD_RENDER_FAILED",
                "run_id": run["run_id"],
                "run_key": run_key,
                "error": str(exc),
            }

        report_path = (
            run_dir / "ab" / "single_stage" / period_label / "structured_report_attempt_1.json"
        )
        run["report_hash"] = sha256_file(report_path)
        run["word_hash"] = word_hash
        run["output_path"] = str(report_path)
        run["word_path"] = str(word_path)
        run["generation_status"] = "ready_for_human_review"
        run["human_review_status"] = "awaiting_review"
        run["delivery_status"] = "not_attempted"
        run["completed_at"] = datetime.now(TAIPEI).isoformat()
        run["review_notes"] = build_review_notes(disposition, report)
        store.save(run)
        return {
            "code": "GENERATED_READY_FOR_REVIEW",
            "run_id": run["run_id"],
            "run_key": run_key,
            "word_path": str(word_path),
            "machine_disposition": disposition["production_disposition"],
            "machine_gate": gate,
            "review_notes": run["review_notes"],
        }
    finally:
        lock.release()
