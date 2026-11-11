"""SQLite repository for the independent candidate fact pipeline database."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

# Terminal review statuses: candidates in these states must never silently
# return to the auto-approval pool via a bulk refresh upsert.
TERMINAL_REVIEW_STATUSES = {"published", "rolled_back", "publication_failed"}


CREATE_PIPELINE_RUNS = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    run_id TEXT PRIMARY KEY,
    election_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    scan_mode TEXT NOT NULL,
    date_from TEXT,
    date_to TEXT,
    cursor_before TEXT,
    cursor_after TEXT,
    articles_examined INTEGER DEFAULT 0,
    articles_matched INTEGER DEFAULT 0,
    candidate_events_created INTEGER DEFAULT 0,
    candidate_events_updated INTEGER DEFAULT 0,
    articles_attached INTEGER DEFAULT 0,
    auto_reject_count INTEGER DEFAULT 0,
    duplicate_candidate_count INTEGER DEFAULT 0,
    review_required_count INTEGER DEFAULT 0,
    hold_count INTEGER DEFAULT 0,
    context_only_count INTEGER DEFAULT 0,
    pipeline_version TEXT NOT NULL,
    input_hash TEXT,
    business_output_hash TEXT,
    error_summary TEXT
)
"""

CREATE_SCAN_CURSORS = """
CREATE TABLE IF NOT EXISTS scan_cursors (
    election_id TEXT NOT NULL,
    cursor_type TEXT NOT NULL,
    last_article_id INTEGER NOT NULL DEFAULT 0,
    last_published_at TEXT,
    last_collected_at TEXT,
    last_successful_run_id TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (election_id, cursor_type)
)
"""

CREATE_CANDIDATE_EVENTS = """
CREATE TABLE IF NOT EXISTS candidate_events (
    candidate_id TEXT PRIMARY KEY,
    election_id TEXT NOT NULL,
    anchor_article_id TEXT NOT NULL,
    cluster_fingerprint TEXT NOT NULL,
    canonical_event_date TEXT,
    event_date_precision TEXT NOT NULL,
    event_date_basis TEXT NOT NULL DEFAULT '',
    event_date_confidence TEXT NOT NULL,
    candidate_event_type TEXT NOT NULL,
    candidate_title TEXT NOT NULL,
    candidate_summary TEXT,
    primary_actor TEXT,
    secondary_actors_json TEXT,
    locations_json TEXT,
    themes_json TEXT,
    keywords_json TEXT,
    assertion_profile_json TEXT,
    article_count INTEGER DEFAULT 0,
    source_count INTEGER DEFAULT 0,
    relevance_score REAL DEFAULT 0,
    completeness_score REAL DEFAULT 0,
    cluster_confidence REAL DEFAULT 0,
    date_confidence REAL DEFAULT 0,
    source_confidence REAL DEFAULT 0,
    assertion_risk_score REAL DEFAULT 0,
    formal_duplicate_score REAL DEFAULT 0,
    formal_duplicate_status TEXT NOT NULL DEFAULT 'not_checked',
    risk_level TEXT NOT NULL,
    review_status TEXT NOT NULL,
    status_reason_codes_json TEXT NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_updated_at TEXT NOT NULL,
    created_run_id TEXT NOT NULL,
    updated_run_id TEXT NOT NULL,
    candidate_schema_version TEXT NOT NULL
)
"""

CREATE_CANDIDATE_EVENT_ARTICLES = """
CREATE TABLE IF NOT EXISTS candidate_event_articles (
    candidate_id TEXT NOT NULL,
    news_article_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    is_anchor INTEGER NOT NULL DEFAULT 0,
    article_title TEXT,
    article_url TEXT,
    source_name TEXT,
    published_at TEXT,
    event_date_candidate TEXT,
    event_date_basis TEXT,
    match_score REAL DEFAULT 0,
    attached_run_id TEXT NOT NULL,
    PRIMARY KEY (candidate_id, news_article_id),
    FOREIGN KEY (candidate_id) REFERENCES candidate_events(candidate_id) ON DELETE CASCADE
)
"""

CREATE_CANDIDATE_ASSERTIONS = """
CREATE TABLE IF NOT EXISTS candidate_assertions (
    assertion_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    assertion_kind TEXT NOT NULL,
    assertion_text TEXT NOT NULL,
    subject TEXT,
    predicate TEXT,
    object_text TEXT,
    speaker TEXT,
    evidence_article_id TEXT NOT NULL,
    evidence_field TEXT,
    evidence_text TEXT,
    confidence REAL DEFAULT 0,
    risk_flags_json TEXT,
    source_clause TEXT,
    classification_reasons_json TEXT,
    created_run_id TEXT NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES candidate_events(candidate_id) ON DELETE CASCADE
)
"""

CREATE_CANDIDATE_ARTICLE_MATCHES = """
CREATE TABLE IF NOT EXISTS candidate_article_matches (
    news_article_id TEXT NOT NULL,
    election_id TEXT NOT NULL,
    match_mode TEXT NOT NULL,
    relevance_label TEXT NOT NULL,
    matched_people_json TEXT,
    matched_parties_json TEXT,
    matched_issues_json TEXT,
    matched_basis_json TEXT,
    match_score REAL DEFAULT 0,
    classified_at TEXT,
    classifier_version TEXT,
    PRIMARY KEY (news_article_id, election_id, match_mode)
)
"""

