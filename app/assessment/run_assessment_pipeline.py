"""台南选情半月研判总编排器（development / dry_run / production）。"""

from __future__ import annotations

import argparse
import json
import os
import sys
import uuid
from datetime import date, datetime
from pathlib import Path
from typing import Any

from . import __version__ as assessment_version
from .build_evidence_pack import compute_input_hashes, run as build_evidence_pack_run
from .deployment_preflight import build_deployment_preflight, write_preflight_files
from .delivery import create_delivery
from .delivery.errors import DeliveryError
from .evidence_pack_builder import canonical_hash, load_formal_data, load_yaml
from .generate_llm_report import run as generate_report_run
from .llm.provider_factory import REGISTERED_PROVIDERS
from .pipeline_lock import PipelineLock
from .pipeline_manifest import build_pipeline_manifest, build_pipeline_validation
from .pipeline_state import (
    append_stage_result,
    atomic_write_json,
    create_run_dir,
    setup_pipeline_logger,
    write_failure_summary,
    write_latest,
)
from .report_artifact_validator import validate_report_artifact, word_text_signature
from .reporting_period import PeriodError, resolve_reporting_period
from .word_report_renderer import render_word_report


PIPELINE_VERSION = "1.0.0"


def _load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_period(config: dict, args: argparse.Namespace):
    tz_name = config.get("timezone", "Asia/Taipei")
    run_days = tuple(int(x) for x in (config.get("schedule", {}).get("run_days") or [9, 22]))
    raw_rules = (config.get("schedule", {}) or {}).get("periods") or {}
    period_rules = {
        int(key.split("_")[-1]): value
        for key, value in raw_rules.items()
        if key.startswith("day_") and value
    }
    return resolve_reporting_period(
        timezone_name=tz_name,
        run_days=run_days,
        period_rules=period_rules or None,
        as_of=args.as_of,
        explicit_start=args.period_start,
        explicit_end=args.period_end,
    )


def _resolve_provider(config: dict, args: argparse.Namespace) -> str:
    if args.provider:
        return args.provider
    if args.mode == "production":
        return (config.get("llm", {}) or {}).get("default_provider", "deepseek")
    return "mock"


def _resolve_delivery_provider(config: dict, args: argparse.Namespace) -> str:
    if args.delivery_provider:
        return args.delivery_provider
    if args.mode == "production":
        return (config.get("delivery", {}) or {}).get("default_provider", "feishu")
    return "mock"


def _coverage_path(config: dict, root: Path, evidence_dir: Path) -> Path:
    manifest = evidence_dir / "report_run_manifest.json"
    if manifest.exists():
        try:
            value = _load_json(manifest).get("coverage_path")
            if value:
                return Path(value)
        except Exception:  # noqa: BLE001
            pass
    return root / (config.get("paths") or {}).get(
        "coverage_root", "data/election_seed/tainan_2026"
    )


def _build_summary_text(
    structured_report: dict,
    pack: dict,
    docx_filename: str,
    mode: str,
) -> str:
    rp = structured_report.get("report_period") or {}
    data = rp.get("data_status") or {}
    claims = {c.get("claim_id"): c for c in (structured_report.get("claims") or [])}
    overall = []
    for cid in structured_report.get("overall_judgment_claim_ids") or []:
        claim = claims.get(cid)
        if claim and claim.get("claim_text"):
            overall.append(str(claim["claim_text"]))
    lines = [
        "【台南市长选情半月研判】",
        f"标题：{structured_report.get('title') or ''}",
        f"报告周期：{rp.get('period_start')} 至 {rp.get('period_end')}",
        f"报告状态：{structured_report.get('report_status')}",
        f"事实截止日：{data.get('facts_cutoff') or '未披露'}",
        f"民调截止日：{data.get('poll_cutoff') or '未披露'}",
        f"生成模式：{structured_report.get('generation_mode')}",
        f"主要结论摘要：{'；'.join(overall)[:500]}" if overall else "主要结论摘要：无",
        f"Word文件名：{docx_filename}",
        "本地归档状态：已生成",
    ]
    if structured_report.get("generation_mode") == "draft_with_data_gap":
        lines.insert(1, "【数据不完整草稿，请勿作为完整周期报告使用】")
    return "\n".join(lines)


