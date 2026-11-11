"""Publication preview: build a read-only batch preview from review decisions."""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from app.time_utils import TAIPEI

from .candidate_repository import CandidateRepository
from .formal_duplicate_checker import load_formal_events
from .formal_id_allocator import (
    allocate_event_id,
    allocate_source_id,
    collect_existing_ids,
    payload_hash,
)
from .review_workflow import is_review_stale
from app.election_context.formal_state_hash import formal_state_business_hash_from_db


def read_seed_events(config) -> list[dict[str, Any]]:
    path = config.path("events_seed")
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def read_seed_sources(config) -> list[dict[str, Any]]:
    path = config.path("sources_seed")
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def formal_seed_business_hash(config) -> str:
    h = hashlib.sha256()
    for p in (
        config.path("events_seed"),
        config.path("sources_seed"),
        config.path("initial_snapshot"),
        config.path("snapshot_history"),
    ):
        if p.exists():
            h.update(p.name.encode("utf-8"))
            h.update(p.read_bytes())
    return h.hexdigest()


def election_prefix_from_config(config) -> str:
    """Event id 前缀：cand_ntp -> ntp（缺省回落 tnn 台南约定）。"""
    prefix = str(config.get("election.candidate_id_prefix") or "cand_tnn")
    return prefix[5:] if prefix.startswith("cand_") else prefix


def resolve_sources(decision_sources: list[dict], seed_sources: list[dict], config) -> dict[str, Any]:
    existing = {s["source_id"]: s for s in seed_sources}
    reused = []
    new = []
    unresolved = []
    for src in decision_sources:
        formal_id = src.get("formal_source_id") or ""
        status = src.get("formal_match_status", "")
        if formal_id and status in ("exact", "normalized_match") and formal_id in existing:
            reused.append({"formal_source_id": formal_id, "name": src.get("source_name", "")})
        elif src.get("approve_new_source") is True:
            new.append(src)
        else:
            unresolved.append(src)
    return {"reused": reused, "new": new, "unresolved": unresolved}


