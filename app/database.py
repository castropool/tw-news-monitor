import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from urllib.parse import quote

from .article_identity import article_identity_key, prefer_richer_article
from .models import Article

logger = logging.getLogger(__name__)
SCHEMA_VERSION = 6
IDENTITY_BACKFILL_BATCH = 500


class Database:
    def __init__(self, db_path: str | Path, *, read_only: bool = False):
        self._db_path = Path(db_path)
        self._read_only = read_only
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        if self._read_only:
            uri_path = quote(self._db_path.resolve().as_posix(), safe="/:\\")
            self._conn = sqlite3.connect(f"file:{uri_path}?mode=ro", uri=True)
            self._conn.execute("PRAGMA query_only=ON")
            return
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self._db_path))
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._ensure_schema_meta()
        self._run_migrations()

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("Database not connected. Call connect() first.")
        return self._conn

    def create_tables(self) -> None:
        self._run_migrations()

    def _ensure_schema_meta(self) -> None:
        self.conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            )
            """
        )
        self.conn.commit()

    def schema_version(self) -> int:
        row = self.conn.execute(
            "SELECT value FROM schema_meta WHERE key = 'schema_version'"
        ).fetchone()
        try:
            return int(row[0]) if row else 0
        except (TypeError, ValueError):
            return 0

    def _set_schema_version(self, version: int) -> None:
        self.conn.execute(
            "INSERT INTO schema_meta(key, value) VALUES('schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(version),),
        )

    def _run_migrations(self) -> None:
        """Create or upgrade the news DB in one repeatable transaction."""
        self.conn.execute("BEGIN")
        try:
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS articles (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    source_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    category TEXT NOT NULL,
                    title TEXT NOT NULL,
                    url TEXT NOT NULL UNIQUE,
                    identity_key TEXT,
                    published_at TEXT,
                    published_at_precision TEXT NOT NULL DEFAULT 'exact',
                    fetched_at TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    summary TEXT,
                    summary_source TEXT,
                    summary_attempted_at TEXT,
                    section TEXT,
                    language TEXT,
                    access_level TEXT,
                    delivery_eligible INTEGER NOT NULL DEFAULT 1,
                    filter_reason TEXT,
                    filter_version TEXT,
                    delivered_at TEXT
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS news_topics (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    topic TEXT NOT NULL DEFAULT 'election_2026_local',
                    scope TEXT,
                    region TEXT,
                    regions_json TEXT,
                    event_type TEXT,
                    confidence INTEGER,
                    source_type TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(url, topic)
                )
                """
            )
            self.conn.execute(
                """
                CREATE TABLE IF NOT EXISTS identity_collisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    identity_key TEXT NOT NULL,
                    url TEXT NOT NULL,
                    existing_url TEXT NOT NULL,
                    detected_at TEXT NOT NULL,
                    UNIQUE(identity_key, url, existing_url)
                )
                """
            )

            article_columns = {
                row[1] for row in self.conn.execute("PRAGMA table_info(articles)")
            }
            for col, ddl in (
                ("summary", "summary TEXT"),
                ("summary_source", "summary_source TEXT"),
                ("summary_attempted_at", "summary_attempted_at TEXT"),
                ("section", "section TEXT"),
                ("language", "language TEXT"),
                ("access_level", "access_level TEXT"),
                ("identity_key", "identity_key TEXT"),
                (
                    "published_at_precision",
                    "published_at_precision TEXT NOT NULL DEFAULT 'exact'",
                ),
                (
                    "delivery_eligible",
                    "delivery_eligible INTEGER NOT NULL DEFAULT 1",
                ),
                ("filter_reason", "filter_reason TEXT"),
                ("filter_version", "filter_version TEXT"),
                ("delivered_at", "delivered_at TEXT"),
            ):
                if col not in article_columns:
                    self.conn.execute(f"ALTER TABLE articles ADD COLUMN {ddl}")

            # Identity backfill runs after the identity index exists so each
            # collision/query uses an index instead of a full table scan.
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_identity_key "
                "ON articles(identity_key)"
            )

            self._ensure_topics_schema()
            self._ensure_social_schema()
            self._backfill_identity_keys()

            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_url ON articles(url)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_fetched_at ON articles(fetched_at)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_articles_category ON articles(category)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_topics_url ON news_topics(url)"
            )
            self.conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_news_topics_topic ON news_topics(topic)"
            )
            self._set_schema_version(SCHEMA_VERSION)
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    def _ensure_topics_schema(self) -> None:
        """Upgrade old URL-only topic uniqueness without losing rows."""
        columns = {
            row[1] for row in self.conn.execute("PRAGMA table_info(news_topics)")
        }
        unique_indexes: list[tuple[str, ...]] = []
        for index in self.conn.execute("PRAGMA index_list(news_topics)"):
            if index[2]:
                unique_indexes.append(
                    tuple(
                        row[2]
                        for row in self.conn.execute(
                            f"PRAGMA index_info('{index[1]}')"
                        )
                    )
                )

        if ("url",) in unique_indexes:
            def expr(name: str) -> str:
                return name if name in columns else "NULL"

            self.conn.execute(
                """
                CREATE TABLE news_topics_new (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    url TEXT NOT NULL,
                    topic TEXT NOT NULL DEFAULT 'election_2026_local',
                    scope TEXT,
                    region TEXT,
                    regions_json TEXT,
                    event_type TEXT,
                    confidence INTEGER,
                    source_type TEXT,
                    metadata_json TEXT,
                    created_at TEXT NOT NULL,
                    UNIQUE(url, topic)
                )
                """
            )
            self.conn.execute(
                f"""
                INSERT OR IGNORE INTO news_topics_new
                    (id, url, topic, scope, region, regions_json, event_type,
                     confidence, source_type, metadata_json, created_at)
                SELECT id, {expr('url')}, {expr('topic')}, {expr('scope')},
                       {expr('region')}, {expr('regions_json')}, {expr('event_type')},
                       {expr('confidence')}, {expr('source_type')},
                       {expr('metadata_json')}, {expr('created_at')}
                FROM news_topics
                """
            )
            self.conn.execute("DROP TABLE news_topics")
            self.conn.execute("ALTER TABLE news_topics_new RENAME TO news_topics")
            columns = {
                row[1] for row in self.conn.execute("PRAGMA table_info(news_topics)")
            }

        for col, ddl in (
            ("source_type", "source_type TEXT"),
            ("metadata_json", "metadata_json TEXT"),
        ):
            if col not in columns:
                self.conn.execute(f"ALTER TABLE news_topics ADD COLUMN {ddl}")

    def _ensure_social_schema(self) -> None:
        """Create political-social-monitor tables without touching news data."""
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS political_person (
                person_id TEXT PRIMARY KEY,
                canonical_name TEXT NOT NULL,
                display_name TEXT,
                current_role TEXT,
                organization TEXT,
                party TEXT,
                jurisdiction_level TEXT,
                jurisdiction TEXT,
                election_scope TEXT,
                candidate_id TEXT,
                monitoring_tier INTEGER,
                active_from TEXT,
                active_to TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS social_account (
                account_id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                platform TEXT NOT NULL,
                platform_user_id TEXT,
                handle TEXT,
                canonical_url TEXT,
                display_name TEXT,
                account_type TEXT,
                account_status TEXT NOT NULL DEFAULT 'unknown',
                verification_status TEXT NOT NULL DEFAULT 'unverified',
                verification_method TEXT,
                verification_source_url TEXT,
                verified_at TEXT,
                collection_method TEXT,
                enabled INTEGER NOT NULL DEFAULT 1,
                monitoring_priority INTEGER,
                first_seen_at TEXT NOT NULL,
                last_checked_at TEXT,
                last_success_at TEXT,
                last_seen_post_id TEXT,
                last_seen_published_at TEXT,
                consecutive_failures INTEGER NOT NULL DEFAULT 0,
                last_error_code TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(person_id) REFERENCES political_person(person_id)
            );

            CREATE TABLE IF NOT EXISTS social_post (
                post_id TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                platform_post_id TEXT NOT NULL,
                account_id TEXT NOT NULL,
                person_id TEXT NOT NULL,
                published_at TEXT,
                first_seen_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                text TEXT,
                normalized_text TEXT,
                title TEXT,
                canonical_url TEXT,
                media_type TEXT,
                media_urls_json TEXT,
                thumbnail_url TEXT,
                external_urls_json TEXT,
                is_reply INTEGER NOT NULL DEFAULT 0,
                is_repost INTEGER NOT NULL DEFAULT 0,
                is_quote INTEGER NOT NULL DEFAULT 0,
                reply_to_post_id TEXT,
                quoted_post_id TEXT,
                reposted_post_id TEXT,
                language TEXT,
                content_hash TEXT NOT NULL,
                crosspost_group_id TEXT,
                edited INTEGER NOT NULL DEFAULT 0,
                deleted INTEGER NOT NULL DEFAULT 0,
                raw_payload_hash TEXT,
                raw_snapshot_path TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(platform, platform_post_id),
                FOREIGN KEY(account_id) REFERENCES social_account(account_id),
                FOREIGN KEY(person_id) REFERENCES political_person(person_id)
            );

            CREATE TABLE IF NOT EXISTS social_post_revision (
                revision_id TEXT PRIMARY KEY,
                post_id TEXT NOT NULL,
                revision_number INTEGER NOT NULL,
                text TEXT,
                content_hash TEXT NOT NULL,
                observed_at TEXT NOT NULL,
                raw_snapshot_hash TEXT,
                UNIQUE(post_id, revision_number),
                FOREIGN KEY(post_id) REFERENCES social_post(post_id)
            );

            CREATE TABLE IF NOT EXISTS social_crosspost_group (
                crosspost_group_id TEXT PRIMARY KEY,
                person_id TEXT NOT NULL,
                canonical_post_id TEXT NOT NULL,
                member_post_ids_json TEXT NOT NULL,
                matching_method TEXT,
                matching_score REAL,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                FOREIGN KEY(canonical_post_id) REFERENCES social_post(post_id)
            );

            CREATE TABLE IF NOT EXISTS social_source_link (
                post_id TEXT PRIMARY KEY,
                source_id TEXT NOT NULL UNIQUE,
                created_at TEXT NOT NULL,
                FOREIGN KEY(post_id) REFERENCES social_post(post_id)
            );

            CREATE TABLE IF NOT EXISTS social_monitor_run (
                run_id TEXT PRIMARY KEY,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                accounts_attempted INTEGER NOT NULL DEFAULT 0,
                accounts_successful INTEGER NOT NULL DEFAULT 0,
                accounts_failed INTEGER NOT NULL DEFAULT 0,
                posts_seen INTEGER NOT NULL DEFAULT 0,
                posts_new INTEGER NOT NULL DEFAULT 0,
                posts_updated INTEGER NOT NULL DEFAULT 0,
                posts_deleted INTEGER NOT NULL DEFAULT 0,
                crossposts_created INTEGER NOT NULL DEFAULT 0,
                errors_json TEXT,
                warnings_json TEXT
            );
            """
        )
        indexes = [
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_social_account_platform_user "
            "ON social_account(platform, platform_user_id) "
            "WHERE platform_user_id IS NOT NULL",
            "CREATE INDEX IF NOT EXISTS idx_social_account_person ON social_account(person_id)",
            "CREATE INDEX IF NOT EXISTS idx_social_post_account ON social_post(account_id)",
            "CREATE INDEX IF NOT EXISTS idx_social_post_person ON social_post(person_id)",
            "CREATE INDEX IF NOT EXISTS idx_social_post_crosspost ON social_post(crosspost_group_id)",
            "CREATE INDEX IF NOT EXISTS idx_social_source_link_source ON social_source_link(source_id)",
            "CREATE INDEX IF NOT EXISTS idx_social_run_started ON social_monitor_run(started_at)",
        ]
        for sql in indexes:
            self.conn.execute(sql)

    def _backfill_identity_keys(self) -> int:
        """Fill missing identity keys in bounded batches and record collisions."""
        updated = 0
        while True:
            rows = self.conn.execute(
                "SELECT id, url FROM articles "
                "WHERE identity_key IS NULL OR identity_key = '' "
                "ORDER BY id LIMIT ?",
                (IDENTITY_BACKFILL_BATCH,),
            ).fetchall()
            if not rows:
                return updated
            for article_id, url in rows:
                try:
                    key = article_identity_key(str(url))
                except Exception:
                    key = "url:" + str(url)
                existing = self.conn.execute(
                    "SELECT url FROM articles WHERE identity_key = ? AND id <> ? "
                    "ORDER BY id LIMIT 1",
                    (key, article_id),
                ).fetchone()
                if existing and existing[0] != url:
                    self.conn.execute(
                        "INSERT OR IGNORE INTO identity_collisions "
                        "(identity_key, url, existing_url, detected_at) VALUES (?, ?, ?, ?)",
                        (key, str(url), str(existing[0]), datetime.now().isoformat()),
                    )
                    logger.warning(
                        "Identity collision during backfill: %s -> %s / %s",
                        key, url, existing[0],
                    )
                self.conn.execute(
                    "UPDATE articles SET identity_key = ? WHERE id = ?",
                    (key, article_id),
                )
                updated += 1
    def article_exists(self, url: str) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM articles WHERE url = ?", (url,)
        ).fetchone()
        return row is not None

    @staticmethod
    def _article_values(article: Article) -> tuple:
        try:
            identity = article_identity_key(article.url)
        except Exception:
            identity = "url:" + str(article.url)
        return (
            article.source_id,
            article.source_name,
            article.category,
            article.title,
            article.url,
            identity,
            article.published_at.isoformat() if article.published_at else None,
            getattr(article, "published_at_precision", "exact") or "exact",
            article.fetched_at.isoformat(),
            article.position,
            article.summary,
            article.summary_source,
            article.summary_attempted_at.isoformat() if article.summary_attempted_at else None,
            article.section,
            article.language,
            article.access_level,
            1 if getattr(article, "delivery_eligible", True) else 0,
            getattr(article, "filter_reason", None),
            getattr(article, "filter_version", None),
            article.delivered_at.isoformat() if getattr(article, "delivered_at", None) else None,
        )

    _ARTICLE_INSERT_SQL = """
        INSERT OR IGNORE INTO articles
            (source_id, source_name, category, title, url, identity_key,
             published_at, published_at_precision, fetched_at, position,
             summary, summary_source, summary_attempted_at, section,
             language, access_level, delivery_eligible, filter_reason,
             filter_version, delivered_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """

    def save_article(self, article: Article) -> None:
        self.conn.execute(self._ARTICLE_INSERT_SQL, self._article_values(article))
        self.conn.commit()

    def save_articles(self, articles: list[Article]) -> list[Article]:
        inserted: list[Article] = []
        for article in articles:
            cursor = self.conn.execute(self._ARTICLE_INSERT_SQL, self._article_values(article))
            if cursor.rowcount > 0:
                inserted.append(article)
        self.conn.commit()
        return inserted

    @staticmethod
    def _row_to_article(row) -> Article:
        return Article(
            source_id=row[0],
            source_name=row[1],
            category=row[2],
            title=row[3],
            url=row[4],
            published_at=datetime.fromisoformat(row[6]) if row[6] else None,
            fetched_at=datetime.fromisoformat(row[8]),
            position=row[9],
            summary=row[10],
            summary_source=row[11],
            summary_attempted_at=datetime.fromisoformat(row[12]) if row[12] else None,
            section=row[13],
            language=row[14],
            access_level=row[15],
            published_at_precision=row[7] or "exact",
            delivery_eligible=bool(row[16]) if row[16] is not None else True,
            filter_reason=row[17],
            filter_version=row[18],
            delivered_at=datetime.fromisoformat(row[19]) if row[19] else None,
        )

    _ARTICLE_SELECT = (
        "SELECT source_id, source_name, category, title, url, identity_key, "
        "published_at, published_at_precision, fetched_at, position, summary, "
        "summary_source, summary_attempted_at, section, language, access_level, "
        "delivery_eligible, filter_reason, filter_version, delivered_at "
        "FROM articles"
    )

    def get_articles_since(
        self, time: datetime, *, eligible_only: bool = False
    ) -> list[Article]:
        sql = self._ARTICLE_SELECT + " WHERE fetched_at >= ?"
        params: list = [time.isoformat()]
        if eligible_only:
            sql += " AND delivery_eligible = 1"
        rows = self.conn.execute(
            sql + " ORDER BY category, position, published_at",
            params,
        ).fetchall()
        return [self._row_to_article(row) for row in rows]

    def get_articles_between(
        self,
        start: datetime,
        end: datetime,
        *,
        eligible_only: bool = False,
    ) -> list[Article]:
        """Return rows with ``start <= fetched_at <= end``.

        Reuses the canonical article SELECT and row parser so every persisted
        metadata field (including precision/eligibility/filter fields) is
        restored for backfill and other DB-rebuild entrypoints.
        """
        sql = self._ARTICLE_SELECT + " WHERE fetched_at >= ? AND fetched_at <= ?"
        params: list = [start.isoformat(), end.isoformat()]
        if eligible_only:
            sql += " AND delivery_eligible = 1"
        rows = self.conn.execute(
            sql + " ORDER BY published_at DESC",
            params,
        ).fetchall()
        return [self._row_to_article(row) for row in rows]

    def iter_articles(
        self,
        *,
        batch_size: int = 500,
        since: datetime | None = None,
        until: datetime | None = None,
    ):
        """Yield stored articles by id without loading the whole DB."""
        last_id = 0
        batch_size = max(1, min(int(batch_size), 5000))
        while True:
            conditions = ["id > ?"]
            params: list = [last_id]
            if since is not None:
                conditions.append("fetched_at >= ?")
                params.append(since.isoformat())
            if until is not None:
                conditions.append("fetched_at < ?")
                params.append(until.isoformat())
            params.append(batch_size)
            rows = self.conn.execute(
                self._ARTICLE_SELECT.replace("SELECT ", "SELECT id, ", 1)
                + " WHERE "
                + " AND ".join(conditions)
                + " ORDER BY id LIMIT ?",
                params,
            ).fetchall()
            if not rows:
                return
            for row in rows:
                last_id = row[0]
                yield self._row_to_article(row[1:])

    def update_article_summaries(
        self, summaries: dict[str, str], source: str = "llm", attempted_at=None
    ) -> None:
        """Write generated summaries back to the database by URL."""
        for url, summary in summaries.items():
            self.conn.execute(
                "UPDATE articles SET summary = ?, summary_source = ?, "
                "summary_attempted_at = ? WHERE url = ?",
                (
                    summary,
                    source,
                    attempted_at.isoformat() if attempted_at else None,
                    url,
                ),
            )
        self.conn.commit()

    def mark_summary_attempted(self, urls: list[str], attempted_at=None) -> None:
        """Record a failed summary attempt (negative cache for retries)."""
        attempted_at = attempted_at or datetime.now()
        for url in urls:
            self.conn.execute(
                "UPDATE articles SET summary_attempted_at = ? "
                "WHERE url = ? AND summary IS NULL",
                (attempted_at.isoformat(), url),
            )
        self.conn.commit()

    def count_articles(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM articles").fetchone()
        return row[0] if row else 0

    def count_by_category(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT category, COUNT(*) FROM articles GROUP BY category"
        ).fetchall()
        return dict(rows) if rows else {}

    def count_by_source(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT source_id, COUNT(*) FROM articles GROUP BY source_id"
        ).fetchall()
        return dict(rows) if rows else {}

    def get_total_count(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) FROM articles"
        ).fetchone()
        return row[0] if row else 0

    def get_all_article_urls(self) -> list[str]:
        """Compatibility API; production dedup uses bounded batch lookups."""
        rows = self.conn.execute("SELECT url FROM articles").fetchall()
        return [row[0] for row in rows]

    @staticmethod
    def _chunks(values: list, size: int = 500):
        for start in range(0, len(values), size):
            yield values[start:start + size]

    def get_existing_urls(self, urls: list[str]) -> set[str]:
        """Return only matching URLs, without scanning the article table."""
        found: set[str] = set()
        for chunk in self._chunks(list(dict.fromkeys(urls))):
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT url FROM articles WHERE url IN ({placeholders})", chunk
            ).fetchall()
            found.update(row[0] for row in rows)
        return found

    def get_existing_identity_keys(self, identity_keys: list[str]) -> set[str]:
        """Return identity keys matching this run's candidates only."""
        found: set[str] = set()
        for chunk in self._chunks(list(dict.fromkeys(identity_keys))):
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                f"SELECT identity_key FROM articles "
                f"WHERE identity_key IN ({placeholders})", chunk
            ).fetchall()
            found.update(row[0] for row in rows if row[0])
        return found

    def get_articles_by_urls(self, urls: list[str]) -> dict[str, Article]:
        """Return existing articles keyed by URL, using bounded IN queries."""
        found: dict[str, Article] = {}
        for chunk in self._chunks(list(dict.fromkeys(urls))):
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                self._ARTICLE_SELECT + f" WHERE url IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                article = self._row_to_article(row)
                found[article.url] = article
        return found

    def get_articles_by_identity_keys(self, identity_keys: list[str]) -> dict[str, Article]:
        """Return existing articles keyed by identity_key."""
        found: dict[str, Article] = {}
        for chunk in self._chunks(list(dict.fromkeys(identity_keys))):
            if not chunk:
                continue
            placeholders = ",".join("?" for _ in chunk)
            rows = self.conn.execute(
                self._ARTICLE_SELECT
                + f" WHERE identity_key IN ({placeholders})",
                chunk,
            ).fetchall()
            for row in rows:
                article = self._row_to_article(row)
                key = row[5]
                if key and key not in found:
                    found[key] = article
        return found

    @staticmethod
    def _metadata_precision_rank(value) -> int:
        text = str(value or "unknown").strip().lower()
        if text == "exact":
            return 3
        if text == "date_only":
            return 2
        return 1

    def update_article_metadata_if_richer(
        self, existing: Article, incoming: Article
    ) -> bool:
        """Promote persisted metadata only when the incoming copy is richer.

        Exact-time records are never overwritten by another exact time; the
        method only updates time fields when precision rank increases or the
        existing copy had no time at all.  Summary/title enrichment is also
        one-way.  The article URL/identity/created row is never changed.
        """
        winner = prefer_richer_article(existing, incoming)
        if winner is not incoming:
            return False

        updates: dict[str, object] = {}
        old_rank = self._metadata_precision_rank(
            getattr(existing, "published_at_precision", "unknown")
        )
        new_rank = self._metadata_precision_rank(
            getattr(incoming, "published_at_precision", "unknown")
        )
        existing_pub = getattr(existing, "published_at", None)
        incoming_pub = getattr(incoming, "published_at", None)
        if new_rank > old_rank or (existing_pub is None and incoming_pub is not None):
            updates["published_at"] = incoming_pub.isoformat() if incoming_pub else None
            updates["published_at_precision"] = (
                getattr(incoming, "published_at_precision", "unknown") or "unknown"
            )

        existing_summary = str(getattr(existing, "summary", "") or "").strip()
        incoming_summary = str(getattr(incoming, "summary", "") or "").strip()
        if incoming_summary and not existing_summary:
            updates["summary"] = getattr(incoming, "summary", None)
            updates["summary_source"] = getattr(incoming, "summary_source", None)

        existing_title = str(getattr(existing, "title", "") or "").strip()
        incoming_title = str(getattr(incoming, "title", "") or "").strip()
        if incoming_title and not existing_title:
            updates["title"] = getattr(incoming, "title", "")

        if not updates:
            return False
        assignments = ", ".join(f"{column} = ?" for column in updates)
        values = list(updates.values()) + [existing.url]
        self.conn.execute(
            f"UPDATE articles SET {assignments} WHERE url = ?",
            values,
        )
        self.conn.commit()
        return True

    def mark_articles_delivered(
        self, urls: list[str], delivered_at: datetime | None = None
    ) -> int:
        """Record first successful delivery time for the given article URLs."""
        unique = list(dict.fromkeys(str(url) for url in urls if url))
        if not unique:
            return 0
        timestamp = (delivered_at or datetime.now()).isoformat()
        changed = 0
        for chunk in self._chunks(unique):
            placeholders = ",".join("?" for _ in chunk)
            cursor = self.conn.execute(
                f"UPDATE articles SET delivered_at = COALESCE(delivered_at, ?) "
                f"WHERE url IN ({placeholders})",
                [timestamp, *chunk],
            )
            changed += max(0, cursor.rowcount)
        self.conn.commit()
        return changed

    def count_topics(self) -> int:
        row = self.conn.execute("SELECT COUNT(*) FROM news_topics").fetchone()
        return row[0] if row else 0

    def save_election_topic(
        self,
        url: str,
        *,
        scope: str,
        region: str | None,
        regions: list[str],
        event_type: str,
        confidence: int,
        created_at: datetime | None = None,
        topic: str = "election_2026_local",
        metadata: dict | None = None,
    ) -> None:
        """Persist one article's election-topic annotation (idempotent)."""
        self.save_topic(
            url,
            topic,
            scope=scope,
            region=region,
            regions=regions,
            event_type=event_type,
            confidence=confidence,
            created_at=created_at,
            metadata=metadata,
        )

    def save_military_topic(
        self,
        url: str,
        *,
        source_type: str,
        created_at: datetime | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Persist a military topic while retaining any other URL topics."""
        if source_type not in {"commercial_military", "official_military"}:
            raise ValueError(f"Unsupported military source_type: {source_type}")
        self.save_topic(
            url,
            "military",
            source_type=source_type,
            created_at=created_at,
            metadata=metadata,
        )

    def save_topic(
        self,
        url: str,
        topic: str,
        *,
        scope: str | None = None,
        region: str | None = None,
        regions: list[str] | None = None,
        event_type: str | None = None,
        confidence: int | None = None,
        source_type: str | None = None,
        created_at: datetime | None = None,
        metadata: dict | None = None,
    ) -> None:
        """Upsert one (URL, topic) annotation without replacing other topics."""
        self.save_topics([{
            "url": url,
            "topic": topic,
            "scope": scope,
            "region": region,
            "regions": regions or [],
            "event_type": event_type,
            "confidence": confidence,
            "source_type": source_type,
            "created_at": created_at,
            "metadata": metadata,
        }])

    @staticmethod
    def _topic_values(row: dict) -> tuple:
        created_at = row.get("created_at") or datetime.now()
        metadata = row.get("metadata")
        if metadata is None and row.get("metadata_json") is not None:
            metadata_json = row.get("metadata_json")
        else:
            metadata_json = json.dumps(metadata or {}, ensure_ascii=False)
        return (
            row["url"],
            row.get("topic", "election_2026_local"),
            row.get("scope"),
            row.get("region"),
            json.dumps(row.get("regions") or [], ensure_ascii=False),
            row.get("event_type"),
            row.get("confidence"),
            row.get("source_type"),
            metadata_json,
            created_at.isoformat() if isinstance(created_at, datetime) else str(created_at),
        )

    def save_topics(self, rows: list[dict], *, update_existing: bool = True) -> int:
        """Upsert (default) or insert-only topic annotations in one transaction.

        Normal ingestion keeps ``update_existing=True`` so a corrected
        annotation can update an existing row.  Backfill uses
        ``update_existing=False`` so historical annotations are never
        overwritten by a re-run.
        """
        if not rows:
            return 0
        values = [self._topic_values(row) for row in rows]
        if update_existing:
            conflict_sql = """
                ON CONFLICT(url, topic) DO UPDATE SET
                    scope = excluded.scope,
                    region = excluded.region,
                    regions_json = excluded.regions_json,
                    event_type = excluded.event_type,
                    confidence = excluded.confidence,
                    source_type = excluded.source_type,
                    metadata_json = excluded.metadata_json,
                    created_at = excluded.created_at
            """
        else:
            conflict_sql = "ON CONFLICT(url, topic) DO NOTHING"
        sql = """
            INSERT INTO news_topics
                (url, topic, scope, region, regions_json, event_type,
                 confidence, source_type, metadata_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """ + conflict_sql
        was_in_transaction = self.conn.in_transaction
        try:
            if not was_in_transaction:
                self.conn.execute("BEGIN")
            self.conn.executemany(sql, values)
            if not was_in_transaction:
                self.conn.commit()
        except Exception:
            if not was_in_transaction:
                self.conn.rollback()
            raise
        return len(values)

    def insert_missing_topics(self, rows: list[dict]) -> int:
        """Insert only missing (url, topic) rows; never update existing rows.

        Returns the number of rows actually inserted.  Used by Topic Backfill
        so a second run is truly no-op for existing annotations.
        """
        if not rows:
            return 0
        before = self.conn.total_changes
        try:
            self.save_topics(rows, update_existing=False)
        except Exception:
            raise
        return self.conn.total_changes - before

    @staticmethod
    def _topic_row_to_dict(row) -> dict:
        regions = []
        if row[4]:
            try:
                regions = json.loads(row[4])
            except (TypeError, ValueError):
                regions = []
        metadata = {}
        if len(row) > 9 and row[9]:
            try:
                metadata = json.loads(row[9])
            except (TypeError, ValueError):
                metadata = {}
        return {
            "url": row[0],
            "topic": row[1],
            "scope": row[2],
            "region": row[3],
            "regions": regions,
            "event_type": row[5],
            "confidence": row[6],
            "source_type": row[7],
            "metadata": metadata,
            "created_at": row[8],
        }

    def get_election_topic(self, url: str) -> dict | None:
        """Return one article's persisted election-topic row (or None)."""
        row = self.conn.execute(
            "SELECT url, topic, scope, region, regions_json, event_type, "
            "confidence, source_type, metadata_json, created_at FROM news_topics "
            "WHERE url = ? AND topic = 'election_2026_local'",
            (url,),
        ).fetchone()
        if not row:
            return None
        return self._topic_row_to_dict((row[0], row[1], row[2], row[3], row[4],
                                        row[5], row[6], row[7], row[9], row[8]))

    def get_election_topics_by_urls(self, urls: list[str]) -> dict[str, dict]:
        """Return persisted topics for the given urls: {url: row_dict}."""
        if not urls:
            return {}
        placeholders = ",".join("?" for _ in urls)
        rows = self.conn.execute(
            f"SELECT url, topic, scope, region, regions_json, event_type, "
            f"confidence, source_type, metadata_json, created_at FROM news_topics "
            f"WHERE topic = 'election_2026_local' AND url IN ({placeholders})",
            urls,
        ).fetchall()
        result: dict[str, dict] = {}
        for row in rows:
            result[row[0]] = self._topic_row_to_dict(
                (row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7], row[9], row[8])
            )
        return result

    def get_topic_urls(self, urls: list[str], topic: str) -> set[str]:
        """Return the subset of URLs carrying the requested topic."""
        if not urls:
            return set()
        placeholders = ",".join("?" for _ in urls)
        rows = self.conn.execute(
            f"SELECT url FROM news_topics "
            f"WHERE topic = ? AND url IN ({placeholders})",
            [topic, *urls],
        ).fetchall()
        return {row[0] for row in rows}

    def __enter__(self):
        self.connect()
        return self

    def __exit__(self, *args):
        self.close()


def build_db_path(db_name: str = "news.db") -> Path:
    return Path(__file__).resolve().parent.parent / "data" / db_name