def _report_business_hash(structured_report: dict) -> str:
    """业务哈希：剔除 report_period.run_at 等运行元数据。"""
    report_period = dict(structured_report.get("report_period") or {})
    report_period.pop("run_at", None)
    payload = dict(structured_report)
    payload["report_period"] = report_period
    return canonical_hash(payload)


def _send_failure_alert(
    mode: str,
    config: dict,
    run_dir: Path,
    failure: dict,
) -> str:
    """失败告警：生产飞书（不递归）；开发/干跑写 mock 告警。"""
    lines = [
        "【台南选情研判管道失败告警】",
        f"失败阶段：{failure.get('failed_stage')}",
        f"错误分类：{failure.get('error_category')}",
        f"报告周期：{failure.get('period_start')} 至 {failure.get('period_end')}",
        f"facts_cutoff：{failure.get('facts_cutoff') or '未披露'}",
        f"period_end：{failure.get('period_end')}",
        f"是否生成本地草稿：{failure.get('local_draft_generated')}",
        f"日志文件名：{failure.get('log_filename')}",
        "建议处理动作："
        + "；".join(failure.get("suggested_actions") or ["查看 failure_summary.json 与 pipeline.log"]),
    ]
    text = "\n".join(lines)
    if mode != "production":
        receipt = {
            "alert_mode": "mock",
            "message": text,
            "network_calls": 0,
            "written_at": datetime.now().isoformat(),
        }
        atomic_write_json(run_dir / "mock_alert_receipt.json", receipt)
        return "mock"
    try:
        from .delivery import create_delivery

        delivery = create_delivery("feishu", config=config, mode="production")
        delivery.deliver(
            report_metadata={"title": "失败告警", "period": str(failure.get("report_period"))},
            summary_text=text,
            artifact_paths=[],
            delivery_context={"receipt_path": str(run_dir / "alert_receipt.json")},
        )
        return "delivered"
    except Exception as exc:  # noqa: BLE001
        atomic_write_json(
            run_dir / "alert_receipt.json",
            {
                "alert_mode": "feishu",
                "success": False,
                "error_category": type(exc).__name__,
                "error_message": str(exc),
                "network_calls": 0,
                "written_at": datetime.now().isoformat(),
            },
        )
        return "failed"


def _write_delivery_receipt(run_dir: Path, result) -> None:
    atomic_write_json(run_dir / "delivery_receipt.json", result.to_dict())


def _write_delivery_validation(run_dir: Path, *, success: bool, errors: list[str], warnings: list[str], network_calls: int, skipped: bool = False) -> None:
    atomic_write_json(
        run_dir / "delivery_validation.json",
        {
            "delivery_success": success,
            "skipped": skipped,
            "errors": errors,
            "warnings": warnings,
            "network_calls": network_calls,
            "dry_run_network_calls": 0,
        },
    )


def _write_artifact_manifest(run_dir: Path, render_info: dict, validation: dict) -> None:
    validation = dict(validation)
    validation["docx_path"] = _relative_to_root(validation.get("docx_path") or "")
    atomic_write_json(
        run_dir / "artifact_manifest.json",
        {
            "docx_path": _relative_to_root(render_info["docx_path"]),
            "filename": render_info["filename"],
            "report_mode": render_info["report_mode"],
            "section_count": render_info["section_count"],
            "claim_count": render_info["claim_count"],
            "rendered_claim_count": render_info["rendered_claim_count"],
            "docx_size_bytes": render_info["docx_size_bytes"],
            "artifact_validation": validation,
        },
    )
    atomic_write_json(run_dir / "artifact_validation.json", validation)


def _relative_to_root(path: str) -> str:
    root = Path(__file__).resolve().parent.parent.parent
    try:
        return str(Path(path).resolve().relative_to(root))
    except ValueError:
        return str(path)