CREATE_REVIEW_DECISIONS = """
CREATE TABLE IF NOT EXISTS review_decisions (
    review_decision_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    decision TEXT NOT NULL,
    reviewer TEXT NOT NULL,
    reviewed_at TEXT NOT NULL,
    review_reason TEXT,
    edited_event_payload_json TEXT,
    target_formal_event_id TEXT,
    source_resolution_json TEXT,
    decision_version TEXT NOT NULL,
    candidate_business_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES candidate_events(candidate_id) ON DELETE CASCADE
)
"""

CREATE_PUBLICATION_BATCHES = """
CREATE TABLE IF NOT EXISTS publication_batches (
    batch_id TEXT PRIMARY KEY,
    election_id TEXT NOT NULL,
    created_at TEXT NOT NULL,
    created_by TEXT NOT NULL,
    status TEXT NOT NULL,
    formal_data_hash_before TEXT,
    candidate_hashes_json TEXT,
    review_decision_ids_json TEXT,
    new_event_count INTEGER DEFAULT 0,
    existing_event_attachment_count INTEGER DEFAULT 0,
    new_source_count INTEGER DEFAULT 0,
    new_event_source_link_count INTEGER DEFAULT 0,
    preview_ready INTEGER DEFAULT 0,
    validation_ready INTEGER DEFAULT 0,
    backup_ready INTEGER DEFAULT 0,
    staging_ready INTEGER DEFAULT 0,
    commit_ready INTEGER DEFAULT 0,
    commit_completed INTEGER DEFAULT 0,
    committed_at TEXT,
    rolled_back_at TEXT,
    error_summary TEXT
)
"""

CREATE_PUBLICATION_ITEMS = """
CREATE TABLE IF NOT EXISTS publication_items (
    publication_item_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    candidate_id TEXT NOT NULL,
    review_decision_id TEXT NOT NULL,
    operation_type TEXT NOT NULL,
    allocated_event_id TEXT,
    target_event_id TEXT,
    payload_hash TEXT,
    status TEXT NOT NULL,
    error TEXT,
    FOREIGN KEY (batch_id) REFERENCES publication_batches(batch_id) ON DELETE CASCADE
)
"""

CREATE_PUBLICATION_AUDIT_LOG = """
CREATE TABLE IF NOT EXISTS publication_audit_log (
    audit_id TEXT PRIMARY KEY,
    batch_id TEXT,
    candidate_id TEXT,
    review_decision_id TEXT,
    reviewer TEXT,
    action TEXT NOT NULL,
    event_id TEXT,
    source_ids_json TEXT,
    timestamp TEXT NOT NULL,
    formal_hash_before TEXT,
    formal_hash_after TEXT,
    result TEXT NOT NULL,
    reason TEXT
)
"""

CREATE_DOWNSTREAM_REFRESH_BATCHES = """
CREATE TABLE IF NOT EXISTS downstream_refresh_batches (
    refresh_batch_id TEXT PRIMARY KEY,
    publication_batch_id TEXT NOT NULL UNIQUE,
    election_id TEXT NOT NULL,
    formal_state_hash TEXT,
    previous_coverage_version TEXT,
    previous_snapshot_id TEXT,
    requested_period_start TEXT,
    requested_period_end TEXT,
    status TEXT NOT NULL,
    coverage_refresh_required INTEGER DEFAULT 0,
    snapshot_refresh_required INTEGER DEFAULT 0,
    assessment_refresh_required INTEGER DEFAULT 0,
    coverage_result TEXT,
    snapshot_result TEXT,
    assessment_trigger_result TEXT,
    created_at TEXT NOT NULL,
    started_at TEXT,
    finished_at TEXT,
    error_summary TEXT
)
"""

CREATE_ASSESSMENT_TRIGGER_QUEUE = """
CREATE TABLE IF NOT EXISTS assessment_trigger_queue (
    trigger_id TEXT PRIMARY KEY,
    election_id TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    refresh_batch_id TEXT,
    formal_state_hash TEXT,
    coverage_version TEXT,
    facts_cutoff TEXT,
    snapshot_id TEXT,
    trigger_reason TEXT,
    status TEXT NOT NULL,
    created_at TEXT NOT NULL,
    eligible_at TEXT,
    started_at TEXT,
    finished_at TEXT,
    assessment_run_id TEXT,
    error_summary TEXT,
    UNIQUE (election_id, period_start, period_end, formal_state_hash)
)
"""

CREATE_DAILY_REVIEW_COMPLETION = """
CREATE TABLE IF NOT EXISTS daily_review_completion (
    election_id TEXT NOT NULL,
    review_date TEXT NOT NULL,
    review_status TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    completed_by TEXT NOT NULL,
    candidate_total INTEGER DEFAULT 0,
    resolved_count INTEGER DEFAULT 0,
    unresolved_count INTEGER DEFAULT 0,
    material_event_count INTEGER DEFAULT 0,
    no_material_event INTEGER DEFAULT 0,
    candidate_cursor_at_completion INTEGER DEFAULT 0,
    business_hash TEXT,
    PRIMARY KEY (election_id, review_date)
)
"""

CREATE_CANDIDATE_SOURCES = """
CREATE TABLE IF NOT EXISTS candidate_sources (
    candidate_source_id TEXT PRIMARY KEY,
    normalized_source_name TEXT NOT NULL,
    normalized_domain TEXT,
    original_source_names_json TEXT,
    formal_source_id TEXT,
    formal_match_status TEXT NOT NULL,
    formal_match_basis TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    UNIQUE (normalized_source_name, normalized_domain)
)
"""