def build_preview(
    repo: CandidateRepository,
    config,
    election_id: str,
    reviewer: str,
    review_decision_ids: list[str],
    batch_id: str | None = None,
    output_root: str | Path | None = None,
) -> dict[str, Any]:
    if not reviewer or reviewer.strip().lower() == "system":
        raise ValueError("reviewer required and must not be 'system'")
    decisions = [repo.get_review_decision(rid) for rid in review_decision_ids]
    if any(d is None for d in decisions):
        raise ValueError("one or more review decisions not found")
    election_prefix = election_prefix_from_config(config)
    seed_events = read_seed_events(config)
    seed_sources = read_seed_sources(config)
    seed_sources_by_id = {s["source_id"]: s for s in seed_sources}
    db_events = load_formal_events(config.path("formal_db"), election_id, config)
    existing_ids = collect_existing_ids(
        seed_events, seed_sources, {"events": {e["event_id"] for e in db_events}, "sources": set()}
    )
    existing_ids["sources"] = {s["source_id"] for s in seed_sources}

    now = datetime.now(TAIPEI).isoformat()
    bid = batch_id or (
        f"pub_{datetime.now(TAIPEI).strftime('%Y%m%d_%H%M%S')}_{uuid.uuid4().hex[:8]}"
    )
    allocated_event_ids: set[str] = set(existing_ids["events"])
    allocated_source_ids: set[str] = set(existing_ids["sources"])
    items: list[dict[str, Any]] = []
    new_events: list[dict[str, Any]] = []
    new_sources: list[dict[str, Any]] = []
    new_links: list[dict[str, Any]] = []
    attachments: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    errors: list[str] = []

    for decision in decisions:
        cid = decision["candidate_id"]
        if is_review_stale(repo, decision):
            errors.append(f"stale_review:{decision['review_decision_id']}")
            continue
        event_payload = json.loads(decision.get("edited_event_payload_json", "{}") or "{}")
        decision_type = decision["decision"]
        if decision_type in ("reject", "hold", "needs_edit"):
            continue
        if decision_type == "attach_to_existing_event":
            target = decision.get("target_formal_event_id", "")
            if target not in {e["event_id"] for e in db_events}:
                errors.append(f"target_event_not_found:{target}")
                continue
            attach_date = event_payload.get("event_date") or ""
            decision_sources = json.loads(decision.get("source_resolution_json", "[]") or "[]")
            resolved = resolve_sources(decision_sources, seed_sources, config)
            unresolved.extend(resolved["unresolved"])
            for src in resolved["new"]:
                sid = allocate_source_id(
                    allocated_source_ids,
                    src.get("domain", ""),
                    src.get("source_name", ""),
                    attach_date,
                )
                allocated_source_ids.add(sid)
                full_source = {
                    **src,
                    "source_id": sid,
                    "publisher": src.get("source_name", ""),
                    "url": src.get("url", ""),
                    "published_at": attach_date,
                    "source_type": "news",
                }
                new_sources.append(full_source)
                new_links.append({"event_id": target, "source_id": sid})
                attachments.append(
                    {
                        "event_id": target,
                        "source_id": sid,
                        "is_primary": False,
                        "source": full_source,
                    }
                )
                items.append(
                    {
                        "publication_item_id": f"item_{hashlib.sha256(f'{bid}|{decision['review_decision_id']}|attachsrc|{sid}'.encode()).hexdigest()[:16]}",
                        "batch_id": bid,
                        "candidate_id": cid,
                        "review_decision_id": decision["review_decision_id"],
                        "operation_type": "create_source",
                        "allocated_event_id": "",
                        "target_event_id": target,
                        "payload_hash": payload_hash(src),
                        "status": "planned",
                    }
                )
            for src in resolved["reused"]:
                sid = src["formal_source_id"]
                new_links.append({"event_id": target, "source_id": sid})
                full_source = {
                    **seed_sources_by_id.get(sid, {"source_id": sid}),
                    "is_primary": False,
                }
                attachments.append(
                    {
                        "event_id": target,
                        "source_id": sid,
                        "is_primary": False,
                        "source": full_source,
                    }
                )
                item_raw = f"{bid}|{decision['review_decision_id']}|attachlink|{sid}"
                items.append(
                    {
                        "publication_item_id": "item_" + hashlib.sha256(item_raw.encode("utf-8")).hexdigest()[:16],
                        "batch_id": bid,
                        "candidate_id": cid,
                        "review_decision_id": decision["review_decision_id"],
                        "operation_type": "link_event_source",
                        "allocated_event_id": "",
                        "target_event_id": target,
                        "payload_hash": payload_hash(src),
                        "status": "planned",
                    }
                )
            items.append(
                {
                    "publication_item_id": f"item_{hashlib.sha256(f'{bid}|{decision['review_decision_id']}|attach'.encode()).hexdigest()[:16]}",
                    "batch_id": bid,
                    "candidate_id": cid,
                    "review_decision_id": decision["review_decision_id"],
                    "operation_type": "attach_source",
                    "allocated_event_id": "",
                    "target_event_id": target,
                    "payload_hash": payload_hash(event_payload),
                    "status": "planned",
                }
            )
            continue

        # create_event / approve_as_subevent
        date = event_payload.get("event_date") or ""
        title = event_payload.get("title") or ""
        event_id = allocate_event_id(allocated_event_ids, date, title, election_prefix=election_prefix)
        allocated_event_ids.add(event_id)
        payload = {
            "event_id": event_id,
            "election_id": election_id,
            "occurred_at": date,
            "event_date": date,
            "event_type": event_payload.get("event_type", "unknown"),
            "title": title,
            "fact_summary": event_payload.get("summary", ""),
            # 人工/自动审核发布的事件视为已验证事实（seed 校验要求合法 fact_status）
            "fact_status": "verified",
            "significance_score": 50,
            "limitations": [],
            "actors": event_payload.get("actors", []),
            "issues": event_payload.get("themes", []),
            "sources": [],
            "analysis_json": {
                "observed_facts": event_payload.get("observed_facts", []),
                "attributed_statements": event_payload.get("attributed_statements", []),
                "allegations": event_payload.get("allegations", []),
                "limitations": event_payload.get("limitations", []),
            },
        }
        if decision_type == "approve_as_subevent":
            payload["parent_event_id"] = decision.get("target_formal_event_id", "")
            payload["analysis_json"]["subevents"] = []
        new_events.append(payload)
        items.append(
            {
                "publication_item_id": f"item_{hashlib.sha256(f'{bid}|{decision['review_decision_id']}|create'.encode()).hexdigest()[:16]}",
                "batch_id": bid,
                "candidate_id": cid,
                "review_decision_id": decision["review_decision_id"],
                "operation_type": "create_event",
                "allocated_event_id": event_id,
                "target_event_id": "",
                "payload_hash": payload_hash(payload),
                "status": "planned",
            }
        )

        # sources
        decision_sources = json.loads(decision.get("source_resolution_json", "[]") or "[]")
        resolved = resolve_sources(decision_sources, seed_sources, config)
        unresolved.extend(resolved["unresolved"])
        for src in resolved["new"]:
            sid = allocate_source_id(
                allocated_source_ids,
                src.get("domain", ""),
                src.get("source_name", ""),
                date,
            )
            allocated_source_ids.add(sid)
            new_sources.append(
                {
                    "source_id": sid,
                    "publisher": src.get("source_name", ""),
                    "title": src.get("title", ""),
                    "url": src.get("url", ""),
                    "published_at": date,
                    "source_type": "news",
                }
            )
            full_source = {
                **src,
                "source_id": sid,
                "publisher": src.get("source_name", ""),
                "url": src.get("url", ""),
            }
            payload["sources"].append({**full_source, "is_primary": True})
            new_links.append({"event_id": event_id, "source_id": sid})
            items.append(
                {
                    "publication_item_id": f"item_{hashlib.sha256(f'{bid}|{decision['review_decision_id']}|src|{sid}'.encode()).hexdigest()[:16]}",
                    "batch_id": bid,
                    "candidate_id": cid,
                    "review_decision_id": decision["review_decision_id"],
                    "operation_type": "create_source",
                    "allocated_event_id": "",
                    "target_event_id": event_id,
                    "payload_hash": payload_hash(src),
                    "status": "planned",
                }
            )
        for src in resolved["reused"]:
            full_source = seed_sources_by_id.get(
                src["formal_source_id"], {"source_id": src["formal_source_id"]}
            )
            payload["sources"].append({**full_source, "is_primary": False})
            new_links.append({"event_id": event_id, "source_id": src["formal_source_id"]})
            src_id = src["formal_source_id"]
            item_raw = f"{bid}|{decision['review_decision_id']}|link|{src_id}"
            items.append(
                {
                    "publication_item_id": "item_" + hashlib.sha256(item_raw.encode("utf-8")).hexdigest()[:16],
                    "batch_id": bid,
                    "candidate_id": cid,
                    "review_decision_id": decision["review_decision_id"],
                    "operation_type": "link_event_source",
                    "allocated_event_id": "",
                    "target_event_id": event_id,
                    "payload_hash": payload_hash(src),
                    "status": "planned",
                }
            )

    if unresolved:
        errors.append(f"unresolved_sources:{','.join(s.get('source_name', '?') for s in unresolved)}")

    preview = {
        "batch_id": bid,
        "election_id": election_id,
        "reviewer": reviewer,
        "created_at": now,
        "review_decision_ids": review_decision_ids,
        "formal_data_hash_before": formal_seed_business_hash(config),
        "formal_state_hash_before": formal_state_business_hash_from_db(config.path("formal_db")),
        "new_events": new_events,
        "new_sources": new_sources,
        "new_links": new_links,
        "attachments": attachments,
        "items": items,
        "errors": errors,
        "warnings": [],
    }

    out_dir = Path(output_root) if output_root else (
        config.path("output_root") / "publication_batches" / bid
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "publication_preview.json").write_text(
        json.dumps(preview, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    md_lines = [
        "# 发布预览",
        "",
        f"- batch：{bid}",
        f"- reviewer：{reviewer}",
        f"- 新事件：{len(new_events)}",
        f"- 新来源：{len(new_sources)}",
        f"- 新关联：{len(new_links)}",
        "",
    ]
    for evt in new_events:
        md_lines.append(f"## {evt['event_id']}")
        md_lines.append("")
        md_lines.append(f"- 日期：{evt.get('occurred_at')}")
        md_lines.append(f"- 类型：{evt.get('event_type')}")
        md_lines.append(f"- 标题：{evt.get('title')}")
        md_lines.append(f"- 摘要：{evt.get('fact_summary')}")
        md_lines.append(f"- 人物：{','.join(evt.get('actors', []))}")
        md_lines.append("")
    (out_dir / "publication_preview.md").write_text("\n".join(md_lines), encoding="utf-8")
    batch = {
        "batch_id": bid,
        "election_id": election_id,
        "created_at": now,
        "created_by": reviewer,
        "status": "draft",
        "formal_data_hash_before": preview["formal_data_hash_before"],
        "candidate_hashes_json": json.dumps(
            {d["review_decision_id"]: d.get("candidate_business_hash", "") for d in decisions},
            ensure_ascii=False,
        ),
        "review_decision_ids_json": json.dumps(review_decision_ids, ensure_ascii=False),
        "new_event_count": len(new_events),
        "existing_event_attachment_count": sum(1 for i in items if i["operation_type"] == "attach_source"),
        "new_source_count": len(new_sources),
        "new_event_source_link_count": len(new_links),
        "preview_ready": 1,
        "validation_ready": 0,
        "backup_ready": 0,
        "staging_ready": 0,
        "commit_ready": 0,
        "commit_completed": 0,
        "committed_at": "",
        "rolled_back_at": "",
        "error_summary": "",
    }
    repo.upsert_publication_batch(batch)
    for item in items:
        repo.insert_publication_item(item)
    return preview