def _resolve_failure_data_context(
    config: dict,
    root: Path,
    period,
    evidence_dir: Path,
) -> dict:
    result = {
        "election_id": config.get("election", {}).get("election_id", ""),
        "period_start": period.period_start.isoformat(),
        "period_end": period.period_end.isoformat(),
        "facts_cutoff": None,
        "poll_cutoff": None,
        "active_snapshot_id": None,
        "coverage_version": None,
        "data_context_resolution_error": None,
    }
    try:
        pack_path = evidence_dir / "report_evidence_pack.json"
        if pack_path.exists():
            pack = _load_json(pack_path)
            ds = pack.get("data_status") or {}
            result["facts_cutoff"] = ds.get("facts_cutoff")
            result["poll_cutoff"] = ds.get("poll_cutoff")
            result["active_snapshot_id"] = ds.get("active_snapshot_id") or (
                pack.get("current_snapshot") or {}
            ).get("snapshot_id")
            result["coverage_version"] = ds.get("coverage_version")
        else:
            formal = load_formal_data(config, root, config["election"]["election_id"])
            state = (formal.active_snapshot.get("state") or {}) if formal.active_snapshot else {}
            result["facts_cutoff"] = (
                (state.get("coverage") or {}).get("facts_cutoff")
                or formal.coverage_preflight.get("facts_cutoff")
            )
            result["poll_cutoff"] = (
                (state.get("coverage") or {}).get("poll_cutoff")
                or formal.coverage_preflight.get("poll_cutoff")
            )
            result["active_snapshot_id"] = (
                formal.active_snapshot.get("snapshot_id") if formal.active_snapshot else None
            )
            result["coverage_version"] = formal.coverage_name
    except Exception as exc:  # noqa: BLE001
        result["data_context_resolution_error"] = str(exc)
    return result


def _previous_run_dir(period_runs_root: Path, current_run_id: str) -> Path | None:
    if not period_runs_root.exists():
        return None
    candidates: list[Path] = []
    for p in period_runs_root.iterdir():
        if not p.is_dir() or p.name == current_run_id:
            continue
        manifest_path = p / "pipeline_manifest.json"
        if not manifest_path.exists() or not (p / "artifact_manifest.json").exists():
            continue
        try:
            manifest = _load_json(manifest_path)
        except Exception:  # noqa: BLE001
            continue
        if manifest.get("status") != "success":
            continue
        candidates.append(p)
    if not candidates:
        return None
    return max(candidates, key=lambda p: p.stat().st_mtime)


