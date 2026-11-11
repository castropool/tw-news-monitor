"""Conservative low-risk automated candidate approval and publication runner.

Design contract:
- Dedicated machine principal ``auto_approver_v1``; the human
  ``review_and_publish`` entry keeps rejecting reviewer ``system``.
- Every manifest record carries ``decision_origin=automated_policy``,
  ``policy_version``, full per-gate reasons and ``candidate_business_hash``.
- Idempotency key: candidate_id + candidate_business_hash + policy_version.
- One-candidate-at-a-time publication; any failure stops the round; N
  consecutive failures open the circuit breaker (manual reset only).
- After every committed publication batch the post-publication refresh runs
  (snapshot + coverage activation via ``run_post_publication_pipeline``); the
  batch_id is the downstream idempotency key.  A downstream gate failure marks
  the manifest record ``downstream_status=failed`` (facts stay published), stops
  the round and counts toward the circuit breaker.  ``--skip-downstream`` is a
  test/fault-isolation escape hatch refused by the production configuration.
- ``--check-only`` evaluates without writing anything (no DB writes, no
  manifest, no output files) and never executes downstream.
- Never relaxes the human review path; only the conservative low-risk subset
  defined by ``auto_publish_gate`` may be auto-published.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from app.time_utils import TAIPEI

from .auto_publish_gate import (
    APPROVE_DECISIONS,
    ELIGIBLE_INPUT_STATUSES,
    AutoPublishPolicy,
    evaluate_candidate,
)
from .candidate_repository import CandidateRepository
from .config import load_config
from .publication_pipeline import batch_hash, commit_batch, prepare_batch
from .publication_preview import build_preview, formal_seed_business_hash
from .review_workflow import candidate_business_hash
from .state_machine import apply_status
from app.election_context.formal_state_hash import formal_state_business_hash_from_db

RUN_DATE_FORMAT = "%Y-%m-%d"
MANIFEST_FILENAME = "auto_publish_manifest.jsonl"


class AutoPublishManifest:
    """Append-only JSONL manifest for automated publication runs."""

    def __init__(self, manifest_dir: str | Path):
        self.path = Path(manifest_dir) / MANIFEST_FILENAME

    def append(self, record: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.path, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")

    def read_records(self) -> list[dict[str, Any]]:
        if not self.path.exists():
            return []
        records = []
        for line in self.path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return records

    def count_published_on(self, run_date: str) -> int:
        """Full-success published candidates for the day (distinct candidate_id).

        A published record whose downstream refresh failed is NOT counted as a
        full success (it blocks the daily quota progress).
        """
        seen: set[str] = set()
        for r in self.read_records():
            if (
                r.get("run_date") == run_date
                and r.get("status") == "published"
                and r.get("downstream_status") not in ("failed",)
            ):
                seen.add(r.get("candidate_id"))
        return len(seen)

    def has_published_key(self, candidate_id: str, business_hash: str, policy_version: str) -> bool:
        for r in self.read_records():
            if (
                r.get("candidate_id") == candidate_id
                and r.get("candidate_business_hash") == business_hash
                and r.get("policy_version") == policy_version
                and r.get("status") == "published"
            ):
                return True
        return False

    def latest_record_for(self, candidate_id: str, business_hash: str, policy_version: str) -> dict[str, Any]:
        for r in reversed(self.read_records()):
            if (
                r.get("candidate_id") == candidate_id
                and r.get("candidate_business_hash") == business_hash
                and r.get("policy_version") == policy_version
            ):
                return r
        return {}

    def consecutive_failed_count(self) -> int:
        """Trailing streak of consecutive failed records (append-only tail scan).

        Both ``failed`` records and published records whose downstream refresh
        failed count toward the streak; only a fully successful published /
        skipped record resets it.  Intermediate selected/eligible evaluation
        records are neutral.
        """
        count = 0
        for r in reversed(self.read_records()):
            status = r.get("status")
            if status == "failed" or (
                status == "published" and r.get("downstream_status") == "failed"
            ):
                count += 1
            elif status in ("published", "skipped"):
                break
        return count


def _recheck_freshness(repo: CandidateRepository, candidate_id: str, expected_hash: str) -> list[str]:
    current = candidate_business_hash(repo, candidate_id)
    if current != expected_hash:
        return [f"candidate_drifted:expected={expected_hash} actual={current}"]
    return []


def _build_event_payload(repo: CandidateRepository, candidate: dict[str, Any]) -> dict[str, Any]:
    assertions = repo.get_assertions(candidate["candidate_id"])
    observed = [a["assertion_text"] for a in assertions if a["assertion_kind"] == "observed_fact"]
    statements = [a["assertion_text"] for a in assertions if a["assertion_kind"] == "actor_statement"]
    allegations = [a["assertion_text"] for a in assertions if a["assertion_kind"] == "allegation"]
    limitations = [
        a["assertion_text"]
        for a in assertions
        if a["assertion_kind"] in ("uncertain_report", "unknown", "media_interpretation")
    ]
    actors: list[str] = []
    primary = candidate.get("primary_actor") or ""
    if primary:
        actors.append(primary)
    for a in json.loads(candidate.get("secondary_actors_json", "[]") or "[]"):
        if a and a not in actors:
            actors.append(a)
    return {
        "event_date": candidate.get("canonical_event_date", ""),
        "event_date_precision": candidate.get("event_date_precision", ""),
        "event_type": candidate.get("candidate_event_type", ""),
        "title": candidate.get("candidate_title", ""),
        "summary": candidate.get("candidate_summary", ""),
        "actors": actors,
        "themes": json.loads(candidate.get("themes_json", "[]") or "[]"),
        "locations": json.loads(candidate.get("locations_json", "[]") or "[]"),
        "observed_facts": observed,
        "attributed_statements": statements,
        "allegations": allegations,
        "limitations": limitations,
    }


def _build_sources_payload(repo: CandidateRepository, candidate_id: str) -> list[dict[str, Any]]:
    return [
        {
            "source_name": s.get("normalized_source_name", ""),
            "domain": s.get("normalized_domain", ""),
            "formal_source_id": s.get("formal_source_id", ""),
            "formal_match_status": s.get("formal_match_status", ""),
            "approve_new_source": False,
        }
        for s in repo.get_sources(candidate_id)
    ]


def _next_decision_id(repo: CandidateRepository, candidate_id: str, decision: str) -> str:
    seq = repo.conn.execute("SELECT COUNT(*) FROM review_decisions").fetchone()[0] + 1
    now = datetime.now(TAIPEI).isoformat()
    return (
        f"rev_{seq:06d}_"
        + hashlib.sha256(f"{candidate_id}|{now}|{decision}".encode("utf-8")).hexdigest()[:12]
    )


def publish_one(
    repo: CandidateRepository,
    config,
    policy: AutoPublishPolicy,
    election_id: str,
    candidate: dict[str, Any],
    business_hash: str,
    *,
    output_root: str | Path | None,
) -> dict[str, Any]:
    """Publish one gated candidate through the existing publication chain.

    Never re-implements DB/seed commit: delegates to preview -> prepare ->
    commit and only records the automated decision + terminal status.
    """
    cid = candidate["candidate_id"]
    decision = "approve_new_event"
    now = datetime.now(TAIPEI).isoformat()
    rid = _next_decision_id(repo, cid, decision)
    record = {
        "review_decision_id": rid,
        "candidate_id": cid,
        "decision": decision,
        "reviewer": policy.auto_approver,
        "reviewed_at": now,
        "review_reason": "auto_policy_eligible",
        "edited_event_payload_json": json.dumps(
            _build_event_payload(repo, candidate), ensure_ascii=False
        ),
        "target_formal_event_id": "",
        "source_resolution_json": json.dumps(
            _build_sources_payload(repo, cid), ensure_ascii=False
        ),
        "decision_version": "0.1.0",
        "candidate_business_hash": business_hash,
        "created_at": now,
    }
    repo.insert_review_decision(record)
    apply_status(repo, cid, "under_review", updated_run_id=f"auto:{rid}")
    apply_status(repo, cid, "review_approved", updated_run_id=f"auto:{rid}")

    before_seed = formal_seed_business_hash(config)
    before_state = formal_state_business_hash_from_db(config.path("formal_db"))

    preview = build_preview(
        repo, config, election_id, policy.auto_approver, [rid],
        output_root=output_root,
    )
    if preview["errors"]:
        raise ValueError(f"auto preview errors: {preview['errors']}")
    batch_id = preview["batch_id"]
    prepare_batch(repo, config, election_id, batch_id, preview, policy.auto_approver)
    current = repo.get_candidate(cid)["review_status"]
    if current != "publication_prepared":
        apply_status(repo, cid, "publication_prepared", updated_run_id=f"pub:{batch_id}")
    commit = commit_batch(
        repo, config, election_id, batch_id, policy.auto_approver,
        batch_hash(preview), preview,
    )
    post = commit.get("post_commit_validation", {})
    if not post.get("post_commit_ready"):
        raise ValueError(f"post commit validation failed: {post.get('errors', [])}")
    apply_status(repo, cid, "published", updated_run_id=f"pub:{batch_id}")
    return {
        "status": "published",
        "review_decision_id": rid,
        "batch_id": batch_id,
        "formal_hash_before": before_seed,
        "formal_state_hash_before": before_state,
        "formal_hash_after": commit.get("formal_hash_after", ""),
        "formal_state_hash_after": commit.get("formal_state_hash_after", ""),
        "errors": [],
    }


def _downstream_request_path(config, batch_id: str) -> Path:
    return (
        config.path("output_root")
        / "publication_batches"
        / batch_id
        / "downstream_refresh_request.json"
    )


def run_downstream_for_batch(
    repo: CandidateRepository,
    config,
    policy: AutoPublishPolicy,
    batch_id: str,
    run_date: str,
) -> dict[str, Any]:
    """Run the post-publication snapshot/coverage activation for one committed
    batch.

    Raises RuntimeError on any deterministic gate failure so the caller stops
    the round.  Facts committed by the publication transaction are never rolled
    back here; only the downstream state is marked failed upstream.
    """
    if policy.skip_downstream:
        return {"downstream_status": "skipped_by_flag", "batch_id": batch_id}
    request_path = _downstream_request_path(config, batch_id)
    if not request_path.exists():
        raise RuntimeError(f"downstream_refresh_request missing: {request_path}")
    request = json.loads(request_path.read_text(encoding="utf-8"))
    # Input hash drift gate: the formal DB must still match the committed batch
    # record before any downstream product is activated.
    current_hash = formal_state_business_hash_from_db(config.path("formal_db"))
    if request.get("formal_state_hash") and request["formal_state_hash"] != current_hash:
        raise RuntimeError(
            f"input_hash_drift: formal state changed after publication "
            f"(expected={request['formal_state_hash'][:12]} actual={current_hash[:12]})"
        )
    from app.election_context.run_post_publication_pipeline import (
        run_post_publication_pipeline,
    )

    manifest = run_post_publication_pipeline(
        repo,
        config,
        publication_batch_id=batch_id,
        request_path=request_path,
        run_date=run_date,
        manual=False,
        allow_real_snapshot=policy.auto_activate_snapshots,
    )
    if manifest.get("retry_required"):
        raise RuntimeError(
            "downstream incomplete: "
            f"assessment={manifest.get('assessment', {}).get('status', '')}"
        )
    snapshot = manifest.get("snapshot", {})
    coverage = manifest.get("coverage", {})
    return {
        "downstream_status": "ok",
        "refresh_batch_id": manifest.get("refresh_batch_id", ""),
        "snapshot_id": snapshot.get("active_snapshot_id", ""),
        "snapshot_status": snapshot.get("status", ""),
        "coverage_version": coverage.get("version", ""),
        "coverage_activation": coverage.get("activation_status", ""),
        "facts_cutoff": manifest.get("facts_cutoff", ""),
    }


def run_auto_publish(config, args) -> dict[str, Any]:
    policy = AutoPublishPolicy.from_config(config)
    now = datetime.now(TAIPEI)
    run_id = f"auto_pub_{now.strftime('%Y%m%d_%H%M%S_%f')}"
    run_date = now.strftime(RUN_DATE_FORMAT)
    base = {
        "run_id": run_id,
        "run_date": run_date,
        "decision_origin": "automated_policy",
        "policy_version": policy.policy_version,
        "timestamp": now.isoformat(),
    }

    # 7) --skip-downstream is a test / fault-isolation escape hatch only; the
    #     production configuration refuses it by default.
    if getattr(args, "skip_downstream", False):
        if not policy.allow_skip_downstream:
            return {
                **base,
                "status": "blocked",
                "reason": "skip_downstream_not_allowed:auto_publish.allow_skip_downstream=false",
                "evaluated": 0,
                "eligible": 0,
                "rejected": 0,
                "published": 0,
            }
        policy.skip_downstream = True

    # 6) disabled by configuration: zero side effects, nothing written
    if not policy.enabled:
        return {
            **base,
            "status": "disabled",
            "reason": "auto_publish.enabled=false",
            "evaluated": 0,
            "eligible": 0,
            "rejected": 0,
            "published": 0,
        }

    # 6) kill switch (manual reset only; this runner never creates/deletes it)
    if policy.kill_switch_file.exists():
        return {
            **base,
            "status": "blocked",
            "reason": f"kill_switch:{policy.kill_switch_file}",
            "evaluated": 0,
            "eligible": 0,
            "rejected": 0,
            "published": 0,
        }

    # 6) circuit breaker opened by previous consecutive failures (manual reset only)
    if policy.circuit_break_file.exists():
        return {
            **base,
            "status": "blocked",
            "reason": f"circuit_open:{policy.circuit_break_file}",
            "evaluated": 0,
            "eligible": 0,
            "rejected": 0,
            "published": 0,
        }

    manifest = AutoPublishManifest(policy.manifest_dir)

    # 6) daily quota
    daily_published = manifest.count_published_on(run_date)
    if daily_published >= policy.max_daily:
        return {
            **base,
            "status": "blocked",
            "reason": f"daily_limit:{daily_published}>={policy.max_daily}",
            "evaluated": 0,
            "eligible": 0,
            "rejected": 0,
            "published": 0,
        }

    election_id = config.resolve_election_id(args.election_id)
    candidate_db = args.candidate_db or config.path("candidate_db")
    repo = CandidateRepository(candidate_db)
    repo.connect()
    repo.create_tables()
    try:
        # Candidates already published with a failed downstream refresh re-enter
        # the round for a downstream retry only (never republished).  check-only
        # and --skip-downstream never touch them.
        downstream_retry_ids: set[str] = set()
        if not args.check_only and not policy.skip_downstream:
            for r in manifest.read_records():
                if r.get("status") == "published" and r.get("downstream_status") == "failed":
                    downstream_retry_ids.add(r.get("candidate_id"))
        candidates = [
            c for c in repo.list_candidates(limit=100000)
            if c.get("review_status") in ELIGIBLE_INPUT_STATUSES
            or c.get("candidate_id") in downstream_retry_ids
        ]
        candidates.sort(
            key=lambda c: (c.get("canonical_event_date") or "", c["candidate_id"]),
            reverse=True,
        )

        result = {
            **base,
            "status": "completed",
            "election_id": election_id,
            "evaluated": 0,
            "eligible": 0,
            "rejected": 0,
            "published": 0,
            "failed": 0,
            "skipped": 0,
            "circuit_open": False,
            "check_only": bool(args.check_only),
            "candidates": [],
        }
        stop_reason = ""

        def _open_circuit(reason: str) -> None:
            policy.circuit_break_file.parent.mkdir(parents=True, exist_ok=True)
            policy.circuit_break_file.write_text(
                json.dumps(
                    {
                        "opened_at": datetime.now(TAIPEI).isoformat(),
                        "run_id": run_id,
                        "consecutive_failures": manifest.consecutive_failed_count(),
                        "reason": reason,
                    },
                    ensure_ascii=False,
                    indent=2,
                ),
                encoding="utf-8",
            )
            result["circuit_open"] = True

        def _handle_failure(cid: str, business_hash: str, error: str) -> None:
            """Record a failed publication and open the circuit breaker when the
            consecutive failure streak (across rounds) reaches the limit."""
            nonlocal stop_reason
            result["failed"] += 1
            stop_reason = error
            manifest.append(
                {
                    **base,
                    "status": "failed",
                    "candidate_id": cid,
                    "candidate_business_hash": business_hash,
                    "error": error,
                }
            )
            if manifest.consecutive_failed_count() >= policy.consecutive_failure_limit:
                _open_circuit(error)

        def _handle_downstream_failure(
            cid: str, business_hash: str, outcome: dict[str, Any], error: str
        ) -> None:
            """Post-publication refresh failure after a committed batch.

            Facts are published and stay published (no rollback); the manifest
            record keeps ``status=published`` so the idempotency key still
            protects against republishing, while ``downstream_status=failed``
            marks the batch as not fully successful and feeds the circuit
            breaker (downstream failures count as failures).
            """
            nonlocal stop_reason
            result["failed"] += 1
            stop_reason = error
            manifest.append(
                {
                    **base,
                    "status": "published",
                    "downstream_status": "failed",
                    "downstream_error": error,
                    "candidate_id": cid,
                    "candidate_business_hash": business_hash,
                    "review_decision_id": outcome.get("review_decision_id", ""),
                    "batch_id": outcome.get("batch_id", ""),
                    "formal_hash_before": outcome.get("formal_hash_before", ""),
                    "formal_state_hash_before": outcome.get("formal_state_hash_before", ""),
                    "formal_hash_after": outcome.get("formal_hash_after", ""),
                    "formal_state_hash_after": outcome.get("formal_state_hash_after", ""),
                    "errors": outcome.get("errors", []),
                }
            )
            if manifest.consecutive_failed_count() >= policy.consecutive_failure_limit:
                _open_circuit(error)

        def _record_published(cid: str, business_hash: str, outcome: dict[str, Any], extra: dict[str, Any]) -> None:
            manifest.append(
                {
                    **base,
                    "status": "published",
                    "candidate_id": cid,
                    "candidate_business_hash": business_hash,
                    "review_decision_id": outcome.get("review_decision_id", ""),
                    "batch_id": outcome.get("batch_id", ""),
                    "formal_hash_before": outcome.get("formal_hash_before", ""),
                    "formal_state_hash_before": outcome.get("formal_state_hash_before", ""),
                    "formal_hash_after": outcome.get("formal_hash_after", ""),
                    "formal_state_hash_after": outcome.get("formal_state_hash_after", ""),
                    "errors": outcome.get("errors", []),
                    **extra,
                }
            )

        for candidate in candidates:
            cid = candidate["candidate_id"]

            # 7) idempotency first: candidate_id + candidate_business_hash +
            #    policy_version; a previously published key is never republished,
            #    regardless of what the current gates would say.
            try:
                pre_hash = candidate_business_hash(repo, cid)
            except ValueError:
                pre_hash = ""
            if pre_hash and manifest.has_published_key(cid, pre_hash, policy.policy_version):
                latest = manifest.latest_record_for(cid, pre_hash, policy.policy_version) or {}
                if (
                    latest.get("downstream_status") == "failed"
                    and not args.check_only
                    and not policy.skip_downstream
                ):
                    # Facts were already committed by the previous run; only the
                    # post-publication refresh is retried (idempotent by batch).
                    result["candidates"].append(
                        {"candidate_id": cid, "decision": "downstream_retry"}
                    )
                    try:
                        ds = run_downstream_for_batch(
                            repo, config, policy, latest.get("batch_id", ""), run_date
                        )
                    except Exception as exc:  # noqa: BLE001 - downstream gate failure
                        _handle_downstream_failure(
                            cid, pre_hash,
                            {"batch_id": latest.get("batch_id", ""), "errors": []},
                            str(exc),
                        )
                        break
                    _record_published(
                        cid, pre_hash,
                        {"review_decision_id": latest.get("review_decision_id", ""),
                         "batch_id": latest.get("batch_id", ""),
                         "formal_hash_before": latest.get("formal_hash_before", ""),
                         "formal_state_hash_before": latest.get("formal_state_hash_before", ""),
                         "formal_hash_after": latest.get("formal_hash_after", ""),
                         "formal_state_hash_after": latest.get("formal_state_hash_after", ""),
                         "errors": latest.get("errors", [])},
                        {k: ds[k] for k in (
                            "downstream_status", "refresh_batch_id", "snapshot_id",
                            "snapshot_status", "coverage_version", "coverage_activation",
                            "facts_cutoff",
                        ) if k in ds},
                    )
                    result["published"] += 1
                    continue
                result["skipped"] += 1
                result["candidates"].append(
                    {"candidate_id": cid, "decision": "skipped_idempotent"}
                )
                if not args.check_only:
                    manifest.append(
                        {
                            **base,
                            "status": "skipped",
                            "candidate_id": cid,
                            "candidate_business_hash": pre_hash,
                            "reason": "idempotency_key_published",
                        }
                    )
                continue

            eval_result = evaluate_candidate(repo, config, cid, policy)
            result["evaluated"] += 1
            if not args.check_only:
                manifest.append(
                    {
                        **base,
                        "status": "selected",
                        "candidate_id": cid,
                        "candidate_business_hash": eval_result.get("candidate_business_hash", ""),
                        "gate_results": eval_result["gate_results"],
                        "gate_reasons": eval_result["reasons"],
                    }
                )

            if eval_result["decision"] == "rejected":
                result["rejected"] += 1
                result["candidates"].append(
                    {
                        "candidate_id": cid,
                        "decision": "rejected",
                        "reasons": eval_result["reasons"],
                    }
                )
                if not args.check_only:
                    manifest.append(
                        {
                            **base,
                            "status": "rejected",
                            "candidate_id": cid,
                            "candidate_business_hash": eval_result.get("candidate_business_hash", ""),
                            "gate_results": eval_result["gate_results"],
                            "gate_reasons": eval_result["reasons"],
                        }
                    )
                continue

            # eligible
            business_hash = eval_result.get("candidate_business_hash", "")
            result["eligible"] += 1
            result["candidates"].append(
                {"candidate_id": cid, "decision": "eligible", "reasons": []}
            )
            if args.check_only:
                continue

            # publish gate closure in the manifest
            manifest.append(
                {
                    **base,
                    "status": "eligible",
                    "candidate_id": cid,
                    "candidate_business_hash": business_hash,
                    "gate_results": eval_result["gate_results"],
                    "gate_reasons": [],
                }
            )

            # freshness check right before the decision is recorded
            drift = _recheck_freshness(repo, cid, business_hash)
            if drift:
                _handle_failure(cid, business_hash, f"drift:{','.join(drift)}")
                break

            try:
                outcome = publish_one(
                    repo, config, policy, election_id, candidate, business_hash,
                    output_root=args.output_root,
                )
            except Exception as exc:  # any failure stops this round
                current = repo.get_candidate(cid)
                if current and current["review_status"] == "publication_prepared":
                    apply_status(
                        repo, cid, "publication_failed",
                        updated_run_id=f"fail:{run_id}",
                    )
                _handle_failure(cid, business_hash, str(exc))
                break

            if outcome["status"] != "published":
                _handle_failure(cid, business_hash, "; ".join(outcome["errors"]))
                break

            # 8) Post-publication refresh hook: snapshot/coverage activation for
            #     this committed batch.  A gate failure marks downstream_failed,
            #     stops the round and feeds the circuit breaker; the committed
            #     facts are never rolled back.  check-only never reaches here.
            try:
                ds = run_downstream_for_batch(
                    repo, config, policy, outcome["batch_id"], run_date
                )
            except Exception as exc:  # noqa: BLE001 - downstream gate failure
                _handle_downstream_failure(cid, business_hash, outcome, str(exc))
                break
            _record_published(
                cid, business_hash, outcome,
                {k: ds[k] for k in (
                    "downstream_status", "refresh_batch_id", "snapshot_id",
                    "snapshot_status", "coverage_version", "coverage_activation",
                    "facts_cutoff",
                ) if k in ds},
            )
            result["published"] += 1

            # 6) per-round quota
            if result["published"] >= policy.max_per_run:
                stop_reason = f"max_per_run:{policy.max_per_run}"
                break

        if stop_reason:
            result["stop_reason"] = stop_reason
        return result
    finally:
        repo.close()


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Conservative low-risk automated candidate approval + publication"
    )
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--election-id", default=None)
    parser.add_argument("--check-only", action="store_true",
                        help="evaluate only; never write DB, manifest or files")
    parser.add_argument("--skip-downstream", action="store_true",
                        help="skip the post-publication snapshot/coverage activation "
                             "(test / fault isolation only; refused unless "
                             "auto_publish.allow_skip_downstream=true)")
    # injectable for isolated / test environments (tmp_path)
    parser.add_argument("--project-root", default=None,
                        help="override config root so data/ paths resolve relative to it")
    parser.add_argument("--candidate-db", default=None)
    parser.add_argument("--formal-db", default=None)
    parser.add_argument("--seed-dir", default=None)
    parser.add_argument("--output-root", default=None)
    args = parser.parse_args()

    config = load_config(args.config)
    if args.project_root:
        config.root = Path(args.project_root)
    if args.formal_db:
        config.raw.setdefault("paths", {})["formal_db"] = str(args.formal_db)
    if args.seed_dir:
        seed_dir = Path(args.seed_dir)
        config.raw["paths"].update(
            {
                "events_seed": str(seed_dir / "events.jsonl"),
                "sources_seed": str(seed_dir / "sources.jsonl"),
                "initial_snapshot": str(seed_dir / "initial_snapshot.json"),
                "snapshot_history": str(seed_dir / "snapshot_history.jsonl"),
            }
        )
    if args.output_root:
        config.raw["paths"]["output_root"] = str(args.output_root)

    result = run_auto_publish(config, args)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if result.get("status") in ("blocked", "disabled"):
        sys.exit(2)
    if result.get("failed"):
        sys.exit(1)


if __name__ == "__main__":
    main()