CREATE_CANDIDATE_EVENT_SOURCES = """
CREATE TABLE IF NOT EXISTS candidate_event_sources (
    candidate_id TEXT NOT NULL,
    candidate_source_id TEXT NOT NULL,
    news_article_id TEXT NOT NULL,
    relationship_type TEXT NOT NULL,
    PRIMARY KEY (candidate_id, candidate_source_id, news_article_id),
    FOREIGN KEY (candidate_id) REFERENCES candidate_events(candidate_id) ON DELETE CASCADE,
    FOREIGN KEY (candidate_source_id) REFERENCES candidate_sources(candidate_source_id) ON DELETE CASCADE
)
"""

CREATE_FORMAL_DUPLICATE_SUGGESTIONS = """
CREATE TABLE IF NOT EXISTS formal_duplicate_suggestions (
    suggestion_id TEXT PRIMARY KEY,
    candidate_id TEXT NOT NULL,
    formal_event_id TEXT NOT NULL,
    similarity_score REAL DEFAULT 0,
    date_score REAL DEFAULT 0,
    actor_score REAL DEFAULT 0,
    event_type_score REAL DEFAULT 0,
    keyword_score REAL DEFAULT 0,
    source_overlap_score REAL DEFAULT 0,
    matching_reasons_json TEXT,
    conflicting_reasons_json TEXT,
    suggested_action TEXT NOT NULL,
    created_run_id TEXT NOT NULL,
    FOREIGN KEY (candidate_id) REFERENCES candidate_events(candidate_id) ON DELETE CASCADE
)
"""

CREATE_CANDIDATE_VALIDATION_RESULTS = """
CREATE TABLE IF NOT EXISTS candidate_validation_results (
    candidate_id TEXT PRIMARY KEY,
    validation_ready INTEGER NOT NULL DEFAULT 0,
    errors_json TEXT,
    warnings_json TEXT,
    checked_at TEXT,
    validator_version TEXT,
    FOREIGN KEY (candidate_id) REFERENCES candidate_events(candidate_id) ON DELETE CASCADE
)
"""