def run(config_path: Path, args: argparse.Namespace) -> int:
    started_at = datetime.now().isoformat()
    try:
        config = load_yaml(config_path)
    except Exception as exc:  # noqa: BLE001
        print(f"ERROR: 配置读取失败: {exc}", file=sys.stderr)
        return 1
    root = config_path.resolve().parent.parent
    mode = args.mode
    try:
        period = _resolve_period(config, args)
    except PeriodError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    run_id = uuid.uuid4().hex
    pipeline_runs_root = root / (config.get("pipeline", {}) or {}).get(
        "pipeline_runs_root", "data/reports/tainan_2026/pipeline_runs"
    )
    output_base = root
    if args.output_root:
        pipeline_runs_root = Path(args.output_root) / "pipeline_runs"
        output_base = Path(args.output_root)
    run_dir = create_run_dir(
        pipeline_runs_root,
        period.period_start.isoformat(),
        period.period_end.isoformat(),
        run_id,
    )
    logger = setup_pipeline_logger(run_dir)

    provider = _resolve_provider(config, args)
    delivery_provider = _resolve_delivery_provider(config, args)

    # ---- 生产模式硬性规则 ----
    if mode == "production":
        if provider == "mock":
            print("ERROR: production 不得使用 Mock Provider", file=sys.stderr)
            return 1
        if delivery_provider == "mock":
            print("ERROR: production 不得使用 Mock delivery", file=sys.stderr)
            return 1
        if args.allow_draft_with_gap:
            print("ERROR: production 不得使用 --allow-draft-with-gap", file=sys.stderr)
            return 1
        if args.skip_delivery:
            print("ERROR: production 不得使用 --skip-delivery", file=sys.stderr)
            return 1

    # ---- 单实例锁 ----
    if args.output_root:
        lock_dir = Path(args.output_root) / "locks"
    else:
        lock_dir = root / (config.get("pipeline", {}) or {}).get("lock_dir", "data/locks")
    lock = PipelineLock(
        lock_dir,
        election_id=config["election"]["election_id"],
        period_start=period.period_start.isoformat(),
        period_end=period.period_end.isoformat(),
        mode=mode,
        stale_after_seconds=int((config.get("pipeline", {}) or {}).get("stale_lock_seconds", 3600)),
    )
    if not lock.acquire():
        msg = "单实例锁被占用：同周期同模式已有运行"
        evidence_dir_for_failure = (
            root
            / (config.get("paths") or {}).get(
                "output_root", "data/reports/tainan_2026/evidence_packages"
            )
            / f"{period.period_start.isoformat()}_{period.period_end.isoformat()}"
        )
        dc = _resolve_failure_data_context(config, root, period, evidence_dir_for_failure)
        write_failure_summary(
            run_dir,
            failed_stage="lock",
            error_category="lock_conflict",
            error_message=msg,
            election_id=dc["election_id"],
            period_start=period.period_start.isoformat(),
            period_end=period.period_end.isoformat(),
            facts_cutoff=dc["facts_cutoff"],
            poll_cutoff=dc["poll_cutoff"],
            active_snapshot_id=dc["active_snapshot_id"],
            coverage_version=dc["coverage_version"],
            suggested_actions=["等待现有运行结束，或检查陈旧锁"],
            data_context_resolution_error=dc["data_context_resolution_error"],
        )
        print(f"ERROR: {msg}", file=sys.stderr)
        return 2

    def fail(
        stage: str,
        category: str,
        message: str,
        local_draft_generated: bool = False,
        alert: bool = True,
    ) -> int:
        evidence_dir_for_failure = (
            root
            / (config.get("paths") or {}).get(
                "output_root", "data/reports/tainan_2026/evidence_packages"
            )
            / f"{period.period_start.isoformat()}_{period.period_end.isoformat()}"
        )
        dc = _resolve_failure_data_context(config, root, period, evidence_dir_for_failure)
        failure = {
            "failed_stage": stage,
            "error_category": category,
            "error_message": message,
            "election_id": dc["election_id"],
            "period_start": dc["period_start"],
            "period_end": dc["period_end"],
            "facts_cutoff": dc["facts_cutoff"],
            "poll_cutoff": dc["poll_cutoff"],
            "active_snapshot_id": dc["active_snapshot_id"],
            "coverage_version": dc["coverage_version"],
            "local_draft_generated": local_draft_generated,
            "artifact_generated": bool(list(run_dir.glob("*.docx"))),
            "delivery_attempted": (
                (run_dir / "delivery_validation.json").exists()
                or (run_dir / "delivery_receipt.json").exists()
            ),
            "log_filename": "pipeline.log",
            "suggested_actions": [
                "查看 failure_summary.json 与 pipeline.log 后重试"
            ],
            "data_context_resolution_error": dc["data_context_resolution_error"],
        }
        failure_path = write_failure_summary(
            run_dir,
            failed_stage=stage,
            error_category=category,
            error_message=message,
            election_id=dc["election_id"],
            period_start=period.period_start.isoformat(),
            period_end=period.period_end.isoformat(),
            facts_cutoff=dc["facts_cutoff"],
            poll_cutoff=dc["poll_cutoff"],
            active_snapshot_id=dc["active_snapshot_id"],
            coverage_version=dc["coverage_version"],
            local_draft_generated=local_draft_generated,
            artifact_generated=bool(list(run_dir.glob("*.docx"))),
            delivery_attempted=(
                (run_dir / "delivery_validation.json").exists()
                or (run_dir / "delivery_receipt.json").exists()
            ),
            log_filename="pipeline.log",
            suggested_actions=failure["suggested_actions"],
            data_context_resolution_error=dc["data_context_resolution_error"],
        )
        alert_status = _send_failure_alert(mode, config, run_dir, failure) if alert else "not_attempted"
        data = _load_json(failure_path)
        data["alert_status"] = alert_status
        atomic_write_json(failure_path, data)
        manifest_path = run_dir / "pipeline_manifest.json"
        if not manifest_path.exists():
            stages = []
            stage_path = run_dir / "stage_results.json"
            if stage_path.exists():
                stages = _load_json(stage_path).get("stages", [])
            pipeline_status = "blocked" if mode == "production" else "failed"
            atomic_write_json(
                manifest_path,
                build_pipeline_manifest(
                    run_id=run_id,
                    mode=mode,
                    election_id=config["election"]["election_id"],
                    period_start=period.period_start.isoformat(),
                    period_end=period.period_end.isoformat(),
                    status=pipeline_status,
                    stages=stages,
                    provider=provider,
                    model="",
                    delivery_provider=delivery_provider,
                    generation_mode="",
                    report_status="",
                    artifact_status="not_attempted",
                    delivery_status="not_attempted",
                    production_llm_ready=False,
                    delivery_preflight_ready=False,
                    formal_inputs_unchanged=True,
                    started_at=started_at,
                    finished_at=datetime.now().isoformat(),
                ),
            )
        logger.error("[%s] %s: %s", stage, category, message)
        print(f"pipeline_status={'blocked' if mode == 'production' else 'failed'}", file=sys.stderr)
        print(f"ERROR: {message}", file=sys.stderr)
        return 1

    try:
        # ---- 部署前置检查 ----
        preflight = build_deployment_preflight(
            mode,
            config=config,
            root=root,
            as_of=period.run_date if period.resolution_mode == "scheduled" else None,
            period_start=period.period_start,
            period_end=period.period_end,
        )
        if args.validate_only or mode in ("development", "dry_run", "production"):
            write_preflight_files(root, {mode: preflight}, target_root=output_base)
        append_stage_result(
            run_dir, "deployment_preflight", "passed" if preflight["preflight_ready"] else "failed",
            payload={"preflight_ready": preflight["preflight_ready"], "level": mode},
            error=None if preflight["preflight_ready"] else "; ".join(preflight["errors"]),
        )
        if not preflight["preflight_ready"]:
            if mode == "production":
                return fail(
                    "deployment_preflight",
                    "production_preflight_blocked",
                    "生产前置检查未通过：" + "；".join(preflight["errors"]),
                )
            return fail("deployment_preflight", "preflight_failed", "；".join(preflight["errors"]))

        evidence_out_root = root / (config.get("paths") or {}).get(
            "output_root", "data/reports/tainan_2026/evidence_packages"
        )
        if args.output_root:
            evidence_out_root = Path(args.output_root) / "evidence_packages"
        build_out_root = evidence_out_root if args.output_root else None
        evidence_dir = evidence_out_root / f"{period.period_start.isoformat()}_{period.period_end.isoformat()}"
        formal = load_formal_data(config, root, config["election"]["election_id"])
        coverage_path = formal.coverage_dir
        before_hashes = compute_input_hashes(config, root, coverage_path)

        if args.validate_only:
            code = build_evidence_pack_run(
                config_path=config_path,
                election_id=config["election"]["election_id"],
                as_of=args.as_of,
                period_start=args.period_start,
                period_end=args.period_end,
                output_root=build_out_root,
                validate_only=True,
                force_rebuild=False,
            )
            if code != 0:
                return fail("evidence_pack_validate", "evidence_pack_failed", "证据包验证失败")
            gen_root = root / (config.get("report_generation", {}) or {}).get(
                "output_root", "data/reports/tainan_2026/generated_reports"
            )
            code = generate_report_run(
                config_path=config_path,
                evidence_dir=evidence_dir,
                provider=provider,
                model=args.model,
                allow_draft_with_gap=args.allow_draft_with_gap,
                validate_only=True,
                force_model_call=args.force_model_call,
                no_repair=False,
                output_root=gen_root,
                deepseek_thinking="disabled",
            )
            if code != 0:
                return fail("report_generation_validate", "report_generation_failed", "报告生成验证失败")
            append_stage_result(run_dir, "validate_only", "passed")
            logger.info("validate-only 通过")
            return 0

        # ---- 证据包 ----
        append_stage_result(run_dir, "build_evidence_pack", "running")
        code = build_evidence_pack_run(
            config_path=config_path,
            election_id=config["election"]["election_id"],
            as_of=args.as_of,
            period_start=args.period_start,
            period_end=args.period_end,
            output_root=build_out_root,
            validate_only=False,
            force_rebuild=args.force_evidence_rebuild,
        )
        if code != 0:
            return fail("build_evidence_pack", "evidence_pack_failed", "证据包构建或验证失败")
        append_stage_result(run_dir, "build_evidence_pack", "passed")

        pack = _load_json(evidence_dir / "report_evidence_pack.json")
        evidence_validation = _load_json(evidence_dir / "evidence_pack_validation.json")
        contract_validation = _load_json(evidence_dir / "llm_input_contract_validation.json")
        coverage_path = _coverage_path(config, root, evidence_dir)
        if evidence_validation.get("evidence_pack_ready") is not True:
            return fail("evidence_pack_validate", "evidence_pack_failed", "证据包校验未通过")
        if contract_validation.get("llm_input_contract_ready") is not True:
            return fail("llm_input_contract", "contract_failed", "LLM 输入合同校验未通过")
        eligibility = pack.get("generation_eligibility") or {}
        if eligibility.get("final_report_allowed") is not True and not args.allow_draft_with_gap:
            return fail(
                "generation_eligibility",
                "draft_requires_permission",
                "final_report_allowed=false；必须显式 --allow-draft-with-gap",
            )

        # ---- 结构化报告 ----
        append_stage_result(run_dir, "report_generation", "running")
        gen_root = root / (config.get("report_generation", {}) or {}).get(
            "output_root", "data/reports/tainan_2026/generated_reports"
        )
        if args.output_root:
            gen_root = Path(args.output_root) / "generated_reports"
        code = generate_report_run(
            config_path=config_path,
            evidence_dir=evidence_dir,
            provider=provider,
            model=args.model,
            allow_draft_with_gap=args.allow_draft_with_gap,
            validate_only=False,
            force_model_call=args.force_model_call,
            no_repair=False,
            output_root=gen_root,
            deepseek_thinking="disabled",
        )
        if code != 0:
            return fail("report_generation", "report_generation_failed", "结构化报告生成失败")
        append_stage_result(run_dir, "report_generation", "passed")

        generated_dir = gen_root / f"{period.period_start.isoformat()}_{period.period_end.isoformat()}"
        structured_report = _load_json(generated_dir / "structured_report_final.json")
        generation_validation = _load_json(generated_dir / "report_generation_validation.json")
        generation_manifest = _load_json(generated_dir / "report_generation_manifest.json")
        attempt1 = _load_json(generated_dir / "claim_evidence_validation_attempt_1.json")
        attempt2_path = generated_dir / "claim_evidence_validation_attempt_2.json"
        claim_validation = _load_json(attempt2_path) if attempt2_path.exists() else attempt1
        for name, obj in (
            ("structured_report_final.json", structured_report),
            ("claim_evidence_validation.json", claim_validation),
            ("report_generation_manifest.json", generation_manifest),
            ("report_generation_validation.json", generation_validation),
        ):
            atomic_write_json(run_dir / name, obj)
        md_path = generated_dir / "report_draft.md"
        if md_path.exists():
            (run_dir / "report_draft.md").write_text(md_path.read_text(encoding="utf-8"), encoding="utf-8")

        if generation_validation.get("report_generation_ready") is not True:
            return fail("claim_evidence_validate", "claim_evidence_failed", "Claim—Evidence 校验未通过")
        if structured_report.get("report_status") == "rejected":
            return fail("claim_evidence_validate", "report_rejected", "报告被拒绝")

        # ---- Word ----
        append_stage_result(run_dir, "word_render", "running")
        render_info = render_word_report(
            structured_report,
            output_dir=run_dir,
            mode=mode,
            generation_validation=generation_validation,
            manifest=generation_manifest,
        )
        artifact_validation = validate_report_artifact(
            structured_report,
            Path(render_info["docx_path"]),
            expected_mode=structured_report.get("generation_mode"),
            generation_validation=generation_validation,
        )
        _write_artifact_manifest(run_dir, render_info, artifact_validation)
        append_stage_result(
            run_dir,
            "word_render",
            "passed" if artifact_validation["artifact_ready"] else "failed",
            payload={
                "docx": _relative_to_root(render_info["docx_path"]),
                "artifact_ready": artifact_validation["artifact_ready"],
            },
            error=None if artifact_validation["artifact_ready"] else "; ".join(artifact_validation["errors"]),
        )
        if not artifact_validation["artifact_ready"]:
            return fail("word_render", "artifact_invalid", "Word 产物校验未通过")

        # ---- 交付 ----
        summary_text = _build_summary_text(
            structured_report,
            pack,
            render_info["filename"],
            mode,
        )
        if args.skip_delivery:
            _write_delivery_validation(run_dir, success=True, errors=[], warnings=["delivery skipped"], network_calls=0, skipped=True)
            delivery_status = "skipped"
            append_stage_result(run_dir, "delivery", "skipped")
        else:
            append_stage_result(run_dir, "delivery", "running")
            fixture = (config.get("delivery") or {}).get("mock", {}).get("fixture", "success")
            if args.delivery_fixture:
                fixture = args.delivery_fixture
            delivery = create_delivery(
                delivery_provider,
                config=config,
                mode=mode,
                fixture=fixture,
            )
            try:
                delivery_result = delivery.deliver(
                    report_metadata={
                        "title": structured_report.get("title"),
                        "period": f"{period.period_start.isoformat()}_{period.period_end.isoformat()}",
                        "report_status": structured_report.get("report_status"),
                        "generation_mode": structured_report.get("generation_mode"),
                    },
                    summary_text=summary_text,
                    artifact_paths=[render_info["docx_path"]],
                    delivery_context={"receipt_path": str(run_dir / "delivery_receipt.json")},
                )
            except DeliveryError as exc:
                _write_delivery_validation(
                    run_dir,
                    success=False,
                    errors=[f"{type(exc).__name__}: {exc}"],
                    warnings=[],
                    network_calls=0,
                )
                return fail("delivery", "delivery_failed", str(exc))
            _write_delivery_receipt(run_dir, delivery_result)
            _write_delivery_validation(
                run_dir,
                success=delivery_result.success,
                errors=[] if delivery_result.success else delivery_result.warnings,
                warnings=delivery_result.warnings,
                network_calls=delivery_result.network_calls,
            )
            append_stage_result(
                run_dir,
                "delivery",
                "passed" if delivery_result.success else "failed",
                payload={"delivery_mode": delivery_result.delivery_mode, "network_calls": delivery_result.network_calls},
                error=None if delivery_result.success else "; ".join(delivery_result.warnings),
            )
            if delivery_result.delivery_mode == "disabled_by_configuration":
                delivery_status = "disabled_by_configuration"
            else:
                delivery_status = "delivered" if delivery_result.success else "failed"
            if not delivery_result.success:
                return fail("delivery", "delivery_failed", "；".join(delivery_result.warnings))

        # ---- 幂等与保护 ----
        after_hashes = compute_input_hashes(config, root, coverage_path)
        formal_unchanged = before_hashes == after_hashes
        report_hash = _report_business_hash(structured_report)
        word_signature = word_text_signature(Path(render_info["docx_path"]))
        previous = _previous_run_dir(run_dir.parent, run_id)
        prev_report_hash = None
        prev_word_signature = None
        if previous is not None:
            prev_artifact = _load_json(previous / "artifact_manifest.json")
            prev_report_hash = prev_artifact.get("report_business_hash")
            prev_word_signature = prev_artifact.get("word_signature")
            if not prev_report_hash:
                prev_report_path = previous / "structured_report_final.json"
                if prev_report_path.exists():
                    prev_report_hash = _report_business_hash(_load_json(prev_report_path))
            if not prev_word_signature:
                prev_docx = Path(prev_artifact.get("docx_path") or "")
                if prev_docx.exists():
                    prev_word_signature = word_text_signature(prev_docx)
        report_equal = prev_report_hash is None or prev_report_hash == report_hash
        word_equal = prev_word_signature is None or prev_word_signature == word_signature
        idempotency = {
            "first_run_business_hash": prev_report_hash or report_hash,
            "second_run_business_hash": report_hash,
            "report_business_equal": report_equal,
            "word_business_equal": word_equal,
            "word_signature": word_signature,
            "formal_inputs_unchanged": formal_unchanged,
            "idempotent": report_equal and word_equal and formal_unchanged,
        }
        atomic_write_json(run_dir / "pipeline_idempotency.json", idempotency)
        artifact_manifest = _load_json(run_dir / "artifact_manifest.json")
        artifact_manifest["report_business_hash"] = report_hash
        artifact_manifest["word_signature"] = word_signature
        atomic_write_json(run_dir / "artifact_manifest.json", artifact_manifest)

        if not formal_unchanged:
            return fail("formal_data_protection", "formal_data_changed", "正式输入哈希发生变化")

        # ---- manifest / validation ----
        stages = _load_json(run_dir / "stage_results.json")["stages"]
        manifest = build_pipeline_manifest(
            run_id=run_id,
            mode=mode,
            election_id=config["election"]["election_id"],
            period_start=period.period_start.isoformat(),
            period_end=period.period_end.isoformat(),
            status="success",
            stages=stages,
            provider=provider,
            model=generation_manifest.get("model") or "",
            delivery_provider=delivery_provider,
            generation_mode=structured_report.get("generation_mode") or "",
            report_status=structured_report.get("report_status") or "",
            artifact_status="ready" if artifact_validation["artifact_ready"] else "failed",
            delivery_status=delivery_status,
            production_llm_ready=preflight.get("production_llm_ready") is True,
            delivery_preflight_ready=preflight.get("preflight_ready") is True,
            formal_inputs_unchanged=formal_unchanged,
            started_at=started_at,
            finished_at=datetime.now().isoformat(),
        )
        atomic_write_json(run_dir / "pipeline_manifest.json", manifest)
        pipeline_validation = build_pipeline_validation(
            pipeline_ready=True,
            errors=[],
            warnings=[],
            deployment_preflight_ready=preflight["preflight_ready"],
            evidence_pack_ready=evidence_validation.get("evidence_pack_ready") is True,
            llm_input_contract_ready=contract_validation.get("llm_input_contract_ready") is True,
            report_generation_ready=generation_validation.get("report_generation_ready") is True,
            artifact_ready=artifact_validation["artifact_ready"],
            delivery_success=delivery_status != "failed",
            formal_inputs_unchanged=formal_unchanged,
            production_mode_allowed=mode != "production" or preflight.get("production_llm_ready") is True,
            network_calls=(
                _load_json(run_dir / "delivery_validation.json").get("network_calls", 0)
                if (run_dir / "delivery_validation.json").exists()
                else 0
            ),
        )
        atomic_write_json(run_dir / "pipeline_validation.json", pipeline_validation)
        write_latest(pipeline_runs_root, run_dir, manifest)

        logger.info(
            "pipeline success mode=%s status=%s generation_mode=%s artifact=%s delivery=%s",
            mode,
            manifest["status"],
            manifest["generation_mode"],
            manifest["artifact_status"],
            manifest["delivery_status"],
        )
        print(f"pipeline_status={manifest['status']}")
        print(f"generation_mode={manifest['generation_mode']}")
        print(f"report_status={manifest['report_status']}")
        print(f"artifact_ready={artifact_validation['artifact_ready']}")
        print(f"delivery_status={manifest['delivery_status']}")
        print(f"formal_inputs_unchanged={formal_unchanged}")
        print(f"run_dir={run_dir}")
        return 0
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc(file=sys.stderr)
        return fail("pipeline", type(exc).__name__, str(exc))
    finally:
        lock.release()


