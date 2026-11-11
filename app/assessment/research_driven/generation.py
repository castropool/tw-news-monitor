"""research-driven 研判生成编排（生产路径 V1）。

流程：Period Gate -> Research Pack -> LLM 分析（变化/判断/因果/权力/趋势）
-> 最终文章 -> Fact Safety Check -> Word -> ready_for_review。

只读正式事实；写入 Assessment 运营数据（Research Pack/文章/Word/run 元数据）。
"""

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

from app.assessment.evidence_pack_builder import select_coverage_version
from app.assessment.pipeline_lock import PipelineLock
from app.assessment.r2.period import period_for_run_date, report_run_key
from app.assessment.r2.state import ReportRunStore, new_run_record
from app.assessment.reporting_period import previous_period_for
from app.time_utils import TAIPEI

from . import IDEMPOTENT_SKIP_STATUSES
from .adapter import AssessmentLLMAdapter
from .fact_safety import run_fact_safety_check
from .prompt import build_system_prompt, build_user_payload, parse_model_output
from .research_pack import ResearchPackContext, build_pack_with_context, render_pack_markdown
from .word_renderer import render_article_word

PRODUCTION_ROOT_REL = Path("data/election_assessment/tainan_2026/production")
DEFAULT_SEED_ROOT_REL = Path("data/election_seed/tainan_2026")
DEFAULT_REPORTS_ROOT_REL = Path("data/reports/tainan_2026")

PREVIEW_FILES = (
    "FINAL_ASSESSMENT_PREVIEW.md",
    "FINAL_ASSESSMENT_PREVIEW.docx",
    "ASSESSMENT_RESEARCH_PACK.md",
)


def default_project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def seed_root_from_config(config: dict, project_root: Path | None = None) -> Path:
    """正式 seed 根目录：优先 config paths.seed_root/coverage_root，缺省台南。"""
    project_root = project_root or default_project_root()
    raw = config.get("paths") or {}
    for key in ("seed_root", "coverage_root"):
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            p = Path(value)
            if not p.is_absolute():
                p = project_root / p
            return p
    return project_root / DEFAULT_SEED_ROOT_REL


def runs_root_from_config(config: dict, project_root: Path | None = None) -> Path | None:
    """Assessment 生产 runs 根目录：读 config paths.assessment_runs_root，缺省 None
    （调用方回落 PRODUCTION_ROOT_REL 台南默认）。"""
    project_root = project_root or default_project_root()
    value = (config.get("paths") or {}).get("assessment_runs_root")
    if isinstance(value, str) and value.strip():
        p = Path(value)
        if not p.is_absolute():
            p = project_root / p
        return p
    return None