class CandidateRepository:
    def __init__(self, db_path: str | Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = None

    def connect(self):
        self.conn = sqlite3.connect(str(self.db_path))
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.execute("PRAGMA busy_timeout=5000")
        self.conn.execute("PRAGMA journal_mode=WAL")

    def create_tables(self):
        if self.conn is None:
            raise RuntimeError("connect() first")
        for ddl in [
            CREATE_PIPELINE_RUNS,
            CREATE_SCAN_CURSORS,
            CREATE_CANDIDATE_EVENTS,
            CREATE_CANDIDATE_EVENT_ARTICLES,
            CREATE_CANDIDATE_ASSERTIONS,
            CREATE_CANDIDATE_SOURCES,
            CREATE_CANDIDATE_EVENT_SOURCES,
            CREATE_FORMAL_DUPLICATE_SUGGESTIONS,
            CREATE_CANDIDATE_VALIDATION_RESULTS,
            CREATE_CANDIDATE_ARTICLE_MATCHES,
            CREATE_REVIEW_DECISIONS,
            CREATE_PUBLICATION_BATCHES,
            CREATE_PUBLICATION_ITEMS,
            CREATE_PUBLICATION_AUDIT_LOG,
            CREATE_DOWNSTREAM_REFRESH_BATCHES,
            CREATE_ASSESSMENT_TRIGGER_QUEUE,
            CREATE_DAILY_REVIEW_COMPLETION,
        ]:
            self.conn.execute(ddl)
        self._migrate_columns()
        self.conn.commit()

    def _migrate_columns(self):
        assertion_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(candidate_assertions)").fetchall()}
        if "source_clause" not in assertion_cols:
            self.conn.execute("ALTER TABLE candidate_assertions ADD COLUMN source_clause TEXT")
        if "classification_reasons_json" not in assertion_cols:
            self.conn.execute("ALTER TABLE candidate_assertions ADD COLUMN classification_reasons_json TEXT")
        event_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(candidate_events)").fetchall()}
        if "relevance_label" not in event_cols:
            self.conn.execute("ALTER TABLE candidate_events ADD COLUMN relevance_label TEXT DEFAULT ''")
        if "date_flagged_inferred" not in event_cols:
            self.conn.execute("ALTER TABLE candidate_events ADD COLUMN date_flagged_inferred INTEGER DEFAULT 0")
        run_cols = {r[1] for r in self.conn.execute("PRAGMA table_info(pipeline_runs)").fetchall()}
        if "context_only_count" not in run_cols:
            self.conn.execute("ALTER TABLE pipeline_runs ADD COLUMN context_only_count INTEGER DEFAULT 0")

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def upsert_pipeline_run(self, run: dict[str, Any]):
        cols = [
            "run_id", "election_id", "started_at", "finished_at", "status", "scan_mode",
            "date_from", "date_to", "cursor_before", "cursor_after", "articles_examined",
            "articles_matched", "candidate_events_created", "candidate_events_updated",
            "articles_attached", "auto_reject_count", "duplicate_candidate_count",
            "review_required_count", "hold_count", "context_only_count", "pipeline_version", "input_hash",
            "business_output_hash", "error_summary",
        ]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "run_id")
        self.conn.execute(
            f"INSERT INTO pipeline_runs ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(run_id) DO UPDATE SET {updates}",
            [run.get(c, "") for c in cols],
        )
        self.conn.commit()

    def set_scan_cursor(
        self,
        election_id: str,
        cursor_type: str,
        last_article_id: int,
        last_published_at: str,
        last_collected_at: str,
        last_successful_run_id: str,
        updated_at: str,
    ):
        self.conn.execute(
            """INSERT INTO scan_cursors
               (election_id, cursor_type, last_article_id, last_published_at,
                last_collected_at, last_successful_run_id, updated_at)
               VALUES (?,?,?,?,?,?,?)
               ON CONFLICT(election_id, cursor_type) DO UPDATE SET
                 last_article_id=excluded.last_article_id,
                 last_published_at=excluded.last_published_at,
                 last_collected_at=excluded.last_collected_at,
                 last_successful_run_id=excluded.last_successful_run_id,
                 updated_at=excluded.updated_at""",
            (
                election_id,
                cursor_type,
                last_article_id,
                last_published_at,
                last_collected_at,
                last_successful_run_id,
                updated_at,
            ),
        )
        self.conn.commit()

    def get_scan_cursor(self, election_id: str, cursor_type: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM scan_cursors WHERE election_id=? AND cursor_type=?",
            (election_id, cursor_type),
        ).fetchone()
        return dict(row) if row else None

    def reset_test_cursor(self, election_id: str, cursor_type: str, allow_test: bool = False) -> dict[str, Any]:
        if not allow_test:
            raise PermissionError(
                "--reset-test-cursor is only allowed on a test database or with explicit test mode"
            )
        self.conn.execute(
            "DELETE FROM scan_cursors WHERE election_id=? AND cursor_type=?",
            (election_id, cursor_type),
        )
        self.conn.commit()
        return {"reset": True, "election_id": election_id, "cursor_type": cursor_type}

    def upsert_candidate(self, candidate: dict[str, Any], *, preserve_terminal_status: bool = True):
        """Upsert a candidate row.

        ``preserve_terminal_status=True`` (default) protects candidates in a
        terminal state (published / rolled_back / publication_failed) from
        being downgraded by bulk pipeline refreshes: their ``review_status``
        and ``status_reason_codes_json`` are kept untouched.  Explicit state
        machine transitions must pass ``preserve_terminal_status=False`` so
        that ``apply_status`` can still move candidates (e.g. retry paths).
        """
        existing_row = self.conn.execute(
            "SELECT review_status, status_reason_codes_json FROM candidate_events "
            "WHERE candidate_id=?",
            (candidate["candidate_id"],),
        ).fetchone()
        if (
            preserve_terminal_status
            and existing_row is not None
            and existing_row["review_status"] in TERMINAL_REVIEW_STATUSES
        ):
            candidate = dict(candidate)
            candidate["review_status"] = existing_row["review_status"]
            candidate["status_reason_codes_json"] = existing_row["status_reason_codes_json"]
        cols = [
            "candidate_id", "election_id", "anchor_article_id", "cluster_fingerprint",
            "canonical_event_date", "event_date_precision", "event_date_basis",
            "event_date_confidence", "candidate_event_type", "candidate_title",
            "candidate_summary", "primary_actor", "secondary_actors_json",
            "locations_json", "themes_json", "keywords_json", "assertion_profile_json",
            "article_count", "source_count", "relevance_score", "completeness_score", "cluster_confidence",
            "date_confidence", "source_confidence", "assertion_risk_score",
            "formal_duplicate_score", "formal_duplicate_status", "risk_level",
            "review_status", "status_reason_codes_json", "first_seen_at",
            "last_updated_at", "created_run_id", "updated_run_id",
            "relevance_label", "date_flagged_inferred", "candidate_schema_version",
        ]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("candidate_id", "first_seen_at", "created_run_id", "anchor_article_id", "cluster_fingerprint"))
        self.conn.execute(
            f"INSERT INTO candidate_events ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(candidate_id) DO UPDATE SET {updates}",
            [candidate.get(c, "") for c in cols],
        )
        self.conn.commit()

    def attach_article(self, link: dict[str, Any]):
        self.conn.execute(
            """INSERT OR IGNORE INTO candidate_event_articles
               (candidate_id, news_article_id, relationship_type, is_anchor,
                article_title, article_url, source_name, published_at,
                event_date_candidate, event_date_basis, match_score, attached_run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                link["candidate_id"],
                link["news_article_id"],
                link.get("relationship_type", "same_event"),
                int(bool(link.get("is_anchor"))),
                link.get("article_title", ""),
                link.get("article_url", ""),
                link.get("source_name", ""),
                link.get("published_at", ""),
                link.get("event_date_candidate", ""),
                link.get("event_date_basis", ""),
                float(link.get("match_score", 0)),
                link.get("attached_run_id", ""),
            ),
        )
        self.conn.commit()

    def upsert_assertion(self, assertion: dict[str, Any]):
        self.conn.execute(
            """INSERT OR REPLACE INTO candidate_assertions
               (assertion_id, candidate_id, assertion_kind, assertion_text,
                subject, predicate, object_text, speaker, evidence_article_id,
                evidence_field, evidence_text, confidence, risk_flags_json,
                source_clause, classification_reasons_json, created_run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                assertion["assertion_id"],
                assertion["candidate_id"],
                assertion["assertion_kind"],
                assertion["assertion_text"],
                assertion.get("subject", ""),
                assertion.get("predicate", ""),
                assertion.get("object_text", ""),
                assertion.get("speaker", ""),
                assertion["evidence_article_id"],
                assertion.get("evidence_field", ""),
                assertion.get("evidence_text", ""),
                float(assertion.get("confidence", 0)),
                assertion.get("risk_flags_json", "[]"),
                assertion.get("source_clause", ""),
                assertion.get("classification_reasons_json", "[]"),
                assertion.get("created_run_id", ""),
            ),
        )
        self.conn.commit()

    def upsert_article_match(self, match: dict[str, Any]):
        self.conn.execute(
            """INSERT INTO candidate_article_matches
               (news_article_id, election_id, match_mode, relevance_label,
                matched_people_json, matched_parties_json, matched_issues_json,
                matched_basis_json, match_score, classified_at, classifier_version)
               VALUES (?,?,?,?,?,?,?,?,?,?,?)
               ON CONFLICT(news_article_id, election_id, match_mode) DO UPDATE SET
                 relevance_label=excluded.relevance_label,
                 matched_people_json=excluded.matched_people_json,
                 matched_parties_json=excluded.matched_parties_json,
                 matched_issues_json=excluded.matched_issues_json,
                 matched_basis_json=excluded.matched_basis_json,
                 match_score=excluded.match_score,
                 classified_at=excluded.classified_at,
                 classifier_version=excluded.classifier_version""",
            (
                match["news_article_id"],
                match["election_id"],
                match["match_mode"],
                match["relevance_label"],
                json.dumps(match.get("matched_people", []), ensure_ascii=False),
                json.dumps(match.get("matched_parties", []), ensure_ascii=False),
                json.dumps(match.get("matched_issues", []), ensure_ascii=False),
                json.dumps(match.get("matched_basis", []), ensure_ascii=False),
                float(match.get("match_score", 0)),
                match.get("classified_at", ""),
                match.get("classifier_version", "0.2.0"),
            ),
        )
        self.conn.commit()

    def get_article_match(self, news_article_id: str, election_id: str, match_mode: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM candidate_article_matches WHERE news_article_id=? AND election_id=? AND match_mode=?",
            (news_article_id, election_id, match_mode),
        ).fetchone()
        return dict(row) if row else None

    # ---- Phase 2: review / publication ----

    def insert_review_decision(self, decision: dict[str, Any]):
        """Append-only review decision insert."""
        self.conn.execute(
            """INSERT INTO review_decisions
               (review_decision_id, candidate_id, decision, reviewer, reviewed_at,
                review_reason, edited_event_payload_json, target_formal_event_id,
                source_resolution_json, decision_version, candidate_business_hash, created_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                decision["review_decision_id"],
                decision["candidate_id"],
                decision["decision"],
                decision["reviewer"],
                decision.get("reviewed_at", ""),
                decision.get("review_reason", ""),
                decision.get("edited_event_payload_json", "{}"),
                decision.get("target_formal_event_id", ""),
                decision.get("source_resolution_json", "{}"),
                decision.get("decision_version", "0.1.0"),
                decision.get("candidate_business_hash", ""),
                decision.get("created_at", ""),
            ),
        )
        self.conn.commit()

    def list_review_decisions(self, candidate_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM review_decisions WHERE candidate_id=? ORDER BY created_at, review_decision_id",
            (candidate_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_review_decision(self, review_decision_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM review_decisions WHERE review_decision_id=?",
            (review_decision_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_latest_review_decision(self, candidate_id: str) -> dict[str, Any] | None:
        rows = self.list_review_decisions(candidate_id)
        return rows[-1] if rows else None

    def upsert_publication_batch(self, batch: dict[str, Any]):
        cols = [
            "batch_id", "election_id", "created_at", "created_by", "status",
            "formal_data_hash_before", "candidate_hashes_json", "review_decision_ids_json",
            "new_event_count", "existing_event_attachment_count", "new_source_count",
            "new_event_source_link_count", "preview_ready", "validation_ready",
            "backup_ready", "staging_ready", "commit_ready", "commit_completed",
            "committed_at", "rolled_back_at", "error_summary",
        ]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "batch_id")
        self.conn.execute(
            f"INSERT INTO publication_batches ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(batch_id) DO UPDATE SET {updates}",
            [batch.get(c, "") for c in cols],
        )
        self.conn.commit()

    def get_publication_batch(self, batch_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM publication_batches WHERE batch_id=?", (batch_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_publication_batches(self, status: str | None = None, limit: int = 200) -> list[dict[str, Any]]:
        clauses: list[str] = ["1=1"]
        params: list[Any] = []
        if status:
            clauses.append("status=?")
            params.append(status)
        params.append(int(limit))
        rows = self.conn.execute(
            f"SELECT * FROM publication_batches WHERE {' AND '.join(clauses)} "
            "ORDER BY created_at DESC, batch_id LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def insert_publication_item(self, item: dict[str, Any]):
        self.conn.execute(
            """INSERT INTO publication_items
               (publication_item_id, batch_id, candidate_id, review_decision_id,
                operation_type, allocated_event_id, target_event_id, payload_hash,
                status, error)
               VALUES (?,?,?,?,?,?,?,?,?,?)""",
            (
                item["publication_item_id"],
                item["batch_id"],
                item["candidate_id"],
                item["review_decision_id"],
                item["operation_type"],
                item.get("allocated_event_id", ""),
                item.get("target_event_id", ""),
                item.get("payload_hash", ""),
                item.get("status", "pending"),
                item.get("error", ""),
            ),
        )
        self.conn.commit()

    def list_publication_items(self, batch_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM publication_items WHERE batch_id=? ORDER BY publication_item_id",
            (batch_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def append_publication_audit(self, audit: dict[str, Any]):
        self.conn.execute(
            """INSERT INTO publication_audit_log
               (audit_id, batch_id, candidate_id, review_decision_id, reviewer,
                action, event_id, source_ids_json, timestamp,
                formal_hash_before, formal_hash_after, result, reason)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                audit["audit_id"],
                audit.get("batch_id", ""),
                audit.get("candidate_id", ""),
                audit.get("review_decision_id", ""),
                audit.get("reviewer", ""),
                audit["action"],
                audit.get("event_id", ""),
                json.dumps(audit.get("source_ids", []), ensure_ascii=False),
                audit.get("timestamp", ""),
                audit.get("formal_hash_before", ""),
                audit.get("formal_hash_after", ""),
                audit.get("result", "success"),
                audit.get("reason", ""),
            ),
        )
        self.conn.commit()

    def list_publication_audit(self, batch_id: str | None = None) -> list[dict[str, Any]]:
        if batch_id:
            rows = self.conn.execute(
                "SELECT * FROM publication_audit_log WHERE batch_id=? ORDER BY timestamp, audit_id",
                (batch_id,),
            ).fetchall()
        else:
            rows = self.conn.execute(
                "SELECT * FROM publication_audit_log ORDER BY timestamp, audit_id"
            ).fetchall()
        return [dict(r) for r in rows]

    def upsert_refresh_batch(self, batch: dict[str, Any]):
        cols = [
            "refresh_batch_id", "publication_batch_id", "election_id", "formal_state_hash",
            "previous_coverage_version", "previous_snapshot_id",
            "requested_period_start", "requested_period_end", "status",
            "coverage_refresh_required", "snapshot_refresh_required", "assessment_refresh_required",
            "coverage_result", "snapshot_result", "assessment_trigger_result",
            "created_at", "started_at", "finished_at", "error_summary",
        ]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c != "refresh_batch_id")
        self.conn.execute(
            f"INSERT INTO downstream_refresh_batches ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(refresh_batch_id) DO UPDATE SET {updates}",
            [batch.get(c, "") for c in cols],
        )
        self.conn.commit()

    def get_refresh_batch_by_publication(self, publication_batch_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM downstream_refresh_batches WHERE publication_batch_id=?",
            (publication_batch_id,),
        ).fetchone()
        return dict(row) if row else None

    def get_refresh_batch(self, refresh_batch_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM downstream_refresh_batches WHERE refresh_batch_id=?",
            (refresh_batch_id,),
        ).fetchone()
        return dict(row) if row else None

    def insert_trigger(self, trigger: dict[str, Any]):
        self.conn.execute(
            """INSERT OR IGNORE INTO assessment_trigger_queue
               (trigger_id, election_id, period_start, period_end, refresh_batch_id,
                formal_state_hash, coverage_version, facts_cutoff, snapshot_id, trigger_reason, status,
                created_at, eligible_at, started_at, finished_at, assessment_run_id, error_summary)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                trigger["trigger_id"],
                trigger["election_id"],
                trigger["period_start"],
                trigger["period_end"],
                trigger.get("refresh_batch_id", ""),
                trigger.get("formal_state_hash", ""),
                trigger.get("coverage_version", ""),
                trigger.get("facts_cutoff", ""),
                trigger.get("snapshot_id", ""),
                trigger.get("trigger_reason", ""),
                trigger.get("status", "pending"),
                trigger.get("created_at", ""),
                trigger.get("eligible_at", ""),
                trigger.get("started_at", ""),
                trigger.get("finished_at", ""),
                trigger.get("assessment_run_id", ""),
                trigger.get("error_summary", ""),
            ),
        )
        self.conn.commit()

    def supersede_triggers(self, election_id: str, period_start: str, period_end: str, keep_trigger_id: str):
        self.conn.execute(
            "UPDATE assessment_trigger_queue SET status='superseded' "
            "WHERE election_id=? AND period_start=? AND period_end=? "
            "AND status='pending' AND trigger_id!=?",
            (election_id, period_start, period_end, keep_trigger_id),
        )
        self.conn.commit()

    def get_trigger(self, trigger_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM assessment_trigger_queue WHERE trigger_id=?", (trigger_id,)
        ).fetchone()
        return dict(row) if row else None

    def update_trigger_status(
        self,
        trigger_id: str,
        status: str,
        error_summary: str = "",
        assessment_run_id: str = "",
    ):
        from datetime import datetime
        from app.time_utils import TAIPEI

        now = datetime.now(TAIPEI).isoformat()
        self.conn.execute(
            "UPDATE assessment_trigger_queue SET status=?, error_summary=?, "
            "assessment_run_id=CASE WHEN ? != '' THEN ? ELSE assessment_run_id END, "
            "finished_at=CASE WHEN ? IN ('generated','failed','superseded','blocked') "
            "THEN ? ELSE finished_at END WHERE trigger_id=?",
            (status, error_summary, assessment_run_id, assessment_run_id,
             status, now, trigger_id),
        )
        self.conn.commit()

    def get_triggers_for_period(
        self, election_id: str, period_start: str, period_end: str
    ) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM assessment_trigger_queue WHERE election_id=? "
            "AND period_start=? AND period_end=? ORDER BY created_at, trigger_id",
            (election_id, period_start, period_end),
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_daily_review_completion(self, row: dict[str, Any]):
        cols = [
            "election_id", "review_date", "review_status", "completed_at",
            "completed_by", "candidate_total", "resolved_count", "unresolved_count",
            "material_event_count", "no_material_event", "candidate_cursor_at_completion",
            "business_hash",
        ]
        placeholders = ",".join("?" for _ in cols)
        updates = ",".join(f"{c}=excluded.{c}" for c in cols if c not in ("election_id", "review_date"))
        self.conn.execute(
            f"INSERT INTO daily_review_completion ({','.join(cols)}) VALUES ({placeholders}) "
            f"ON CONFLICT(election_id, review_date) DO UPDATE SET {updates}",
            [row.get(c, "") for c in cols],
        )
        self.conn.commit()

    def get_daily_review_completion(self, election_id: str, review_date: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM daily_review_completion WHERE election_id=? AND review_date=?",
            (election_id, review_date),
        ).fetchone()
        return dict(row) if row else None

    def list_daily_review_completions(self, election_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM daily_review_completion WHERE election_id=? ORDER BY review_date",
            (election_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_source(self, source: dict[str, Any]):
        self.conn.execute(
            """INSERT INTO candidate_sources
               (candidate_source_id, normalized_source_name, normalized_domain,
                original_source_names_json, formal_source_id, formal_match_status,
                formal_match_basis, first_seen_at, last_seen_at)
               VALUES (?,?,?,?,?,?,?,?,?)
               ON CONFLICT(candidate_source_id) DO UPDATE SET
                 normalized_source_name=excluded.normalized_source_name,
                 normalized_domain=excluded.normalized_domain,
                 original_source_names_json=excluded.original_source_names_json,
                 formal_source_id=excluded.formal_source_id,
                 formal_match_status=excluded.formal_match_status,
                 formal_match_basis=excluded.formal_match_basis,
                 last_seen_at=excluded.last_seen_at""",
            (
                source["candidate_source_id"],
                source["normalized_source_name"],
                source.get("normalized_domain", ""),
                source.get("original_source_names_json", "[]"),
                source.get("formal_source_id", ""),
                source["formal_match_status"],
                source.get("formal_match_basis", ""),
                source.get("first_seen_at", ""),
                source.get("last_seen_at", ""),
            ),
        )
        self.conn.commit()

    def link_event_source(self, link: dict[str, Any]):
        self.conn.execute(
            """INSERT OR IGNORE INTO candidate_event_sources
               (candidate_id, candidate_source_id, news_article_id, relationship_type)
               VALUES (?,?,?,?)""",
            (
                link["candidate_id"],
                link["candidate_source_id"],
                link["news_article_id"],
                link.get("relationship_type", "reported_by"),
            ),
        )
        self.conn.commit()

    def upsert_duplicate_suggestion(self, suggestion: dict[str, Any]):
        self.conn.execute(
            """INSERT OR REPLACE INTO formal_duplicate_suggestions
               (suggestion_id, candidate_id, formal_event_id, similarity_score,
                date_score, actor_score, event_type_score, keyword_score,
                source_overlap_score, matching_reasons_json, conflicting_reasons_json,
                suggested_action, created_run_id)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                suggestion["suggestion_id"],
                suggestion["candidate_id"],
                suggestion["formal_event_id"],
                float(suggestion["similarity_score"]),
                float(suggestion["date_score"]),
                float(suggestion["actor_score"]),
                float(suggestion["event_type_score"]),
                float(suggestion["keyword_score"]),
                float(suggestion["source_overlap_score"]),
                suggestion.get("matching_reasons_json", "[]"),
                suggestion.get("conflicting_reasons_json", "[]"),
                suggestion["suggested_action"],
                suggestion.get("created_run_id", ""),
            ),
        )
        self.conn.commit()

    def upsert_validation(self, validation: dict[str, Any]):
        self.conn.execute(
            """INSERT OR REPLACE INTO candidate_validation_results
               (candidate_id, validation_ready, errors_json, warnings_json,
                checked_at, validator_version)
               VALUES (?,?,?,?,?,?)""",
            (
                validation["candidate_id"],
                int(bool(validation.get("validation_ready"))),
                validation.get("errors_json", "[]"),
                validation.get("warnings_json", "[]"),
                validation.get("checked_at", ""),
                validation.get("validator_version", "0.1.0"),
            ),
        )
        self.conn.commit()

    def find_candidate_by_article(self, news_article_id: str) -> str | None:
        row = self.conn.execute(
            "SELECT candidate_id FROM candidate_event_articles WHERE news_article_id=? "
            "ORDER BY rowid LIMIT 1",
            (news_article_id,),
        ).fetchone()
        return row[0] if row else None

    def candidate_exists(self, candidate_id: str) -> bool:
        return self.conn.execute(
            "SELECT 1 FROM candidate_events WHERE candidate_id=?", (candidate_id,)
        ).fetchone() is not None

    def get_candidate(self, candidate_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM candidate_events WHERE candidate_id=?", (candidate_id,)
        ).fetchone()
        return dict(row) if row else None

    def list_candidates(
        self,
        status: str | None = None,
        event_date: str | None = None,
        actor: str | None = None,
        event_type: str | None = None,
        risk_level: str | None = None,
        formal_duplicate_status: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        clauses: list[str] = ["1=1"]
        params: list[Any] = []
        if status:
            clauses.append("review_status=?")
            params.append(status)
        if event_date:
            clauses.append("canonical_event_date=?")
            params.append(event_date)
        if actor:
            clauses.append("(primary_actor=? OR secondary_actors_json LIKE ?)")
            params.extend([actor, f"%{actor}%"])
        if event_type:
            clauses.append("candidate_event_type=?")
            params.append(event_type)
        if risk_level:
            clauses.append("risk_level=?")
            params.append(risk_level)
        if formal_duplicate_status:
            clauses.append("formal_duplicate_status=?")
            params.append(formal_duplicate_status)
        params.append(int(limit))
        rows = self.conn.execute(
            f"SELECT * FROM candidate_events WHERE {' AND '.join(clauses)} "
            "ORDER BY canonical_event_date DESC, candidate_id LIMIT ?",
            params,
        ).fetchall()
        return [dict(r) for r in rows]

    def get_articles(self, candidate_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM candidate_event_articles WHERE candidate_id=? "
            "ORDER BY published_at, news_article_id",
            (candidate_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_assertions(self, candidate_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM candidate_assertions WHERE candidate_id=? ORDER BY assertion_id",
            (candidate_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_sources(self, candidate_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT s.* FROM candidate_sources s "
            "JOIN candidate_event_sources es ON es.candidate_source_id=s.candidate_source_id "
            "WHERE es.candidate_id=? GROUP BY s.candidate_source_id ORDER BY s.normalized_source_name",
            (candidate_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_duplicate_suggestions(self, candidate_id: str) -> list[dict[str, Any]]:
        rows = self.conn.execute(
            "SELECT * FROM formal_duplicate_suggestions WHERE candidate_id=? "
            "ORDER BY similarity_score DESC, formal_event_id",
            (candidate_id,),
        ).fetchall()
        return [dict(r) for r in rows]

    def get_validation(self, candidate_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM candidate_validation_results WHERE candidate_id=?",
            (candidate_id,),
        ).fetchone()
        return dict(row) if row else None

    def count_candidates_by_status(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT review_status, COUNT(*) FROM candidate_events GROUP BY review_status"
        ).fetchall()
        return {r[0]: r[1] for r in rows}

    def get_latest_successful_run(self, election_id: str) -> dict[str, Any] | None:
        row = self.conn.execute(
            "SELECT * FROM pipeline_runs WHERE election_id=? AND status='success' "
            "ORDER BY started_at DESC LIMIT 1",
            (election_id,),
        ).fetchone()
        return dict(row) if row else None

    def business_output_hash(self) -> str:
        tables = [
            (
                "candidate_events",
                [
                    "candidate_id", "election_id", "anchor_article_id", "cluster_fingerprint",
                    "canonical_event_date", "event_date_precision", "event_date_basis",
                    "event_date_confidence", "candidate_event_type", "candidate_title",
                    "candidate_summary", "primary_actor", "secondary_actors_json",
                    "locations_json", "themes_json", "keywords_json", "assertion_profile_json",
                    "article_count", "source_count", "relevance_score", "completeness_score",
                    "cluster_confidence", "date_confidence", "source_confidence",
            "assertion_risk_score", "formal_duplicate_score", "formal_duplicate_status",
            "risk_level", "review_status", "status_reason_codes_json",
            "relevance_label", "date_flagged_inferred", "candidate_schema_version",
                ],
            ),
            (
                "candidate_event_articles",
                [
                    "candidate_id", "news_article_id", "relationship_type", "is_anchor",
                    "article_title", "article_url", "source_name", "published_at",
                    "event_date_candidate", "event_date_basis", "match_score",
                ],
            ),
            (
                "candidate_assertions",
                [
                    "assertion_id", "candidate_id", "assertion_kind", "assertion_text",
                    "subject", "predicate", "object_text", "speaker", "evidence_article_id",
                    "evidence_field", "evidence_text", "confidence", "risk_flags_json",
                ],
            ),
            (
                "candidate_sources",
                [
                    "candidate_source_id", "normalized_source_name", "normalized_domain",
                    "original_source_names_json", "formal_source_id", "formal_match_status",
                    "formal_match_basis",
                ],
            ),
            (
                "candidate_event_sources",
                ["candidate_id", "candidate_source_id", "news_article_id", "relationship_type"],
            ),
            (
                "formal_duplicate_suggestions",
                [
                    "suggestion_id", "candidate_id", "formal_event_id", "similarity_score",
                    "date_score", "actor_score", "event_type_score", "keyword_score",
                    "source_overlap_score", "matching_reasons_json", "conflicting_reasons_json",
                    "suggested_action",
                ],
            ),
            (
            "candidate_validation_results",
                ["candidate_id", "validation_ready", "errors_json", "warnings_json", "validator_version"],
            ),
            (
                "candidate_article_matches",
                [
                    "news_article_id", "election_id", "match_mode", "relevance_label",
                    "matched_people_json", "matched_parties_json", "matched_issues_json",
                    "matched_basis_json", "match_score", "classifier_version",
                ],
            ),
        ]
        h = hashlib.sha256()
        for t, cols in tables:
            col_sql = ",".join(cols)
            rows = self.conn.execute(f"SELECT {col_sql} FROM {t} ORDER BY 1").fetchall()
            h.update(t.encode("utf-8"))
            h.update(json.dumps([list(r) for r in rows], ensure_ascii=False, sort_keys=True).encode("utf-8"))
        return h.hexdigest()
