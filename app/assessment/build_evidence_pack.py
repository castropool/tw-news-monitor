"""CLI：台南选情半月报告证据包生成器。"""

from __future__ import annotations

import argparse
import json
import sys
import uuid
from dataclasses import replace
from datetime import date
from pathlib import Path
from typing import Any

from . import __version__
from .evidence_pack_builder import (
    EvidencePackError,
    atomic_write,
    atomic_write_json,
    canonical_hash,
    db_business_hash,
    build_pack,
    load_formal_data,
    load_yaml,
    read_only_conn,
    render_markdown,
    sha256_file,
)
from .evidence_pack_validator import ValidationContext, validate_evidence_pack
from .llm_input_contract import build_llm_input_contract, validate_llm_input_contract
from .reporting_period import PeriodError, resolve_reporting_period


PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent


def _parse_date(text: str) -> date:
    return date.fromisoformat(text)


def _hash_poll_seeds(seed_root: Path) -> str:
    files = sorted(
        p for p in seed_root.iterdir()
        if p.is_file() and p.name.lower().startswith("poll")
    )
    if not files:
        return ""
    return canonical_hash({p.name: sha256_file(p) for p in files})


def _hash_coverage_dir(coverage_dir: Path) -> str:
    files = sorted(p for p in coverage_dir.rglob("*") if p.is_file())
    return canonical_hash({p.relative_to(coverage_dir).as_posix(): sha256_file(p) for p in files})


def compute_input_hashes(config: dict, root: Path, coverage_dir: Path) -> dict[str, str]:
    paths = config["paths"]
    seed_root = (root / paths["events_seed"]).resolve().parent
    hashes: dict[str, str] = {}
    for key, rel in (
        ("events_seed", paths["events_seed"]),
        ("sources_seed", paths["sources_seed"]),
        ("initial_snapshot", paths["initial_snapshot"]),
        ("snapshot_history", paths["snapshot_history"]),
    ):
        p = (root / rel).resolve()
        if p.exists():
            hashes[key] = sha256_file(p)
    hashes["poll_seeds"] = _hash_poll_seeds(seed_root)
    hashes["coverage_dir"] = _hash_coverage_dir(coverage_dir)
    db_path = (root / paths["database"]).resolve()
    conn = read_only_conn(db_path)
    try:
        hashes["database_business"] = db_business_hash(conn)
    finally:
        conn.close()
    return hashes


def business_hash(pack: dict) -> str:
    keys = [
        "report_period",
        "data_status",
        "generation_eligibility",
        "current_snapshot",
        "previous_snapshot",
        "state_diff",
        "research_task_status_reconciliation",
        "snapshot_evidence_changes",
        "gap_changes",
        "risk_changes",
        "period_events",
        "background_events",
        "sources",
        "polls",
        "theme_status",
        "coverage_gaps",
        "known_limitations",
        "do_not_infer",
        "evidence_statistics",
    ]
    # run_at 属于运行元数据，不计入业务哈希
    report_period = dict(pack.get("report_period") or {})
    report_period.pop("run_at", None)
    payload = {k: pack.get(k) for k in keys}
    payload["report_period"] = report_period
    return canonical_hash(payload)