def main() -> int:
    parser = argparse.ArgumentParser(description="台南选情半月研判总编排器")
    parser.add_argument("--config", default="config/election_assessment.yaml")
    parser.add_argument("--mode", choices=["development", "dry_run", "production"], default="development")
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    parser.add_argument("--period-start", type=date.fromisoformat, default=None)
    parser.add_argument("--period-end", type=date.fromisoformat, default=None)
    parser.add_argument("--provider", default=None)
    parser.add_argument("--model", default=None)
    parser.add_argument("--delivery-provider", default=None)
    parser.add_argument("--delivery-fixture", default=None)
    parser.add_argument("--allow-draft-with-gap", action="store_true")
    parser.add_argument("--force-evidence-rebuild", action="store_true")
    parser.add_argument("--force-model-call", action="store_true")
    parser.add_argument("--skip-delivery", action="store_true")
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--output-root", type=Path, default=None)
    args = parser.parse_args()

    if args.as_of is not None and (args.period_start is not None or args.period_end is not None):
        print("ERROR: --as-of 与显式周期不得同时使用", file=sys.stderr)
        return 1
    if (args.period_start is None) != (args.period_end is None):
        print("ERROR: 显式周期必须同时提供 --period-start 和 --period-end", file=sys.stderr)
        return 1
    if args.delivery_provider and args.delivery_provider not in ("mock", "feishu"):
        print(f"ERROR: 未注册 delivery provider: {args.delivery_provider}", file=sys.stderr)
        return 1
    if args.provider and args.provider not in REGISTERED_PROVIDERS:
        print(f"ERROR: 未注册 provider: {args.provider}", file=sys.stderr)
        return 1
    return run(Path(args.config), args)


if __name__ == "__main__":
    sys.exit(main())