def _election_ctx(config: dict) -> dict:
    """从 config 提取 prompt/word/fact-safety 的领域化上下文（缺省台南）。"""
    el = config.get("election") or {}
    display_name = str(el.get("display_name") or "台南市长选举")
    region = str(el.get("region") or el.get("region_term") or "台南")
    report_label = str(el.get("report_label") or "台南市长选情")
    return {
        "election_label": report_label,
        "region": region,
        "system_name": str(el.get("system_name") or "台南选情智能研判系统"),
        "file_prefix": str(el.get("file_prefix") or "台南选情研判"),
    }


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _atomic_write_json(path: Path, obj: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(path)


def load_config(config_path: Path) -> dict:
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def _copy_seed_selectively(src_dir: Path, dst_dir: Path) -> None:
    """选择性复制正式 seed：只复制研究包构建所需内容（根文件 + fact_coverage_*）。

    避免 Windows 长路径问题，也避免把历史预览/导入中间产物带进运行目录。
    """
    dst_dir.mkdir(parents=True, exist_ok=True)
    for child in src_dir.iterdir():
        if child.is_dir():
            if child.name.startswith("fact_coverage_"):
                shutil.copytree(child, dst_dir / child.name)
            continue
        shutil.copy2(child, dst_dir / child.name)


def _freeze_production_input(
    run_dir: Path,
    project_root: Path,
    seed_root: Path | None = None,
    formal_db_path: Path | None = None,
) -> dict:
    """冻结正式输入（db + 所需 seed 副本），返回 facts_cutoff/poll_cutoff/hash。"""
    input_dir = run_dir / "input"
    input_dir.mkdir(parents=True, exist_ok=True)
    prod_data = project_root / "data"
    prod_seed = seed_root or prod_data / "election_seed" / "tainan_2026"
    prod_db = formal_db_path or prod_data / "election_context.db"
    _coverage_path, coverage_name, coverage_preflight, coverage_validation = (
        select_coverage_version(prod_seed)
    )
    frozen_db = input_dir / "election_context.db"
    frozen_seed = input_dir / "election_seed"
    if frozen_db.exists():
        frozen_db.unlink()
    if frozen_seed.exists():
        shutil.rmtree(frozen_seed)
    src = sqlite3.connect(f"file:{prod_db}?mode=ro", uri=True)
    dst = sqlite3.connect(f"file:{frozen_db}", uri=True)
    try:
        with dst:
            src.backup(dst)
    finally:
        dst.close()
        src.close()
    _copy_seed_selectively(prod_seed, frozen_seed)

    frozen_preflight_path = frozen_seed / coverage_name / "coverage_preflight.json"
    if not frozen_preflight_path.exists():
        raise RuntimeError(f"冻结输入缺少 coverage preflight: {frozen_preflight_path}")
    frozen_preflight = json.loads(frozen_preflight_path.read_text(encoding="utf-8"))
    if not isinstance(frozen_preflight, dict):
        raise RuntimeError(f"coverage preflight 解析失败: {frozen_preflight_path}")

    from app.election_context.formal_state_hash import (
        formal_state_business_hash_from_db,
        formal_state_business_hash_from_seed_dir,
    )

    return {
        "input_hash": formal_state_business_hash_from_db(frozen_db),
        "seed_hash": formal_state_business_hash_from_seed_dir(frozen_seed),
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


def _write_run_config(
    run_dir: Path,
    project_root: Path,
    seed_root: Path | None = None,
    config_path: Path | None = None,
    config: dict | None = None,
) -> Path:
    """把正式配置改写到 run 内相对路径（input/election_context.db 等）。

    台南/新北通用：以实际 seed/report 根相对 project_root 的串做替换。
    config_path 缺省用台南 workspace config（向后兼容）。
    """
    run_config = run_dir / "input" / "assessment_config.yaml"
    src_config = config_path or project_root / "config" / "election_assessment.yaml"
    text = src_config.read_text(encoding="utf-8")
    seed_rel = (
        seed_root.resolve().relative_to(project_root.resolve())
        if seed_root is not None
        else DEFAULT_SEED_ROOT_REL
    )
    cfg = config or {}
    paths_cfg = cfg.get("paths") or {}
    out_root = paths_cfg.get("output_root") or ""
    if out_root:
        reports_rel = Path(str(out_root).replace("\\", "/")).parent
    else:
        reports_rel = DEFAULT_REPORTS_ROOT_REL
    # database 路径：台南 data/election_context.db 或新北 data/election_context.new_taipei.db
    # 统一改写为 run 内 input/election_context.db（load_formal_data 以 run root 解析）。
    db_raw = str(paths_cfg.get("database") or "data/election_context.db").replace("\\", "/")
    if not db_raw.startswith("data/"):
        db_raw = "data/" + db_raw.lstrip("/")
    text = text.replace(db_raw, "input/election_context.db")
    text = text.replace(str(seed_rel).replace("\\", "/"), "input/election_seed")
    text = text.replace(
        str(reports_rel).replace("\\", "/") + "/evidence_packages",
        "work/evidence_packages",
    )
    text = text.replace(
        str(reports_rel).replace("\\", "/") + "/generated_reports",
        "work/generated_reports",
    )
    text = text.replace("data/locks", "work/locks")
    run_config.parent.mkdir(parents=True, exist_ok=True)
    run_config.write_text(text, encoding="utf-8")
    return run_config


def _load_previous_period_report(
    store: ReportRunStore,
    election_id: str,
    period_start: date,
    period_end: date,
) -> tuple[dict | None, str | None]:
    """读取上一周期的正式生产报告（不存在时返回 None → 用状态基线）。"""
    try:
        prev_start, prev_end = previous_period_for(period_start, period_end)
    except Exception:  # noqa: BLE001
        return None, None
    run_key = report_run_key(election_id, prev_start, prev_end)
    prev_run = store.get(run_key)
    if not prev_run:
        return None, None
    prev_dir = store.root / "periods" / f"{prev_start:%Y%m%d}_{prev_end:%Y%m%d}"
    analysis_plan: dict | None = None
    article: str | None = None
    plan_path = prev_dir / "analysis_plan.json"
    if plan_path.exists():
        try:
            analysis_plan = json.loads(plan_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001
            analysis_plan = None
    article_path = prev_dir / "final_article.md"
    if article_path.exists():
        article = article_path.read_text(encoding="utf-8")
    prev_report = dict(prev_run)
    if analysis_plan:
        prev_report["analysis_plan"] = analysis_plan
    return prev_report, article


def _copy_previews(period_dir: Path, production_root: Path) -> None:
    production_root.mkdir(parents=True, exist_ok=True)
    mapping = {
        "final_article.md": "FINAL_ASSESSMENT_PREVIEW.md",
        "final_article.docx": "FINAL_ASSESSMENT_PREVIEW.docx",
        "ASSESSMENT_RESEARCH_PACK.md": "ASSESSMENT_RESEARCH_PACK.md",
    }
    for src_name, dst_name in mapping.items():
        src = period_dir / src_name
        if src.exists():
            shutil.copy2(src, production_root / dst_name)


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
    provider: str | None = None,
    model: str | None = None,
    mock_fixture: str | None = None,
    lock_dir: Path | None = None,
) -> dict:
    project_root = project_root or default_project_root()
    config = load_config(config_path)
    election_id = config["election"]["election_id"]
    seed_root = seed_root_from_config(config, project_root)
    ectx = _election_ctx(config)
    db_value = (config.get("paths") or {}).get("database") or "data/election_context.db"
    formal_db_path = Path(db_value)
    if not formal_db_path.is_absolute():
        formal_db_path = project_root / formal_db_path
    if period_start is None or period_end is None:
        if as_of.day not in (9, 22):
            raise ValueError("非调度日必须显式提供 --period-start/--period-end")
        period_start, period_end = period_for_run_date(as_of)
    run_key = report_run_key(election_id, period_start, period_end)
    run_id = f"rd_{period_start.strftime('%Y%m%d')}_{period_end.strftime('%Y%m%d')}_{uuid.uuid4().hex[:8]}"
    store = ReportRunStore(runs_root)
    if lock_dir is None:
        lock_dir = Path(config.get("pipeline", {}).get("lock_dir", "data/locks"))
        if not lock_dir.is_absolute():
            lock_dir = config_path.resolve().parent.parent / lock_dir
    else:
        lock_dir = Path(lock_dir)

    existing = store.get(run_key)
    if existing and not force_regenerate and existing.get("generation_status") in (
        IDEMPOTENT_SKIP_STATUSES + ("generated",)
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
    run["generation_mode"] = "research_driven"
    run["model"] = model or ""
    run["mock_fixture"] = mock_fixture or ""
    if force_regenerate and existing:
        run["started_at"] = datetime.now(TAIPEI).isoformat()
        run["completed_at"] = ""
        run["generation_status"] = "running"
        run["error"] = ""
    store.save(run)

    lock = PipelineLock(
        lock_dir,
        election_id=election_id,
        period_start=period_start.isoformat(),
        period_end=period_end.isoformat(),
        mode="research_driven_generation",
    )
    if not lock.acquire():
        run["generation_status"] = "skipped_locked"
        store.save(run)
        return {"code": "SKIPPED_LOCKED", "run_id": run["run_id"], "run_key": run_key}

    period_label = f"{period_start:%Y%m%d}_{period_end:%Y%m%d}"
    try:
        period_dir = runs_root / "periods" / period_label
        frozen = _freeze_production_input(
            period_dir, project_root, seed_root=seed_root, formal_db_path=formal_db_path
        )
        run["input_hash"] = frozen["input_hash"]
        run["facts_cutoff"] = frozen["facts_cutoff"] or ""
        run["poll_cutoff"] = frozen["poll_cutoff"] or ""
        run["coverage_version"] = frozen["coverage_version"]

        # Period Gate：facts_cutoff 必须覆盖 period_end
        if not (frozen["facts_cutoff"] and frozen["facts_cutoff"] >= period_end.isoformat()):
            run["generation_status"] = "period_not_ready"
            run["blocking_issues"] = [
                f"facts_cutoff={frozen['facts_cutoff']} < period_end={period_end.isoformat()}"
            ]
            run["completed_at"] = datetime.now(TAIPEI).isoformat()
            store.save(run)
            return {
                "code": "REPORT_PERIOD_NOT_READY",
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

        # 1) Research Pack（基于冻结正式输入）
        run_config = _write_run_config(
            period_dir, project_root, seed_root=seed_root,
            config_path=config_path, config=config,
        )
        run_root_for_pack = period_dir
        prev_report, prev_article = _load_previous_period_report(
            store, election_id, period_start, period_end
        )
        ctx = ResearchPackContext(
            period_start=period_start,
            period_end=period_end,
            previous_period_start=(
                date.fromisoformat(prev_report["period_start"]) if prev_report else None
            ),
            previous_period_end=(
                date.fromisoformat(prev_report["period_end"]) if prev_report else None
            ),
            previous_period_report=prev_report,
            previous_period_article=prev_article,
        )
        pack = build_pack_with_context(
            load_config(run_config), run_root_for_pack, election_id, ctx
        )
        pack_md = render_pack_markdown(pack)
        _atomic_write_json(period_dir / "research_pack.json", pack)
        (period_dir / "ASSESSMENT_RESEARCH_PACK.md").write_text(pack_md, encoding="utf-8")
        input_manifest = {
            "run_id": run["run_id"],
            "run_key": run_key,
            "period": pack["period"],
            "frozen": frozen,
            "created_at": datetime.now(TAIPEI).isoformat(),
        }
        _atomic_write_json(period_dir / "input_manifest.json", input_manifest)
        run["research_pack_hash"] = sha256_file(period_dir / "research_pack.json")
        run["generation_status"] = "research_pack_ready"
        store.save(run)

        # 2) LLM 生成（分析计划 + 最终文章）
        run["generation_status"] = "generating"
        store.save(run)
        adapter_cfg = load_config(run_config)
        adapter = AssessmentLLMAdapter(
            adapter_cfg,
            provider=provider or ("mock" if mock_fixture else None),
            model=model,
        )
        payload = build_user_payload(pack, prev_article, election_label=ectx["election_label"])
        if mock_fixture:
            payload["_mock_fixture"] = mock_fixture
        llm_audit: dict[str, Any] = {}
        try:
            result = adapter.complete(
                system_prompt=build_system_prompt(
                    election_label=ectx["election_label"],
                    region=ectx["region"],
                ),
                user_payload=payload,
                json_mode=True,
            )
            run["model"] = result.model
            llm_audit = {
                "provider": result.provider,
                "model": result.model,
                "client_request_id": result.client_request_id,
                "response_id": result.response_id,
                "input_tokens": result.input_tokens,
                "output_tokens": result.output_tokens,
                "total_tokens": result.total_tokens,
                "finish_status": result.finish_status,
                "request_duration_ms": result.request_duration_ms,
                "warnings": result.warnings,
            }
            analysis_plan, final_article = parse_model_output(result.structured or {})
        except Exception as exc:  # noqa: BLE001
            run["generation_status"] = "generation_failed"
            run["error"] = str(exc)
            run["article_generation_failed"] = True
            run["research_pack_ready"] = True
            run["completed_at"] = datetime.now(TAIPEI).isoformat()
            run["llm_audit"] = llm_audit
            store.save(run)
            return {
                "code": "GENERATION_FAILED",
                "run_id": run["run_id"],
                "run_key": run_key,
                "error": str(exc),
                "research_pack_ready": True,
                "article_generation_failed": True,
            }
        _atomic_write_json(period_dir / "analysis_plan.json", analysis_plan)
        article_md = f"# {final_article['title']}\n\n{final_article['body'].strip()}\n"
        (period_dir / "final_article.md").write_text(article_md, encoding="utf-8")
        run["article_hash"] = sha256_file(period_dir / "final_article.md")
        run["generation_status"] = "generated"
        run["llm_audit"] = llm_audit
        store.save(run)

        # 3) Fact Safety Check
        audit = run_fact_safety_check(
            final_article["body"],
            final_article["title"],
            pack,
            period_end.isoformat(),
            region_terms=(ectx["region"], f"{ectx['region']}市") if ectx["region"] else None,
        )
        audit["checked_at"] = datetime.now(TAIPEI).isoformat()
        _atomic_write_json(period_dir / "fact_safety_audit.json", audit)
        review_notes = list(audit.get("review_notes") or [])
        _atomic_write_json(period_dir / "review_notes.json", review_notes)
        run["fact_safety_status"] = audit["status"]
        run["review_notes"] = review_notes
        run["blocking_issues"] = list(audit.get("hard_block_reasons") or [])
        if audit["status"] == "hard_block":
            run["generation_status"] = "machine_rejected"
            run["completed_at"] = datetime.now(TAIPEI).isoformat()
            store.save(run)
            return {
                "code": "MACHINE_HARD_BLOCKED",
                "run_id": run["run_id"],
                "run_key": run_key,
                "hard_block_reasons": audit["hard_block_reasons"],
                "review_notes": review_notes,
            }

        # 4) Word
        try:
            word_info = render_article_word(
                title=final_article["title"],
                body_markdown=final_article["body"],
                output_dir=period_dir,
                period_start=period_start.isoformat(),
                period_end=period_end.isoformat(),
                facts_cutoff=str(frozen["facts_cutoff"] or ""),
                poll_cutoff=str(frozen["poll_cutoff"] or ""),
                report_id=run["run_id"],
                model=run["model"],
                system_name=ectx["system_name"],
                file_prefix=ectx["file_prefix"],
            )
            word_path = Path(word_info["docx_path"])
            shutil.copy2(word_path, period_dir / "final_article.docx")
            run["word_path"] = str(period_dir / "final_article.docx")
            run["word_hash"] = sha256_file(period_dir / "final_article.docx")
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

        # 5) 预览副本 + run 元数据 + 终态
        _copy_previews(period_dir, runs_root)
        metadata = {
            "run_id": run["run_id"],
            "run_key": run_key,
            "period": pack["period"],
            "facts_cutoff": frozen["facts_cutoff"],
            "poll_cutoff": frozen["poll_cutoff"],
            "model": run["model"],
            "provider": llm_audit.get("provider"),
            "input_hash": run["input_hash"],
            "research_pack_hash": run["research_pack_hash"],
            "article_hash": run["article_hash"],
            "word_hash": run["word_hash"],
            "word_path": run["word_path"],
            "status": "ready_for_review",
            "error": "",
            "created_at": datetime.now(TAIPEI).isoformat(),
        }
        _atomic_write_json(period_dir / "run_metadata.json", metadata)
        run["output_path"] = str(period_dir / "final_article.md")
        run["generation_status"] = "ready_for_review"
        run["human_review_status"] = "awaiting_review"
        run["delivery_status"] = "not_attempted"
        run["completed_at"] = datetime.now(TAIPEI).isoformat()
        store.save(run)
        return {
            "code": "GENERATED_READY_FOR_REVIEW",
            "run_id": run["run_id"],
            "run_key": run_key,
            "word_path": run["word_path"],
            "period_dir": str(period_dir),
            "fact_safety_status": audit["status"],
            "review_notes": review_notes,
        }
    finally:
        lock.release()