def run(
    *,
    config_path: Path,
    election_id: str | None,
    as_of: date | None,
    period_start: date | None,
    period_end: date | None,
    output_root: Path | None,
    validate_only: bool,
    force_rebuild: bool,
) -> int:
    try:
        config = load_yaml(config_path)
        root = config_path.resolve().parent.parent
        requested_election_id = election_id or config["election"]["election_id"]
        tz_name = config.get("timezone", "Asia/Taipei")
        run_days = tuple(int(x) for x in (config.get("schedule", {}).get("run_days") or [1, 16]))
        raw_rules = (config.get("schedule", {}) or {}).get("periods") or {}
        period_rules = {
            int(key.split("_")[-1]): value
            for key, value in raw_rules.items()
            if key.startswith("day_") and value
        }

        period = resolve_reporting_period(
            timezone_name=tz_name,
            run_days=run_days,
            period_rules=period_rules or None,
            as_of=as_of,
            explicit_start=period_start,
            explicit_end=period_end,
        )
        out_root = output_root or (root / config["paths"]["output_root"]).resolve()
        out_dir = out_root / f"{period.period_start.isoformat()}_{period.period_end.isoformat()}"

        formal = load_formal_data(config, root, requested_election_id)
        before_hashes = compute_input_hashes(config, root, formal.coverage_dir)

        old_pack = None
        old_pack_path = out_dir / "report_evidence_pack.json"
        if old_pack_path.exists():
            old_pack = json.loads(old_pack_path.read_text(encoding="utf-8"))

        pack = build_pack(formal, period, config, root, previous_pack=old_pack)
        pack["election_id"] = requested_election_id
        pack["election_name"] = config["election"]["display_name"]

        after_hashes = compute_input_hashes(config, root, formal.coverage_dir)
        authoritative_ids = pack["research_task_status_reconciliation"][
            "authoritative_active_task_ids"
        ]
        ctx = ValidationContext(
            formal_event_ids=set(formal.events),
            formal_source_ids=set(formal.sources),
            formal_link_pairs=formal.links,
            formal_poll_ids={p["poll_id"] for p in formal.polls},
            blocked_ids=formal.blocked_ids,
            active_snapshot_id=formal.active_snapshot["snapshot_id"],
            previous_snapshot_id=(
                formal.previous_snapshot["snapshot_id"] if formal.previous_snapshot else None
            ),
            coverage_name=formal.coverage_name,
            facts_cutoff=formal.coverage_preflight.get("facts_cutoff")
            or (formal.active_snapshot.get("state") or {}).get("coverage", {}).get("facts_cutoff"),
            poll_cutoff=formal.coverage_preflight.get("poll_cutoff")
            or (formal.active_snapshot.get("state") or {}).get("coverage", {}).get("poll_cutoff"),
            expected_counts={
                "formal_event_count": formal.counts["formal_event_count"],
                "formal_source_count": formal.counts["formal_source_count"],
                "formal_link_count": formal.counts["formal_link_count"],
                "formal_poll_count": formal.counts["formal_poll_count"],
            },
            before_hashes=before_hashes,
            after_hashes=after_hashes,
            period_start=period.period_start,
            period_end=period.period_end,
            max_background_total=int((config.get("background", {}) or {}).get("max_total", 15)),
            authoritative_active_task_ids=authoritative_ids,
        )
        validation = validate_evidence_pack(pack, ctx)
        pack["generation_eligibility"]["evidence_pack_ready"] = validation["evidence_pack_ready"]

        contract = build_llm_input_contract(pack)
        contract_validation = validate_llm_input_contract(
            contract,
            formal_event_ids=set(formal.events),
            formal_source_ids=set(formal.sources),
            formal_poll_ids={p["poll_id"] for p in formal.polls},
            formal_link_pairs=formal.links,
            authoritative_active_task_ids=authoritative_ids,
            facts_cutoff=ctx.facts_cutoff,
            period_end=period.period_end,
        )
        ctx = replace(ctx, llm_contract_validation=contract_validation)
        validation = validate_evidence_pack(pack, ctx)
        pack["generation_eligibility"]["evidence_pack_ready"] = validation["evidence_pack_ready"]
        pack["validation_summary"] = {
            "evidence_pack_ready": validation["evidence_pack_ready"],
            "errors": validation["errors"],
            "warnings": validation["warnings"],
            "llm_input_contract_ready": validation.get("llm_input_contract_ready", False),
        }

        run_id = uuid.uuid4().hex

        # 幂等检查
        new_business_hash = business_hash(pack)
        old_business_hash = None
        if old_pack_path.exists():
            old_business_hash = business_hash(old_pack)
            if old_business_hash != new_business_hash and not force_rebuild:
                raise EvidencePackError(
                    "同一周期业务内容不幂等：既有输出与本次生成不一致；"
                    "如需覆盖请使用 --force-rebuild"
                )
        business_equal = old_business_hash is None or old_business_hash == new_business_hash

        if not validate_only:
            atomic_write_json(out_dir / "reporting_period.json", period.to_dict())
            atomic_write_json(out_dir / "report_evidence_pack.json", pack)
            atomic_write(out_dir / "report_evidence_pack.md", render_markdown(pack))
            atomic_write_json(
                out_dir / "research_task_status_reconciliation.json",
                {
                    **pack["research_task_status_reconciliation"],
                    "active_task_ids_from_evidence_pack_before": sorted(
                        t.get("research_task_id")
                        for t in (old_pack.get("active_research_tasks") or [])
                        if t.get("research_task_id")
                    )
                    if old_pack
                    else [],
                },
            )
            atomic_write_json(
                out_dir / "snapshot_evidence_change_reconciliation.json",
                pack["snapshot_evidence_changes"],
            )
            prev_state = (
                formal.previous_snapshot["state"] if formal.previous_snapshot else None
            )
            prev_gap_count = (
                len((prev_state.get("coverage") or {}).get("known_gaps") or [])
                if prev_state
                else 0
            )
            cur_gap_count = len(
                ((formal.active_snapshot.get("state") or {}).get("coverage") or {}).get(
                    "known_gaps"
                )
                or []
            )
            gap_recon_file = {
                "previous_gap_count": prev_gap_count,
                "current_gap_count": cur_gap_count,
                "gap_changes": pack["gap_changes"],
                "resolved_gaps": [g["stable_gap_id"] for g in pack["gap_changes"] if g["change_type"] == "resolved"],
                "new_gaps": [g["stable_gap_id"] for g in pack["gap_changes"] if g["change_type"] == "new"],
                "renamed_gaps": [g["stable_gap_id"] for g in pack["gap_changes"] if g["change_type"] == "renamed"],
                "reframed_gaps": [g["stable_gap_id"] for g in pack["gap_changes"] if g["change_type"] == "reframed"],
                "narrowed_gaps": [g["stable_gap_id"] for g in pack["gap_changes"] if g["change_type"] == "narrowed"],
            }
            atomic_write_json(out_dir / "gap_change_reconciliation.json", gap_recon_file)
            atomic_write_json(
                out_dir / "state_diff_semantic_validation.json",
                {
                    "evidence_pack_ready": validation["evidence_pack_ready"],
                    "state_diff_semantically_valid": validation.get("state_diff_semantically_valid", False),
                    "dimensions": pack["state_diff"]["dimensions"],
                    "errors": validation["errors"],
                    "warnings": validation["warnings"],
                },
            )
            atomic_write_json(out_dir / "llm_input_contract.json", contract)
            atomic_write_json(out_dir / "llm_input_contract_validation.json", contract_validation)

        # 写库后再确认正式输入未变
        after_hashes = compute_input_hashes(config, root, formal.coverage_dir)
        formal_unchanged = before_hashes == after_hashes
        if not formal_unchanged:
            validation["errors"].append("formal_data_unchanged: 运行后正式输入哈希发生变化")
            validation["evidence_pack_ready"] = False
        validation["formal_data_unchanged"] = formal_unchanged
        validation["snapshot_data_unchanged"] = all(
            before_hashes.get(k) == after_hashes.get(k)
            for k in before_hashes
            if "snapshot" in k
        )
        validation["coverage_data_unchanged"] = (
            before_hashes.get("coverage_dir") == after_hashes.get("coverage_dir")
        )
        validation["poll_data_unchanged"] = (
            before_hashes.get("poll_seeds") == after_hashes.get("poll_seeds")
        )

        idempotency = {
            "first_run_business_hash": old_business_hash or new_business_hash,
            "second_run_business_hash": new_business_hash,
            "business_outputs_equal": business_equal,
            "formal_inputs_unchanged": formal_unchanged,
            "idempotent": business_equal and formal_unchanged,
        }
        if not idempotency["idempotent"]:
            validation["errors"].append("idempotent: 业务内容或正式输入不幂等")
            validation["evidence_pack_ready"] = False

        output_hashes: dict[str, str] = {}
        if not validate_only:
            for name in (
                "reporting_period.json",
                "report_evidence_pack.json",
                "report_evidence_pack.md",
                "research_task_status_reconciliation.json",
                "snapshot_evidence_change_reconciliation.json",
                "gap_change_reconciliation.json",
                "state_diff_semantic_validation.json",
                "llm_input_contract.json",
                "llm_input_contract_validation.json",
            ):
                output_hashes[name] = sha256_file(out_dir / name)

        manifest = {
            "run_id": run_id,
            "run_at": period.run_at,
            "timezone": period.timezone,
            "command": " ".join(sys.argv),
            "election_id": requested_election_id,
            "period_start": period.period_start.isoformat(),
            "period_end": period.period_end.isoformat(),
            "database_path": str((root / config["paths"]["database"]).resolve()),
            "coverage_path": str(formal.coverage_dir.resolve()),
            "active_snapshot_id": formal.active_snapshot["snapshot_id"],
            "previous_snapshot_id": (
                formal.previous_snapshot["snapshot_id"] if formal.previous_snapshot else None
            ),
            "input_hashes": before_hashes,
            "output_hashes": output_hashes,
            "formal_event_count": formal.counts["formal_event_count"],
            "formal_source_count": formal.counts["formal_source_count"],
            "formal_link_count": formal.counts["formal_link_count"],
            "formal_poll_count": formal.counts["formal_poll_count"],
            "period_event_count": len(pack["period_events"]),
            "background_event_count": len(pack["background_events"]),
            "included_source_count": len(pack["sources"]),
            "included_poll_count": len(pack["polls"]),
            "builder_version": __version__,
            "validation_ready": validation["evidence_pack_ready"],
            "idempotent": idempotency["idempotent"],
        }

        if not validate_only:
            atomic_write_json(out_dir / "evidence_pack_validation.json", validation)
            atomic_write_json(out_dir / "evidence_pack_idempotency.json", idempotency)
            output_hashes["evidence_pack_validation.json"] = sha256_file(
                out_dir / "evidence_pack_validation.json"
            )
            output_hashes["evidence_pack_idempotency.json"] = sha256_file(
                out_dir / "evidence_pack_idempotency.json"
            )
            manifest["output_hashes"] = output_hashes
            atomic_write_json(out_dir / "report_run_manifest.json", manifest)

        print(f"period: {period.period_label}")
        print(f"active_snapshot: {formal.active_snapshot['snapshot_id']}")
        print(f"previous_snapshot: {formal.previous_snapshot['snapshot_id'] if formal.previous_snapshot else None}")
        print(f"coverage: {formal.coverage_name}")
        print(f"period_events={len(pack['period_events'])} background={len(pack['background_events'])} "
              f"sources={len(pack['sources'])} polls={len(pack['polls'])}")
        print(f"evidence_pack_ready={validation['evidence_pack_ready']} "
              f"idempotent={idempotency['idempotent']}")
        print(f"output_dir={out_dir}")
        return 0 if validation["evidence_pack_ready"] else 1
    except (EvidencePackError, PeriodError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001
        import traceback

        traceback.print_exc(file=sys.stderr)
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="台南选情半月报告证据包生成器（不调用大模型、不修改正式数据）"
    )
    parser.add_argument("--config", default=str(PROJECT_ROOT / "config" / "election_assessment.yaml"))
    parser.add_argument("--election-id", default=None)
    parser.add_argument("--as-of", type=_parse_date, default=None)
    parser.add_argument("--period-start", type=_parse_date, default=None)
    parser.add_argument("--period-end", type=_parse_date, default=None)
    parser.add_argument("--output-root", type=Path, default=None)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--force-rebuild", action="store_true")
    args = parser.parse_args()

    if args.as_of is not None and (args.period_start is not None or args.period_end is not None):
        print("ERROR: --as-of 与显式周期（--period-start/--period-end）不得同时使用", file=sys.stderr)
        return 1
    if (args.period_start is None) != (args.period_end is None):
        print("ERROR: 显式周期必须同时提供 --period-start 和 --period-end", file=sys.stderr)
        return 1
    if (
        args.period_start is not None
        and args.period_end is not None
        and args.period_end < args.period_start
    ):
        print("ERROR: --period-end 不得早于 --period-start", file=sys.stderr)
        return 1

    return run(
        config_path=Path(args.config),
        election_id=args.election_id,
        as_of=args.as_of,
        period_start=args.period_start,
        period_end=args.period_end,
        output_root=args.output_root,
        validate_only=args.validate_only,
        force_rebuild=args.force_rebuild,
    )


if __name__ == "__main__":
    sys.exit(main())
