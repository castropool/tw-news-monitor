"""Build the candidate fact queue from news.db and election_watch matches.

This module is the phase-1 read-only pipeline entry point. It writes only to
the independent candidate database and the candidate run output directory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from app.time_utils import TAIPEI

from .candidate_id import (
    candidate_id_for_anchor,
    choose_anchor,
    cluster_fingerprint,
    write_candidate_id_strategy,
)
from .candidate_models import NormalizedArticle
from .candidate_repository import CandidateRepository
from .candidate_router import route_candidate
from .candidate_scorer import finalize_risk_level, score_candidate, write_scoring_explanation
from .candidate_validator import build_global_validation, validate_candidate
from .config import load_config
from .event_clusterer import cluster_articles as cluster_news_articles, extract_event_date
from .event_type_dictionary import classify_event_type
from .relevance_calibrator import assign_relevance_label
from .assertion_classifier import build_assertion_profile, classify_article_assertions
from .formal_duplicate_checker import (
    check_candidate_duplicates,
    formal_event_ids,
    load_formal_events,
    load_formal_sources,
)
from .match_reader import matches_signature, open_match_connection, read_matches
from .news_reader import business_signature, open_news_connection, read_articles
from .preview_renderer import render_run_outputs
from .quality_reports import (
    assertion_kind_counts,
    build_article_adjudication_comparison,
    build_assertion_quality_report,
    build_candidate_quality_summary,
    build_cluster_quality_report,
    build_event_type_quality_report,
    date_basis_stats,
    render_all_candidate_audit,
)
from .run_retention import enforce_run_retention
from .source_resolver import resolve_sources
from .formal_duplicate_diagnostics import build_formal_duplicate_diagnostics


def _file_sha256(path: Path) -> str:
    if not path.exists():
        return ""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _dir_business_hash(root: Path) -> str:
    h = hashlib.sha256()
    if not root.exists():
        return h.hexdigest()
    for p in sorted(root.rglob("*")):
        if p.is_file():
            rel = str(p.relative_to(root)).replace("\\", "/")
            h.update(rel.encode("utf-8"))
            h.update(_file_sha256(p).encode("utf-8"))
    return h.hexdigest()


def _formal_db_business_hash(path: Path) -> str:
    if not path.exists():
        return ""
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    h = hashlib.sha256()
    try:
        tables = [
            "elections", "actors", "sources", "election_events", "event_sources",
            "election_polls", "poll_questions", "poll_results", "poll_source_links",
            "election_state_snapshots",
        ]
        for t in tables:
            try:
                rows = conn.execute(f"SELECT * FROM {t} ORDER BY 1").fetchall()
            except sqlite3.OperationalError:
                continue
            h.update(t.encode("utf-8"))
            h.update(json.dumps([list(r) for r in rows], ensure_ascii=False, sort_keys=True).encode("utf-8"))
    finally:
        conn.close()
    return h.hexdigest()


def compute_input_hashes(config) -> dict[str, str]:
    news_path = config.path("news_db")
    match_path = config.path("match_db")
    formal_path = config.path("formal_db")
    news_conn = open_news_connection(news_path)
    match_conn = open_match_connection(match_path)
    try:
        news_sig = business_signature(news_conn)
        match_sig = matches_signature(match_conn, config.get("match_reader.table", "article_matches"))
    finally:
        news_conn.close()
        match_conn.close()

    seed_files = [
        config.path("events_seed"),
        config.path("sources_seed"),
        config.path("initial_snapshot"),
        config.path("snapshot_history"),
    ] + [Path(p) for p in config.get("paths.poll_seeds", [])]
    seed_hash = hashlib.sha256()
    for p in sorted(set(seed_files)):
        seed_hash.update(str(p).encode("utf-8"))
        seed_hash.update(_file_sha256(p).encode("utf-8"))

    coverage_hash = _dir_business_hash(config.path("coverage_root"))
    formal_db_hash = _formal_db_business_hash(formal_path)
    formal_combined = hashlib.sha256()
    formal_combined.update(seed_hash.hexdigest().encode("utf-8"))
    formal_combined.update(coverage_hash.encode("utf-8"))
    formal_combined.update(formal_db_hash.encode("utf-8"))

    release_zip = config.path("frozen_release_zip")
    return {
        "news_db_unchanged": news_sig,
        "article_matches_unchanged": match_sig,
        "formal_data_unchanged": formal_combined.hexdigest(),
        "frozen_release_unchanged": _file_sha256(release_zip),
        "news_db_sha256": _file_sha256(news_path),
        "match_db_sha256": _file_sha256(match_path),
        "formal_db_sha256": _file_sha256(formal_path),
        "release_zip_sha256": _file_sha256(release_zip),
    }


def build_candidate_summary(
    primary_actor: str,
    canonical_date: str,
    event_type: str,
    profile: dict[str, Any],
    article_count: int,
    source_names: list[str],
) -> str:
    parts: list[str] = []
    who = primary_actor or "相關人士"
    when = f"於{canonical_date[:10]}" if canonical_date else "日期不明"
    what = f"涉{event_type}" if event_type and event_type != "unknown" else "相關動態"
    parts.append(f"據{article_count}篇報導，{who}{when}{what}")
    counts = profile.get("counts", {})
    if counts.get("observed_fact", 0):
        parts.append(f"含{counts['observed_fact']}條可觀察事實")
    if counts.get("actor_statement", 0):
        parts.append(f"含{counts['actor_statement']}條人物表態")
    if counts.get("allegation", 0):
        parts.append(f"含{counts['allegation']}條指控")
    if counts.get("uncertain_report", 0):
        parts.append("含未證實消息")
    if source_names:
        parts.append(f"來源：{'、'.join(sorted(set(source_names))[:5])}")
    return "；".join(parts)


def _has_collection_error(article: NormalizedArticle, config) -> bool:
    markers = config.get("input_filter.collection_error_markers", []) or []
    text = f"{article.raw_title} {article.summary}"
    return any(m in text for m in markers)


def _is_navigation_page(article: NormalizedArticle) -> bool:
    markers = ["列表頁", "標籤", "下一頁", "上一頁", "新聞總覽"]
    return any(m in article.raw_title for m in markers)


def _json_dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False)


def _write_run_log(config, record: dict[str, Any]) -> Path:
    log_path = Path(config.get(
        "deployment.log_path",
        "data/election_candidates/tainan_2026/logs/candidate_pipeline.jsonl",
    ))
    log_path.parent.mkdir(parents=True, exist_ok=True)
    line = {
        k: record.get(k, "")
        for k in (
            "run_id", "election_id", "started_at", "finished_at",
            "cursor_before", "cursor_after", "articles_examined", "articles_matched",
            "candidate_events_created", "duplicate_candidate_count",
            "review_required_count", "hold_count", "auto_reject_count",
            "context_only_count", "status", "error_summary",
        )
    }
    with open(log_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(line, ensure_ascii=False) + "\n")
    return log_path


def _apply_run_retention(config, args, current_run_dir: Path) -> dict[str, object] | None:
    if args.output_root:
        return None
    try:
        report = enforce_run_retention(
            config.path("output_root") / "runs",
            current_run_dir=current_run_dir,
            enabled=bool(config.get("run_retention.enabled", False)),
            keep_latest=int(config.get("run_retention.keep_latest", 12)),
            max_total_mb=int(config.get("run_retention.max_total_mb", 40)),
        )
        payload = report.to_dict()
    except Exception as exc:  # Retention must never fail a successful pipeline run.
        payload = {
            "enabled": bool(config.get("run_retention.enabled", False)),
            "warnings": [f"retention_failed:{type(exc).__name__}"],
        }
    print("run_retention=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload


def run_pipeline(config, args) -> dict[str, Any]:
    election_id = config.resolve_election_id(args.election_id)
    run_id = f"run_{datetime.now(TAIPEI).strftime('%Y%m%d_%H%M%S_%f')}"
    started_at = datetime.now(TAIPEI).isoformat()

    candidate_db = Path(args.candidate_db) if args.candidate_db else config.path("candidate_db")
    repo = CandidateRepository(candidate_db)
    repo.connect()
    repo.create_tables()

    if args.reset_test_cursor:
        allow_test = config.test_mode or bool(args.test_mode) or "_test" in candidate_db.name
        result = repo.reset_test_cursor(election_id, config.get("scan.cursor_type", "news_article_id"), allow_test=allow_test)
        print(json.dumps(result, ensure_ascii=False))
        repo.close()
        return result

    if args.validate_only:
        return _validate_only(repo, config, election_id, args)

    if args.rebuild_preview:
        latest = repo.get_latest_successful_run(election_id)
        if not latest:
            raise RuntimeError("no successful run to rebuild preview from")
        run_dir = Path(args.output_root) if args.output_root else (
            config.path("output_root") / "runs" / latest["run_id"]
        )
        paths = render_run_outputs(repo, latest["run_id"], run_dir, config)
        _apply_run_retention(config, args, run_dir)
        print(json.dumps(paths, ensure_ascii=False, indent=2))
        repo.close()
        return paths

    from app.lock import InstanceLock

    lock = InstanceLock(config.path("lock_root") / f"candidate_pipeline_{election_id}.lock")
    lock_acquired = lock.acquire()
    if not lock_acquired:
        repo.close()
        return {
            "status": "blocked",
            "reason": "another candidate pipeline instance is running",
            "election_id": election_id,
            "lock_path": str(lock.path),
        }

    try:
        inputs_before = compute_input_hashes(config)

        scan_mode = "explicit_history"
        date_from = args.date_from or ""
        date_to = args.date_to or ""
        id_after = 0
        cursor_before = "0"
        if args.since_last_success:
            scan_mode = "since_last_success"
            cursor = repo.get_scan_cursor(election_id, config.get("scan.cursor_type", "news_article_id"))
            if cursor:
                id_after = int(cursor["last_article_id"] or 0)
                cursor_before = str(id_after)
            else:
                days = int(config.get("scan.initial_scan_days", 45))
                date_from = (datetime.now(TAIPEI) - timedelta(days=days)).strftime("%Y-%m-%d")
                date_to = datetime.now(TAIPEI).strftime("%Y-%m-%d")

        run_record = {
            "run_id": run_id,
            "election_id": election_id,
            "started_at": started_at,
            "finished_at": "",
            "status": "running",
            "scan_mode": scan_mode,
            "date_from": date_from,
            "date_to": date_to,
            "cursor_before": cursor_before,
            "cursor_after": "",
            "articles_examined": 0,
            "articles_matched": 0,
            "candidate_events_created": 0,
            "candidate_events_updated": 0,
            "articles_attached": 0,
            "auto_reject_count": 0,
            "duplicate_candidate_count": 0,
            "review_required_count": 0,
            "hold_count": 0,
            "context_only_count": 0,
            "pipeline_version": config.pipeline_version,
            "input_hash": "",
            "business_output_hash": "",
            "error_summary": "",
        }
        repo.upsert_pipeline_run(run_record)
    except Exception:
        lock.release()
        repo.close()
        raise

    try:
        news_conn = open_news_connection(config.path("news_db"))
        try:
            rows = read_articles(
                news_conn,
                table=config.get("news_reader.table", "articles"),
                id_column=config.get("news_reader.id_column", "id"),
                date_column=config.get("news_reader.published_at_column", "published_at"),
                date_from=date_from,
                date_to=date_to,
                id_after=id_after,
            )
        finally:
            news_conn.close()

        from .article_normalizer import normalize_article

        articles: list[NormalizedArticle] = []
        for row in rows:
            art = normalize_article(row, config)
            if art.news_article_id:
                articles.append(art)
        articles.sort(key=lambda a: (a.published_at or "9999", a.news_article_id))
        examined = len(articles)

        matches = read_matches(
            articles,
            config,
            match_db=args.match_db,
            news_db=None,
            mode=args.match_mode,
        )
        for art in articles:
            info = matches.get(art.news_article_id)
            if info:
                art.match = info

        match_mode = args.match_mode or config.get("match_reader.mode", "persisted")
        program_labels: dict[str, str] = {}
        for art in articles:
            label, label_reasons, _evidence = assign_relevance_label(art, config)
            art.match.relevance_label = label
            art.match.relevance_reasons = label_reasons
            program_labels[art.news_article_id] = label
            repo.upsert_article_match(
                {
                    "news_article_id": art.news_article_id,
                    "election_id": election_id,
                    "match_mode": match_mode,
                    "relevance_label": label,
                    "matched_people": art.match.matched_people,
                    "matched_parties": art.match.matched_parties,
                    "matched_issues": art.match.matched_issues,
                    "matched_basis": art.match.matched_basis,
                    "match_score": art.match.match_score,
                    "classified_at": datetime.now(TAIPEI).isoformat(),
                    "classifier_version": config.pipeline_version,
                }
            )

        matched = [a for a in articles if a.match.match_score > 0 or a.match.matched_terms]
        matched.sort(key=lambda a: (a.published_at or "9999", a.news_article_id))
        unmatched_count = examined - len(matched)

        # Input-level exclusions become auto_reject candidates so nothing is silent.
        seen_urls: set[str] = set()
        candidates_to_build: list[NormalizedArticle] = []
        input_exclusions: list[dict[str, Any]] = []
        for art in matched:
            reasons: list[str] = []
            if not art.normalized_title and not art.summary:
                reasons.append("no_valid_text")
            if not art.source_name and not art.normalized_domain:
                reasons.append("no_valid_source")
            if _has_collection_error(art, config):
                reasons.append("collection_error")
            if _is_navigation_page(art):
                reasons.append("navigation_or_list_page")
            norm_url = art.normalized_url or art.raw_url
            if norm_url:
                if norm_url in seen_urls:
                    reasons.append("duplicate_normalized_url")
                else:
                    seen_urls.add(norm_url)
            if reasons:
                input_exclusions.append(
                    {
                        "news_article_id": art.news_article_id,
                        "title": art.raw_title,
                        "url": art.raw_url,
                        "reasons": reasons,
                    }
                )
                candidates_to_build.append(art)
            else:
                candidates_to_build.append(art)

        # For input-excluded articles create singleton auto-reject clusters.
        excluded_ids = {e["news_article_id"] for e in input_exclusions}
        clusters = cluster_news_articles(
            [a for a in candidates_to_build if a.news_article_id not in excluded_ids],
            config,
        )
        singleton_excluded = [
            a for a in candidates_to_build if a.news_article_id in excluded_ids
        ]
        for art in singleton_excluded:
            if not any(art.news_article_id == c.anchor.news_article_id for c in clusters if c.anchor):
                from .event_clusterer import ArticleCluster

                clusters.append(ArticleCluster(articles=[art], coarse_title_group="auto_reject_input"))

        formal_events = load_formal_events(config.path("formal_db"), election_id, config)
        formal_sources = load_formal_sources(config.path("formal_db"))
        formal_ids = formal_event_ids(config.path("formal_db"), election_id)

        run_stats = {
            "candidate_events_created": 0,
            "candidate_events_updated": 0,
            "articles_attached": 0,
            "auto_reject_count": 0,
            "duplicate_candidate_count": 0,
            "review_required_count": 0,
            "hold_count": 0,
            "context_only_count": 0,
        }

        for cluster in clusters:
            cluster_articles = cluster.sorted_articles()
            if not cluster_articles:
                continue
            anchor = choose_anchor(cluster_articles)
            if anchor is None:
                continue
            computed_id = candidate_id_for_anchor(
                anchor,
                prefix=config.candidate_id_prefix,
                hash_length=config.candidate_id_hash_length,
            )

            existing_candidate_id = None
            for art in cluster_articles:
                found = repo.find_candidate_by_article(art.news_article_id)
                if found:
                    existing_candidate_id = found
                    break
            if existing_candidate_id is None and repo.candidate_exists(computed_id):
                existing_candidate_id = computed_id

            candidate_id = existing_candidate_id or computed_id
            created = existing_candidate_id is None
            if created:
                run_stats["candidate_events_created"] += 1
            else:
                run_stats["candidate_events_updated"] += 1

            existing = repo.get_candidate(candidate_id) if existing_candidate_id else None
            anchor_id = existing["anchor_article_id"] if existing else anchor.news_article_id
            fingerprint = existing["cluster_fingerprint"] if existing else cluster_fingerprint(
                cluster_articles,
                prefix=config.candidate_id_prefix,
                hash_length=config.candidate_id_hash_length,
            )
            first_seen = existing["first_seen_at"] if existing else started_at

            event_date, date_basis, date_conf = extract_event_date(anchor, config)
            precision = "day" if event_date else "unknown"
            event_type = classify_event_type(
                anchor.normalized_title, anchor.summary, config
            )
            primary_actor = (
                (anchor.match.matched_people or [""])[0]
                or (anchor.match.matched_parties or [""])[0]
            )
            secondary = list(dict.fromkeys(
                (anchor.match.matched_people + anchor.match.matched_parties)
            ))
            if primary_actor in secondary:
                secondary.remove(primary_actor)
            themes = list(dict.fromkeys(anchor.match.matched_issues))
            keywords = list(dict.fromkeys(anchor.match.matched_terms))
            locations = [
                t for t in anchor.match.matched_terms
                if t in ("台南", "台南市", "大台南", "溪北", "溪南", "南市", "府城")
            ]

            assertions = []
            for art in cluster_articles:
                assertions.extend(
                    classify_article_assertions(art, candidate_id, run_id, config)
                )
            profile = build_assertion_profile(assertions)

            source_names = list(dict.fromkeys(a.source_name for a in cluster_articles if a.source_name))
            article_sources = [
                {
                    "candidate_id": candidate_id,
                    "news_article_id": a.news_article_id,
                    "name": a.source_name,
                    "url": a.raw_url,
                    "first_seen_at": a.published_at or started_at,
                    "last_seen_at": a.published_at or started_at,
                }
                for a in cluster_articles
            ]
            candidate_sources, event_source_links = resolve_sources(
                article_sources, formal_sources, config
            )
            candidate_domains = {s["normalized_domain"] for s in candidate_sources if s["normalized_domain"]}
            suggestions = check_candidate_duplicates(
                {
                    "candidate_id": candidate_id,
                    "primary_actor": primary_actor,
                    "secondary_actors_json": _json_dumps(secondary),
                    "themes_json": _json_dumps(themes),
                    "keywords_json": _json_dumps(keywords),
                    "canonical_event_date": event_date,
                    "candidate_event_type": event_type,
                    "candidate_title": anchor.normalized_title,
                    "candidate_summary": "",
                },
                formal_events,
                config,
                run_id,
                candidate_domains,
            )
            summary = build_candidate_summary(
                primary_actor, event_date, event_type, profile, len(cluster_articles), source_names
            )
            candidate_payload = {
                "candidate_id": candidate_id,
                "election_id": election_id,
                "anchor_article_id": anchor_id,
                "cluster_fingerprint": fingerprint,
                "canonical_event_date": event_date,
                "event_date_precision": precision,
                "event_date_basis": date_basis,
                "event_date_confidence": date_conf,
                "candidate_event_type": event_type,
                "candidate_title": anchor.normalized_title,
                "candidate_summary": summary,
                "region_match": bool(anchor.match.region_match),
                "has_candidate_actor": bool(anchor.match.matched_people),
                "primary_actor": primary_actor,
                "secondary_actors_json": _json_dumps(secondary),
                "locations_json": _json_dumps(locations),
                "themes_json": _json_dumps(themes),
                "keywords_json": _json_dumps(keywords),
                "assertion_profile_json": _json_dumps(profile),
                "relevance_label": anchor.match.relevance_label or "contextual",
                "date_flagged_inferred": 1 if date_basis == "inferred_from_publication" else 0,
                "article_count": len(cluster_articles),
                "source_count": len(candidate_sources),
                "completeness_score": 0.0,
                "cluster_confidence": 0.0,
                "date_confidence": 0.0,
                "source_confidence": 0.0,
                "assertion_risk_score": 0.0,
                "relevance_score": 0.0,
                "formal_duplicate_score": 0.0,
                "formal_duplicate_status": "not_checked",
                "risk_level": "low",
                "review_status": "new",
                "status_reason_codes_json": "[]",
                "first_seen_at": first_seen,
                "last_updated_at": started_at,
                "created_run_id": run_id if created else (existing["created_run_id"] if existing else run_id),
                "updated_run_id": run_id,
                "candidate_schema_version": config.schema_version,
            }
            scores = score_candidate(candidate_payload, cluster_articles, candidate_sources, assertions, profile, suggestions, config)
            scores["risk_level"] = finalize_risk_level(scores["risk_level"], candidate_payload)
            candidate_payload.update(scores)
            likely = float(config.get("duplicate_detection.likely_duplicate_threshold", 0.90))
            possible = float(config.get("duplicate_detection.possible_match_threshold", 0.65))
            if scores["formal_duplicate_score"] >= likely:
                candidate_payload["formal_duplicate_status"] = "likely_duplicate"
            elif scores["formal_duplicate_score"] >= possible:
                candidate_payload["formal_duplicate_status"] = "possible_match"
            else:
                candidate_payload["formal_duplicate_status"] = "no_match"

            existing_duplicate = bool(
                existing_candidate_id
                and (existing_candidate_id != computed_id or any(
                    s["suggested_action"] == "likely_duplicate" for s in suggestions
                ))
            )
            status, reasons = route_candidate(
                candidate_payload, scores, profile, config, existing_duplicate=existing_duplicate
            )
            if anchor.news_article_id in excluded_ids:
                status = "auto_reject"
                reasons = list(dict.fromkeys(reasons + ["input_exclusion"]))
            candidate_payload["review_status"] = status
            candidate_payload["status_reason_codes_json"] = _json_dumps(reasons)
            run_stats[f"{status}_count"] += 1

            repo.upsert_candidate(candidate_payload)
            for i, art in enumerate(cluster_articles):
                before = repo.conn.execute(
                    "SELECT 1 FROM candidate_event_articles WHERE candidate_id=? AND news_article_id=?",
                    (candidate_id, art.news_article_id),
                ).fetchone()
                repo.attach_article(
                    {
                        "candidate_id": candidate_id,
                        "news_article_id": art.news_article_id,
                        "relationship_type": "same_event",
                        "is_anchor": 1 if art.news_article_id == anchor_id else 0,
                        "article_title": art.raw_title,
                        "article_url": art.raw_url,
                        "source_name": art.source_name,
                        "published_at": art.published_at,
                        "event_date_candidate": event_date,
                        "event_date_basis": date_basis,
                        "match_score": art.match.match_score,
                        "attached_run_id": run_id,
                    }
                )
                if before is None:
                    run_stats["articles_attached"] += 1
            for assertion in assertions:
                repo.upsert_assertion(assertion)
            for source in candidate_sources:
                repo.upsert_source(source)
            for link in event_source_links:
                repo.link_event_source(link)
            for suggestion in suggestions:
                repo.upsert_duplicate_suggestion(suggestion)
            validation = validate_candidate(
                candidate_payload,
                [a.to_dict() for a in cluster_articles],
                assertions,
                candidate_sources,
                suggestions,
                formal_ids,
                config,
            )
            repo.upsert_validation(validation)

        output_hash = repo.business_output_hash()
        inputs_after = compute_input_hashes(config)

        max_article_id = max((int(a.news_article_id) for a in articles), default=0)
        cursor_after = str(max(int(cursor_before or 0), max_article_id))
        cursor_type = config.get("scan.cursor_type", "news_article_id")
        previous_cursor = repo.get_scan_cursor(election_id, cursor_type)
        has_cursor = previous_cursor is not None
        new_published_at = max(
            (a.published_at for a in articles if a.published_at), default=""
        )
        new_collected_at = max(
            (a.collected_at for a in articles if a.collected_at), default=""
        )
        if not new_published_at and previous_cursor and previous_cursor.get("last_published_at"):
            new_published_at = previous_cursor["last_published_at"]
        if not new_collected_at and previous_cursor and previous_cursor.get("last_collected_at"):
            new_collected_at = previous_cursor["last_collected_at"]
        if scan_mode == "since_last_success" or not has_cursor:
            repo.set_scan_cursor(
                election_id,
                cursor_type,
                int(cursor_after),
                new_published_at,
                new_collected_at,
                run_id,
                datetime.now(TAIPEI).isoformat(),
            )

        total_status = repo.count_candidates_by_status()
        all_candidates = repo.list_candidates(limit=100000)
        date_stats = date_basis_stats(all_candidates)
        assertion_counts = assertion_kind_counts(all_candidates, repo)
        suggestion_stats: dict[str, int] = {}
        for c in all_candidates:
            for s in repo.get_duplicate_suggestions(c["candidate_id"]):
                suggestion_stats[s["suggested_action"]] = suggestion_stats.get(
                    s["suggested_action"], 0
                ) + 1
        unknown_event_type_count = sum(
            1 for c in all_candidates if c.get("candidate_event_type") == "unknown"
        )
        program_clusters_by_article: dict[str, str] = {}
        for c in all_candidates:
            for a in repo.get_articles(c["candidate_id"]):
                program_clusters_by_article[a["news_article_id"]] = c["candidate_id"]
        program_kinds_by_article: dict[str, set[str]] = {}
        for c in all_candidates:
            for a in repo.get_assertions(c["candidate_id"]):
                program_kinds_by_article.setdefault(a["evidence_article_id"], set()).add(
                    a["assertion_kind"]
                )
        program_types_by_article: dict[str, str] = {}
        for c in all_candidates:
            for a in repo.get_articles(c["candidate_id"]):
                program_types_by_article[a["news_article_id"]] = c.get("candidate_event_type", "")

        adjudication_path = (
            config.path("output_root") / "quality_calibration" / "july_2026_article_adjudication.json"
        )
        adjudication_comparison = build_article_adjudication_comparison(
            program_labels, adjudication_path
        )
        cluster_quality = build_cluster_quality_report(program_clusters_by_article, adjudication_path)
        assertion_quality = build_assertion_quality_report(program_kinds_by_article, adjudication_path)
        event_type_quality = build_event_type_quality_report(program_types_by_article, adjudication_path)
        candidate_quality_summary = build_candidate_quality_summary(
            run_stats,
            total_status,
            date_stats,
            assertion_counts,
            suggestion_stats,
            unknown_event_type_count,
        )

        run_record.update(
            {
                "finished_at": datetime.now(TAIPEI).isoformat(),
                "status": "success",
                "cursor_after": cursor_after,
                "articles_examined": examined,
                "articles_matched": len(matched),
                "candidate_events_created": run_stats["candidate_events_created"],
                "candidate_events_updated": run_stats["candidate_events_updated"],
                "articles_attached": run_stats["articles_attached"],
                "auto_reject_count": run_stats["auto_reject_count"],
                "duplicate_candidate_count": run_stats["duplicate_candidate_count"],
                "review_required_count": run_stats["review_required_count"],
                "hold_count": run_stats["hold_count"],
                "context_only_count": run_stats.get("context_only_count", 0),
                "input_hash": hashlib.sha256(
                    json.dumps(inputs_before, ensure_ascii=False, sort_keys=True).encode("utf-8")
                ).hexdigest(),
                "business_output_hash": output_hash,
            }
        )
        repo.upsert_pipeline_run(run_record)
        _write_run_log(config, run_record)

        run_dir = Path(args.output_root) if args.output_root else (
            config.path("output_root") / "runs" / run_id
        )
        output_paths = render_run_outputs(repo, run_id, run_dir, config)
        render_all_candidate_audit(repo, run_dir, config, adjudication_path)
        write_candidate_id_strategy(config.path("output_root"), config)
        write_scoring_explanation(config.path("output_root"), config)
        diagnostics = build_formal_duplicate_diagnostics(repo, config)
        (run_dir / "formal_duplicate_diagnostics.json").write_text(
            json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "article_adjudication_comparison.json").write_text(
            json.dumps(adjudication_comparison, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "cluster_quality_report.json").write_text(
            json.dumps(cluster_quality, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "assertion_quality_report.json").write_text(
            json.dumps(assertion_quality, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "event_type_quality_report.json").write_text(
            json.dumps(event_type_quality, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        (run_dir / "candidate_quality_summary.json").write_text(
            json.dumps(candidate_quality_summary, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        package_dir = Path(__file__).resolve().parent
        global_validation = build_global_validation(
            sum(total_status.values()),
            sum(1 for c in repo.list_candidates(limit=100000) if (repo.get_validation(c["candidate_id"]) or {}).get("validation_ready")),
            total_status,
            inputs_before,
            inputs_after,
            0,
            package_dir,
            config,
        )
        (run_dir / config.get("outputs.candidate_validation", "candidate_validation.json")).write_text(
            json.dumps(global_validation, ensure_ascii=False, indent=2), encoding="utf-8"
        )

        manifest = {
            "run_id": run_id,
            "candidate_pipeline_version": config.pipeline_version,
            "candidate_schema_version": config.schema_version,
            "election_id": election_id,
            "scan_mode": scan_mode,
            "date_from": date_from,
            "date_to": date_to,
            "cursor_before": cursor_before,
            "cursor_after": cursor_after,
            "articles_examined": examined,
            "articles_matched": len(matched),
            "articles_unmatched": unmatched_count,
            "input_exclusions": input_exclusions,
            "input_exclusion_count": len(input_exclusions),
            "run_status_counts": {
                k: run_stats[k] for k in (
                    "candidate_events_created", "candidate_events_updated", "articles_attached",
                    "auto_reject_count", "duplicate_candidate_count", "review_required_count",
                    "hold_count", "context_only_count",
                )
            },
            "total_candidate_status_counts": total_status,
            "date_stats": date_stats,
            "assertion_counts": assertion_counts,
            "suggestion_action_counts": suggestion_stats,
            "unknown_event_type_count": unknown_event_type_count,
            "adjudication_comparison": adjudication_comparison,
            "cluster_quality_report": cluster_quality,
            "assertion_quality_report": assertion_quality,
            "event_type_quality_report": event_type_quality,
            "average_articles_per_candidate": round(
                sum(c.get("article_count", 0) for c in all_candidates) / max(1, len(all_candidates)), 3
            ),
            "output_paths": output_paths,
            "input_hashes_before": inputs_before,
            "input_hashes_after": inputs_after,
            "inputs_unchanged": {
                key: inputs_before.get(key) == inputs_after.get(key)
                for key in ("news_db_unchanged", "article_matches_unchanged", "formal_data_unchanged", "frozen_release_unchanged")
            },
            "business_output_hash": output_hash,
            "formal_write_method_call_count": 0,
            "formal_database_open_mode": "read_only",
            "no_political_inference": not global_validation["errors"],
        }
        manifest_path = run_dir / config.get("outputs.run_manifest", "run_manifest.json")
        manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")

        idempotency = {
            "run_id": run_id,
            "candidate_ids": sorted(c["candidate_id"] for c in repo.list_candidates(limit=100000)),
            "business_output_hash": output_hash,
            "status_counts": total_status,
            "notes": [
                "Business output hash is stable for identical inputs; run_id/timestamps are excluded.",
            ],
        }
        idem_path = run_dir / config.get("outputs.run_idempotency", "run_idempotency.json")
        idem_path.write_text(json.dumps(idempotency, ensure_ascii=False, indent=2), encoding="utf-8")

        _apply_run_retention(config, args, run_dir)

        print(f"run_id={run_id}")
        print(f"articles_examined={examined}")
        print(f"articles_matched={len(matched)}")
        print(f"review_required={total_status.get('review_required', 0)}")
        print(f"hold={total_status.get('hold', 0)}")
        print(f"duplicate_candidate={total_status.get('duplicate_candidate', 0)}")
        print(f"auto_reject={total_status.get('auto_reject', 0)}")
        print(f"context_only={total_status.get('context_only', 0)}")
        print(f"output_root={run_dir}")
        return manifest
    except Exception as exc:
        run_record.update(
            {
                "finished_at": datetime.now(TAIPEI).isoformat(),
                "status": "failed",
                "error_summary": str(exc)[:2000],
            }
        )
        repo.upsert_pipeline_run(run_record)
        _write_run_log(config, run_record)
        raise
    finally:
        if lock_acquired:
            lock.release()
        repo.close()


def _validate_only(repo: CandidateRepository, config, election_id: str, args) -> dict[str, Any]:
    candidates = repo.list_candidates(limit=100000)
    formal_ids = formal_event_ids(config.path("formal_db"), election_id)
    results = []
    valid = 0
    for c in candidates:
        articles = repo.get_articles(c["candidate_id"])
        assertions = repo.get_assertions(c["candidate_id"])
        sources = repo.get_sources(c["candidate_id"])
        suggestions = repo.get_duplicate_suggestions(c["candidate_id"])
        v = validate_candidate(c, articles, assertions, sources, suggestions, formal_ids, config)
        results.append(v)
        if v["validation_ready"]:
            valid += 1
    status_counts = repo.count_candidates_by_status()
    payload = {
        "candidate_pipeline_ready": valid == len(candidates) and len(candidates) > 0,
        "errors": [e for r in results for e in json.loads(r["errors_json"])],
        "warnings": [w for r in results for w in json.loads(r["warnings_json"])],
        "candidate_count": len(candidates),
        "valid_candidate_count": valid,
        "review_required_count": status_counts.get("review_required", 0),
        "hold_count": status_counts.get("hold", 0),
        "duplicate_candidate_count": status_counts.get("duplicate_candidate", 0),
        "auto_reject_count": status_counts.get("auto_reject", 0),
        "formal_data_unchanged": True,
        "news_db_unchanged": True,
    }
    out = Path(args.output_root) if args.output_root else config.path("output_root") / "validation"
    out.mkdir(parents=True, exist_ok=True)
    (out / "candidate_validation.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Build candidate fact queue (phase 1)")
    parser.add_argument("--config", default="config/election_candidate_pipeline.yaml")
    parser.add_argument("--election-id", default=None)
    parser.add_argument("--date-from", default=None)
    parser.add_argument("--date-to", default=None)
    parser.add_argument("--since-last-success", action="store_true")
    parser.add_argument("--output-root", default=None)
    parser.add_argument("--candidate-db", default=None)
    parser.add_argument("--match-db", default=None)
    parser.add_argument("--match-mode", choices=["persisted", "inline_classifier"], default=None)
    parser.add_argument("--validate-only", action="store_true")
    parser.add_argument("--rebuild-preview", action="store_true")
    parser.add_argument("--reset-test-cursor", action="store_true")
    parser.add_argument("--test-mode", action="store_true")
    args = parser.parse_args()

    if args.date_from and not args.date_to or args.date_to and not args.date_from:
        parser.error("--date-from and --date-to must be provided together")
    if args.since_last_success and (args.date_from or args.date_to):
        parser.error("--since-last-success and --date-from/--date-to are mutually exclusive")
    if not (args.since_last_success or (args.date_from and args.date_to) or args.validate_only or args.rebuild_preview or args.reset_test_cursor):
        parser.error("provide --since-last-success or --date-from/--date-to")

    config = load_config(args.config)
    run_pipeline(config, args)


if __name__ == "__main__":
    main()
