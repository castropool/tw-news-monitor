"""Phase R2 delivery orchestration (approval-required, idempotent, retry-able)."""

from __future__ import annotations

import argparse
import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

import yaml

from app.assessment.delivery import create_delivery
from app.assessment.r2.review import current_report_hash
from app.assessment.r2.security import feishu_gate
from app.assessment.r2.state import ReportRunStore, utcnow_iso


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_config(config_path: Path) -> dict:
    return yaml.safe_load(config_path.read_text(encoding="utf-8"))


def deliver_report(
    *,
    store: ReportRunStore,
    run_key: str,
    config_path: Path,
    provider: str = "feishu",
    mode: str = "production",
) -> dict:
    run = store.get(run_key)
    if not run:
        raise KeyError(f"run_key 不存在: {run_key}")
    if run.get("human_review_status") != "human_approved":
        raise ValueError("DELIVERY_REQUIRES_APPROVAL: 必须先人工批准")
    if run.get("generation_status") != "human_approved":
        raise ValueError(f"当前 generation_status={run.get('generation_status')} 不可交付")

    report_hash = current_report_hash(run)
    if not report_hash or report_hash != run.get("report_hash"):
        raise ValueError("DELIVERY_BLOCKED_REPORT_CHANGED: 报告哈希不一致")
    word_path = Path(run.get("word_path") or "")
    if not word_path.exists():
        raise ValueError("DELIVERY_BLOCKED_WORD_MISSING: Word 文件不存在")
    word_hash = sha256_file(word_path)
    if word_hash != run.get("word_hash"):
        raise ValueError("DELIVERY_BLOCKED_WORD_CHANGED: Word 哈希不一致")

    existing = store.deliveries(run_key)
    for receipt in existing:
        if (
            receipt.get("status") == "delivered"
            and receipt.get("report_hash") == run.get("report_hash")
            and receipt.get("word_hash") == run.get("word_hash")
        ):
            return {
                "status": "already_delivered",
                "delivery_id": receipt.get("delivery_id"),
                "delivery_idempotent": True,
            }

    config = _load_config(config_path)
    gate = feishu_gate(config)
    if provider == "feishu" and not gate["production_delivery_ready"]:
        run["delivery_status"] = "delivery_blocked_credential_rotation"
        store.save(run)
        return {
            "status": "blocked",
            "blocker": gate["blocker"],
            "delivery_blocked": True,
            "production_delivery_ready": False,
        }

    review = store.reviews(run_key)
    approved = next((r for r in reversed(review) if r.get("decision") == "approve"), {})
    delivery_id = f"del_{run_key}_{uuid.uuid4().hex[:8]}"
    receipt = {
        "delivery_id": delivery_id,
        "run_id": run.get("run_id"),
        "run_key": run_key,
        "review_id": approved.get("review_id", ""),
        "channel": provider,
        "report_hash": report_hash,
        "word_hash": word_hash,
        "status": "started",
        "provider_response_id": "",
        "attempted_at": utcnow_iso(),
        "completed_at": "",
        "error": "",
    }
    summary_text = (
        f"台南选情研判报告（{run.get('period_start')} 至 {run.get('period_end')}）"
        "已完成人工终审，现正式交付。详见附件 Word。"
    )
    try:
        delivery = create_delivery(provider, config=config, mode=mode)
        result = delivery.deliver(
            report_metadata={
                "run_id": run.get("run_id"),
                "run_key": run_key,
                "period_start": run.get("period_start"),
                "period_end": run.get("period_end"),
            },
            summary_text=summary_text,
            artifact_paths=[str(word_path)],
            delivery_context={"receipt_path": str(Path(run.get("word_path")).parent / "delivery_receipt.json")},
        )
    except Exception as exc:  # noqa: BLE001
        receipt["status"] = "delivery_failed"
        receipt["error"] = str(exc)
        receipt["completed_at"] = utcnow_iso()
        store.append_delivery(run_key, receipt)
        run["delivery_status"] = "delivery_failed"
        store.save(run)
        return {"status": "delivery_failed", "delivery_id": delivery_id, "error": str(exc)}

    receipt["status"] = "delivered" if result.success else "delivery_failed"
    receipt["provider_response_id"] = result.message_id
    receipt["completed_at"] = result.delivered_at or utcnow_iso()
    receipt["error"] = "" if result.success else "; ".join(result.warnings)
    store.append_delivery(run_key, receipt)
    run["delivery_status"] = "delivered" if result.success else "delivery_failed"
    run["delivery_id"] = delivery_id
    store.save(run)
    return {
        "status": receipt["status"],
        "delivery_id": delivery_id,
        "provider_response_id": result.message_id,
        "delivery_idempotent": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Phase R2 交付 CLI")
    parser.add_argument("run_key")
    parser.add_argument("--config", type=Path, default=Path("config/election_assessment.yaml"))
    parser.add_argument("--runs-root", type=Path, default=None)
    parser.add_argument("--provider", default="feishu")
    parser.add_argument("--mode", default="production")
    args = parser.parse_args()
    project_root = Path(__file__).resolve().parents[3]
    runs_root = args.runs_root or project_root / "data/election_assessment/tainan_2026/r2_runs"
    store = ReportRunStore(runs_root)
    result = deliver_report(
        store=store,
        run_key=args.run_key,
        config_path=args.config,
        provider=args.provider,
        mode=args.mode,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
