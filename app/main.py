import argparse
import importlib
import logging
import sys
from dataclasses import replace as dataclass_replace
import os
import tempfile
import shutil
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
import json

import yaml

from .collectors import RSSCollector, UDNCollector, EBCCollector, CNAHtmlCollector, LtnRSSCollector, PresidentCollector, ZaobaoCollector, ReutersCollector, FTAlphavilleCollector, WSJRSSCollector, WSJNewsletterCollector, BloombergNewsletterCollector, LtnMilitaryCollector, NownewsMilitaryCollector, MNAMilitaryCollector
from .category_classifier import apply_content_classification
from .content_filter import apply_content_filter, load_content_filter
from .military import (
    filter_military_articles,
    is_military_source,
    load_military_config,
)
from .database import Database
from .digest import build_digest
from .lock import InstanceLock
from .notifier import NullNotifier, create_notifier
from .feishu import build_highlight_card, send_card, send_document
from .word_digest import build_word_digest
from .summarizer import enrich_articles_with_summaries
from .freshness import FreshnessResult, filter_fresh_articles
from .source_registry import (
    is_official_source,
    get_source_info,
    get_official_sources,
    validate_registry_against_sources,
)
from .time_utils import TAIPEI
from .importance import (
    classify_articles,
    finalize_importance,
    importance_summary,
    load_rules,
    select_highlights,
    validate_rules_config,
)
from .article_identity import (
    article_identity_key,
    deduplicate_articles_by_identity,
    prefer_richer_article,
)
from .international import (
    dedupe_international_for_digest,
    filter_international,
    is_international_media,
    load_international_config,
)
from .international_events import cluster_international_articles
from .international_translation import (
    SummarizerTranslator,
    TranslationResult,
    translate_article,
)
from .notification_candidates import (
    NotificationDedupStore,
    build_notification_candidates,
)
from .source_health import SourceHealthStore, SourceOutcome
from .settings import get_settings, load_environment
from .news_pipeline import run_delivery_core
from .social_monitor.crosspost import canonical_posts
from .social_monitor.relevance import select_word_posts
from .social_monitor.repository import SocialRepository
from .pipeline_models import CollectionResult
from .election2026.config import load_election_config as load_election2026_config
from .election2026.config import load_entities as load_election2026_entities
from .election2026.classifier import classify_articles as classify_election2026_articles

logger = logging.getLogger(__name__)

COLLECTOR_MAP = {
    "rss": RSSCollector,
    "udn": UDNCollector,
    "ebc": EBCCollector,
    "cna_list_html": CNAHtmlCollector,
    "zaobao": ZaobaoCollector,
    "ltn_rss": LtnRSSCollector,
    "newtalk_rss": RSSCollector,
    "president_json": PresidentCollector,
    "reuters": ReutersCollector,
    "ft_alphaville": FTAlphavilleCollector,
    "wsj_rss": WSJRSSCollector,
    "wsj_newsletter": WSJNewsletterCollector,
    "bloomberg_newsletter": BloombergNewsletterCollector,
    "ltn_military": LtnMilitaryCollector,
    "nownews_military": NownewsMilitaryCollector,
    "mna_military": MNAMilitaryCollector,
}

_INTERNATIONAL_SOURCE_TYPES = {
    "reuters_international": "reuters",
    "ft_alphaville": "ft_alphaville",
    "wsj_newsletter": "wsj_newsletter",
    "bloomberg_newsletter": "bloomberg_newsletter",
}
_FROZEN_WSJ_SOURCE_ID = "wsj_international"
_FROZEN_WSJ_SOURCE_TYPE = "wsj_rss"
_NEWSLETTER_SOURCE_TYPES = frozenset({"wsj_newsletter", "bloomberg_newsletter"})


def setup_logging(log_path: Path, level: str = "INFO") -> None:
    """Configure rotating file logger. Console output stays as print()."""
    log_path.parent.mkdir(parents=True, exist_ok=True)
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))

    fh = RotatingFileHandler(
        log_path,
        maxBytes=5 * 1024 * 1024,
        backupCount=3,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)s | %(message)s"
    ))
    root.addHandler(fh)


def load_sources(config_path: str | Path) -> list[dict]:
    try:
        with open(config_path, encoding="utf-8") as f:
            payload = yaml.safe_load(f)
    except (OSError, yaml.YAMLError) as exc:
        print(f"FATAL: unable to load source config: {exc}")
        raise SystemExit(1)
    if not isinstance(payload, dict) or not isinstance(payload.get("sources"), list):
        print("FATAL: source config must contain top-level sources list")
        raise SystemExit(1)
    if not all(isinstance(item, dict) for item in payload["sources"]):
        print("FATAL: every source entry must be a mapping")
        raise SystemExit(1)
    return payload["sources"]


def validate_sources_config(sources: list[dict], collector_map: dict) -> None:
    """Validate source config before any network/DB/Word/Feishu operations.

    Rules: id non-empty + unique, type non-empty + in map, enabled is real bool,
    url non-empty, category valid, collector-only without type errors.
    Exits with code 1 on any error.
    """
    VALID_CATEGORIES = {
        "politics",
        "economy",
        "military",
        "international",
        "religion",
    }
    errors = []
    seen_ids = set()

    if not isinstance(sources, list):
        print("FATAL: source config must be a list")
        raise SystemExit(1)
    if not isinstance(collector_map, dict):
        print("FATAL: collector map must be a mapping")
        raise SystemExit(1)

    for i, source in enumerate(sources):
        if not isinstance(source, dict):
            errors.append(f"[<index {i}>] source entry must be a mapping")
            continue
        sid = source.get("id", f"<index {i}>")

        if not isinstance(source.get("id"), str) or not source.get("id", "").strip():
            errors.append(f"[<index {i}>] id is empty or missing")
            continue

        if sid in seen_ids:
            errors.append(f"[{sid}] duplicate source id")
        seen_ids.add(sid)

        stype = source.get("type")
        if not isinstance(stype, str) or not stype.strip():
            has_collector = "collector" in source
            if has_collector:
                errors.append(
                    f"[{sid}] has 'collector' field but no 'type' field; "
                    f"add type or remove collector"
                )
            else:
                errors.append(f"[{sid}] 'type' field is missing or empty")
            continue

        if stype not in collector_map:
            errors.append(f"[{sid}] type='{stype}' not found in COLLECTOR_MAP")

        url = source.get("url")
        if not url or not isinstance(url, str):
            errors.append(f"[{sid}] 'url' is missing or empty")

        cat = source.get("category") or source.get("default_category")
        if cat and (not isinstance(cat, str) or cat not in VALID_CATEGORIES):
            allowed = ", ".join(sorted(VALID_CATEGORIES))
            errors.append(f"[{sid}] category='{cat}' is not valid (allowed: {allowed})")

        if "enabled" in source:
            enabled_val = source["enabled"]
            if not isinstance(enabled_val, bool):
                errors.append(
                    f"[{sid}] enabled={repr(enabled_val)} must be a boolean "
                    f"(True/False), not string"
                )

    # Once an international source is present, the production configuration
    # must contain exactly one entry for every planned source. Generic test
    # configurations without any international IDs retain the historical
    # validator behavior, while partial/malformed international configs fail
    # closed before any DB/network work.
    source_ids = [
        source.get("id")
        for source in sources
        if isinstance(source, dict) and isinstance(source.get("id"), str)
    ]
    strict_international = bool(
        set(source_ids) & (set(_INTERNATIONAL_SOURCE_TYPES) | {_FROZEN_WSJ_SOURCE_ID})
        or any(
            isinstance(source, dict)
            and source.get("type") == _FROZEN_WSJ_SOURCE_TYPE
            for source in sources
        )
    )
    if strict_international:
        by_id = {}
        for source in sources:
            if isinstance(source, dict) and source.get("id"):
                by_id.setdefault(source["id"], []).append(source)
        for source_id, expected_type in _INTERNATIONAL_SOURCE_TYPES.items():
            entries = by_id.get(source_id, [])
            if len(entries) != 1:
                errors.append(
                    f"[{source_id}] must appear exactly once in international config"
                )
                continue
            source = entries[0]
            if source.get("type") != expected_type:
                errors.append(f"[{source_id}] type must be {expected_type!r}")
            if type(source.get("enabled")) is not bool:
                errors.append(f"[{source_id}] enabled must be an independent boolean")
            if source_id in _NEWSLETTER_SOURCE_TYPES:
                if source.get("access_level") != "newsletter":
                    errors.append(f"[{source_id}] access_level must be 'newsletter'")
                if source.get("mailbox_label") != "InternationalNews":
                    errors.append(
                        f"[{source_id}] mailbox_label must be 'InternationalNews'"
                    )
                for field in ("sender_allowlist", "article_allowlist"):
                    value = source.get(field)
                    if not isinstance(value, (list, tuple, set, frozenset)) or not value:
                        errors.append(f"[{source_id}] {field} must be a non-empty allowlist")

        frozen = by_id.get(_FROZEN_WSJ_SOURCE_ID, [])
        if len(frozen) != 1:
            errors.append(
                f"[{_FROZEN_WSJ_SOURCE_ID}] frozen WSJ RSS entry must appear exactly once"
            )
        elif frozen[0].get("type") != _FROZEN_WSJ_SOURCE_TYPE:
            errors.append(
                f"[{_FROZEN_WSJ_SOURCE_ID}] frozen WSJ RSS type must be {_FROZEN_WSJ_SOURCE_TYPE!r}"
            )
        elif frozen[0].get("enabled") is not False:
            errors.append(f"[{_FROZEN_WSJ_SOURCE_ID}] frozen WSJ RSS must remain disabled")

    if errors:
        print("=" * 60)
        print("FATAL: Source config validation failed")
        print("=" * 60)
        for err in errors:
            print(f"  ERROR: {err}")
        print(f"\n{len(errors)} configuration error(s) found. Exiting.")
        sys.exit(1)

    logger.info("Source config validation passed (%d sources)", len(sources))



def deduplicate_articles_by_url(articles):
    """Deduplicate by normalized URL within the same run.

    When the same URL appears more than once, the richer metadata copy is
    kept (exact > date_only > unknown, then published_at/summary/title).
    Keeps first occurrence on exact ties, maintaining order.
    """
    seen = {}
    unique = []
    dups = []
    for article in articles:
        url = article.url
        if not url:
            unique.append(article)
            continue
        if url in seen:
            existing = seen[url]
            winner = prefer_richer_article(existing, article)
            if winner is not existing:
                dups.append(existing)
                seen[url] = winner
                for i, item in enumerate(unique):
                    if item is existing:
                        unique[i] = winner
                        break
            else:
                dups.append(article)
        else:
            seen[url] = article
            unique.append(article)
    return unique, dups


def enrich_summaries_safe(articles, db=None) -> None:
    """Best-effort summary enrichment; never blocks Word generation."""
    try:
        enrich_articles_with_summaries(articles, db)
    except Exception as exc:
        logger.warning("Summary enrichment failed: %s", exc)


def _classify_and_persist_election2026(
    articles, db, election_config, election_entities,
) -> dict:
    """九合一分类 + 落库 news_topics（best-effort，不阻断主链路）。

    返回 {url: ElectionAnnotation} 供 Word 渲染直接使用；分类器为确定性
    纯函数，Word 端缺省时也会用同一函数兜底重算，结果一致。
    """
    if not election_config or not election_config.get("enabled", False):
        return {}
    if not articles:
        return {}
    try:
        annotations = classify_election2026_articles(
            articles, election_config, election_entities
        )
        if db is not None and annotations:
            rows = [
                {
                    "url": url,
                    "topic": "election_2026_local",
                    "scope": ann.scope,
                    "region": ann.region,
                    "regions": ann.regions,
                    "event_type": ann.event_type,
                    "confidence": ann.confidence,
                    "metadata": {
                        "classifier_version": "election2026-v1",
                        "event_tags": list(getattr(ann, "event_tags", ()) or ()),
                    },
                }
                for url, ann in annotations.items()
            ]
            try:
                db.save_topics(rows)
            except AttributeError:
                for row in rows:
                    db.save_election_topic(
                        row["url"], scope=row["scope"], region=row["region"],
                        regions=row["regions"], event_type=row["event_type"],
                        confidence=row["confidence"], metadata=row["metadata"],
                    )
        logger.info("Election 2026 annotations: %d/%d", len(annotations), len(articles))
        return annotations
    except Exception as exc:  # noqa: BLE001 - feature must never break pipeline
        logger.warning("Election 2026 classification failed safely: %s", exc)
        return {}


def _get_social_word_items(db: Database) -> list[dict]:
    """Return canonical, Word-eligible social items without blocking Word."""
    try:
        repo = SocialRepository(db)
        items = []
        for post in canonical_posts(repo):
            person = repo.get_person(post.get("person_id")) or {}
            item = dict(post)
            item["person_display_name"] = person.get("display_name") or person.get("canonical_name")
            item["person_canonical_name"] = person.get("canonical_name")
            items.append(item)
        return select_word_posts(items)
    except Exception as exc:  # noqa: BLE001 - social section must never block legacy Word
        logger.warning("Social Word section lookup failed safely: %s", exc)
        return []


def _get_topic_urls_safe(articles, db, topic: str) -> set[str]:
    """Read persisted topic URLs without allowing metadata to block Word."""
    if db is None or not articles:
        return set()
    try:
        return db.get_topic_urls([article.url for article in articles], topic)
    except Exception as exc:  # noqa: BLE001 - optional section must fail safely
        logger.warning("Topic lookup failed safely for %s: %s", topic, exc)
        return set()


class _PrecomputedTranslationLookup:
    """Metadata-only translator view for event notification rendering.

    ``notification_candidates`` intentionally accepts the small translator
    protocol rather than a URL-aware object.  This lookup bridges that
    protocol to the results already computed for Word, so an event candidate
    never invokes a second provider call or invents a different translation.
    Fallback entries are excluded: the notification then uses the safe
    English metadata fallback from the shared delivery code.
    """

    def __init__(self, articles: list, translations: dict[str, TranslationResult]):
        self._by_metadata = {}
        for article in articles:
            result = translations.get(getattr(article, "url", ""))
            if result is None or result.status != "translated":
                continue
            key = self._key(article.title, article.summary, article.source_name)
            self._by_metadata[key] = result

    @staticmethod
    def _key(title: str, summary: str | None, source_name: str) -> tuple[str, str, str]:
        return (
            str(title or "").strip(),
            str(summary or "").strip(),
            str(source_name or "").strip(),
        )

    def translate(self, title: str, summary: str | None, *, source_name: str):
        key = self._key(title, summary, source_name)
        result = self._by_metadata.get(key)
        if result is None:
            raise LookupError("no precomputed translated metadata for article")
        if isinstance(result, tuple):
            return result
        return result.cn_title, result.cn_summary


def _build_notification_translation_lookup(
    articles: list,
    translations: dict[str, TranslationResult],
    international_coverage: dict[str, list] | None,
) -> _PrecomputedTranslationLookup | None:
    """Make event evidence render the canonical translation/fallback."""

    if not articles:
        return None
    lookup = _PrecomputedTranslationLookup(articles, translations)
    by_url = {str(getattr(article, "url", "")): article for article in articles}
    for canonical_url, members in (international_coverage or {}).items():
        canonical = by_url.get(str(canonical_url))
        values = list(members or [])
        if canonical is None and values:
            canonical = values[0]
        if canonical is None:
            continue
        canonical_result = translations.get(str(getattr(canonical, "url", "")))
        if canonical_result is not None and canonical_result.status == "translated":
            rendered = (canonical_result.cn_title, canonical_result.cn_summary)
        else:
            # This is intentionally English metadata, not a synthetic
            # translation.  It keeps the candidate and Word canonical in sync.
            rendered = (
                str(getattr(canonical, "title", "") or ""),
                str(getattr(canonical, "summary", "") or "metadata-only fallback"),
            )
        for member in values:
            lookup._by_metadata[lookup._key(
                member.title, member.summary, member.source_name
            )] = rendered
    return lookup


def _build_international_translator():
    """Build an optional production translator adapter, never a test fake.

    The existing summarizer module currently exposes summary enrichment but no
    stable title+summary translation callable.  A deployment may provide one
    through ``INTERNATIONAL_TRANSLATOR_FACTORY=module:function``; when it is
    absent (the normal current state), callers receive the strict English
    metadata fallback.  This keeps the pipeline explicit and makes a future
    summarizer implementation a drop-in adapter without coupling delivery to
    a provider or fetching an article body.
    """

    spec = os.getenv("INTERNATIONAL_TRANSLATOR_FACTORY", "").strip()
    try:
        if spec:
            module_name, function_name = spec.rsplit(":", 1)
            factory = getattr(importlib.import_module(module_name), function_name)
            provider = factory() if callable(factory) else factory
        else:
            # The current summarizer has no translation provider, but this
            # capability probe makes the adapter explicit when a deployment
            # supplies one without changing the delivery pipeline.
            from . import summarizer as summarizer_module
            provider = (
                getattr(summarizer_module, "translate_metadata", None)
                or getattr(summarizer_module, "international_translator", None)
            )
        if provider is None:
            return None
        return SummarizerTranslator(provider)
    except Exception as exc:
        logger.warning("International translator unavailable; using metadata fallback: %s", exc)
        return None


def _precompute_international_translations(
    articles: list,
    international_config: dict | None,
    translator=None,
) -> dict[str, TranslationResult]:
    """Translate only international delivery articles from legal metadata."""

    if not international_config or not international_config.get("enabled", False):
        return {}
    result: dict[str, TranslationResult] = {}
    for article in articles:
        if not is_international_media(article.source_name, international_config):
            continue
        # ``translate_article`` intentionally ignores body_fetcher and only
        # sees Article title/teaser.  With no real provider it records the
        # explicit English metadata fallback.
        result[article.url] = translate_article(article, translator=translator)
    return result


def _translation_articles_for_delivery(
    delivery_articles: list,
    international_coverage: dict[str, list] | None,
) -> list:
    """Return every current-run international member used by delivery.

    The visible Word set contains only canonical articles, while a fresh
    coverage member may be the notification evidence for an older canonical.
    Precomputing the union keeps both output paths on the same translator or
    the same strict English fallback.
    """

    result: list = []
    seen: set[str] = set()
    values = list(delivery_articles or [])
    for members in (international_coverage or {}).values():
        values.extend(members or [])
    for article in values:
        marker = str(getattr(article, "url", "") or "")
        if marker in seen:
            continue
        seen.add(marker)
        result.append(article)
    return result


def _notification_dedup_store(
    project_root: Path,
    international_config: dict | None,
    *,
    dry_run: bool = False,
) -> NotificationDedupStore | None:
    """Resolve an isolated event-dedup path; dry-run never writes it."""

    if dry_run:
        return None
    configured = os.getenv("INTERNATIONAL_NOTIFICATION_DEDUP_PATH", "").strip()
    if not configured and isinstance(international_config, dict):
        configured = str(
            international_config.get("notification_dedup_path")
            or (international_config.get("notification", {}) or {}).get("dedup_path")
            or ""
        ).strip()
    path = Path(configured) if configured else project_root / "data" / "international_notification_dedup.json"
    if not path.is_absolute():
        path = project_root / path
    return NotificationDedupStore(path)


def _deliver_event_candidates(
    notifier,
    candidates: list,
    dedup_store: NotificationDedupStore | None,
    now: datetime,
) -> bool:
    """Deliver candidates and persist only an explicit ``True`` result."""

    try:
        result = notifier.send_event_candidates(candidates)
    except Exception as exc:
        logger.warning("International event notifier raised: %s", exc)
        return False
    if result is not True:
        return False
    if dedup_store is not None:
        for candidate in candidates:
            dedup_store.mark_sent(candidate.dedup_key, now=now)
    return True


def _build_newsletter_mailbox(source: dict):
    """Build a readonly Gmail mailbox from external auth metadata only.

    No OAuth flow is started here.  Missing paths intentionally produce an
    unauthorized ``AuthContext`` and the injected newsletter collector then
    records ``MAILBOX_AUTH_REQUIRED`` as a source failure.
    """

    from .newsletter_ingestion.gmail_client import (
        GmailMailboxClient,
        build_service,
    )
    from .newsletter_ingestion.oauth import load_auth_context

    credentials_path = (
        os.getenv("GMAIL_CLIENT_SECRET_PATH")
        or os.getenv("GMAIL_CREDENTIALS_PATH")
        or os.getenv("GOOGLE_CLIENT_SECRET_PATH")
    )
    token_path = os.getenv("GMAIL_TOKEN_PATH") or os.getenv("GOOGLE_TOKEN_PATH")
    auth = load_auth_context(credentials_path, token_path)
    service = build_service(auth) if auth.authorized else None
    return GmailMailboxClient(
        service=service,
        label=source.get("mailbox_label", "InternationalNews"),
        modify=False,
        auth=auth,
    )


def _construct_collector(collector_cls, source: dict):
    """Construct a source collector, injecting mailbox policy for newsletters."""

    if source.get("type") in _NEWSLETTER_SOURCE_TYPES:
        return collector_cls(source, mailbox=_build_newsletter_mailbox(source))
    return collector_cls(source)


def collect_all(
    sources: list[dict], db: Database,
    content_filter_config: dict | None = None,
    military_config: dict | None = None,
    *,
    collector_map: dict | None = None,
    health_store: SourceHealthStore | None = None,
    election_config: dict | None = None,
    election_entities: dict | None = None,
) -> CollectionResult:
    """Collect news, dedup by URL, save new ones.

    Topic classification happens immediately after persistence so stale,
    unknown-time, and delivery-excluded articles still receive durable
    metadata.  The returned object exposes named fields and retains a
    legacy tuple iterator for older callers.
    """
    all_raw: list = []
    fetched_articles: list = []
    total_fetched = 0
    failed: list[str] = []
    military_source_types: dict[str, str] = {}
    military_before_save: list = []
    military_from_delivery: list = []

    active_collector_map = collector_map or COLLECTOR_MAP
    for source in sources:
        cls = active_collector_map.get(source.get("type"))
        if not cls:
            logger.warning("Unknown collector: %s", source.get("type"))
            continue
        if source.get("enabled") is False:
            if health_store is not None:
                health_store.disable(source["id"])
            continue
        collector = None
        try:
            # Construction is inside the same isolation boundary as collection:
            # a bad optional dependency/config cannot abort Taiwan sources.
            collector = _construct_collector(cls, source)
            articles = collector.collect()
            outcome = getattr(collector, "last_outcome", None)
            if isinstance(outcome, SourceOutcome) and (
                outcome.error_code
                or outcome.schema_valid is not True
                or not 200 <= int(outcome.http_status) < 300
            ):
                failed.append(source["id"])
                if health_store is not None:
                    health_store.update(source["id"], outcome)
                error_message = getattr(collector, "last_error_message", None)
                error_message = error_message or outcome.error_code or "source outcome invalid"
                logger.error("Failed %s: %s", source["id"], error_message)
                print(f"  [ERR] {source['id']}: {error_message}")
                continue
            articles = apply_content_classification(articles, source)
            fetched_articles.extend(articles)
            total_fetched += len(articles)
            if is_military_source(source):
                source_type = source.get("military_source_type", "commercial_military")
                for article in articles:
                    previous_type = military_source_types.get(article.url)
                    if previous_type != "official_military":
                        military_source_types[article.url] = source_type
                kept_articles, military_blocked = filter_military_articles(
                    articles, military_config
                )
                noise_mode = str(
                    (military_config or {}).get("noise_mode", "drop_before_save")
                ).strip().lower()
                if noise_mode == "exclude_from_delivery":
                    articles = kept_articles + military_blocked
                    military_from_delivery.extend(military_blocked)
                else:
                    articles = kept_articles
                    military_before_save.extend(military_blocked)
                if military_blocked:
                    logger.info(
                        "Military filter blocked %d/%d from %s: %s",
                        len(military_blocked),
                        len(kept_articles) + len(military_blocked),
                        source["id"],
                        [a.title[:40] for a in military_blocked[:5]],
                    )
            if health_store is not None:
                if not isinstance(outcome, SourceOutcome):
                    outcome = SourceOutcome(200, True, len(articles))
                health_store.update(source["id"], outcome)
            logger.info("collected %s: fetched=%d", source["id"], len(articles))
            all_raw.extend(articles)
            print(f"  [OK] {source['id']}: fetched={len(articles)}")
        except Exception as e:
            failed.append(source["id"])
            if health_store is not None:
                outcome = getattr(collector, "last_outcome", None)
                if not isinstance(outcome, SourceOutcome):
                    outcome = SourceOutcome(0, False, 0, "config")
                try:
                    health_store.update(source["id"], outcome)
                except Exception:
                    logger.warning("Source health update failed for %s", source["id"])
            logger.error("Failed %s: %s", source["id"], e)
            print(f"  [ERR] {source['id']}: {e}")
        finally:
            if collector is not None:
                try:
                    collector.close()
                except Exception:
                    logger.warning("Collector close failed for %s", source["id"])

    # Phase 2: Intra-run URL dedup
    unique_by_url, run_dups = deduplicate_articles_by_url(all_raw)

    # Phase 2.5: Intra-run identity dedup (e.g. UDN alias)
    unique_articles, identity_run_dups = deduplicate_articles_by_identity(unique_by_url)
    logger.info(
        "Intra-run dedup: raw=%d, url_unique=%d, url_removed=%d, id_removed=%d",
        len(all_raw), len(unique_articles), len(run_dups), len(identity_run_dups),
    )

    # Phase 3: DB check with identity keys (prevents UDN alias re-insertion).
    # Historical duplicates may still be promoted when the incoming copy is a
    # richer representation (e.g. banner unknown -> list exact on a later run).
    candidate_urls = [a.url for a in unique_articles]
    identity_by_url = {a.url: article_identity_key(a.url) for a in unique_articles}
    can_promote = all(
        hasattr(type(db), name)
        for name in ("get_articles_by_urls", "update_article_metadata_if_richer")
    )
    if can_promote:
        try:
            existing_by_url = db.get_articles_by_urls(candidate_urls)
            existing_by_id = db.get_articles_by_identity_keys(
                list(identity_by_url.values())
            )
            existing_urls = set(existing_by_url)
            existing_ids = set(existing_by_id)
        except Exception as exc:  # noqa: BLE001 - promotion must fail safe
            logger.warning("Historical metadata lookup failed safely: %s", exc)
            can_promote = False
    if not can_promote:
        try:
            existing_urls = set(db.get_existing_urls(candidate_urls))
        except AttributeError:
            # Compatibility for injected legacy database doubles only.
            existing_urls = set(db.get_all_article_urls())
        try:
            existing_ids = set(
                db.get_existing_identity_keys(list(identity_by_url.values()))
            )
        except AttributeError:
            existing_ids = {article_identity_key(u) for u in existing_urls}
        existing_by_url = {}
        existing_by_id = {}

    candidates = []
    promoted_articles = []
    promoted_delivery_articles = []
    hist_url_dups = []
    hist_id_dups = []
    for a in unique_articles:
        if a.url in existing_urls:
            existing = existing_by_url.get(a.url)
            if existing is not None and db.update_article_metadata_if_richer(existing, a):
                promoted = dataclass_replace(a, url=existing.url)
                promoted_articles.append(promoted)
                if (
                    getattr(existing, "delivered_at", None) is None
                    and getattr(existing, "delivery_eligible", True)
                ):
                    promoted_delivery_articles.append(promoted)
            else:
                hist_url_dups.append(a)
            continue
        identity_key = identity_by_url[a.url]
        if identity_key in existing_ids:
            existing = existing_by_id.get(identity_key)
            if existing is not None and db.update_article_metadata_if_richer(existing, a):
                # Keep the persisted canonical URL; only metadata is promoted.
                promoted = dataclass_replace(a, url=existing.url)
                promoted_articles.append(promoted)
                if (
                    getattr(existing, "delivered_at", None) is None
                    and getattr(existing, "delivery_eligible", True)
                ):
                    promoted_delivery_articles.append(promoted)
            else:
                hist_id_dups.append(a)
            continue
        candidates.append(a)

    # Content filter: its mode determines persistence versus delivery only.
    filter_result = apply_content_filter(candidates, content_filter_config)
    filtered_before_save = list(military_before_save)
    filtered_before_save.extend(filter_result.filtered_before_save)
    filtered_from_delivery = list(military_from_delivery)
    filtered_from_delivery.extend(filter_result.filtered_from_delivery)
    if filter_result.blocked:
        logger.info(
            "Content filter blocked %d/%d candidates (mode=%s): %s",
            len(filter_result.blocked), len(candidates), filter_result.mode,
            [a.title[:40] for a in filter_result.blocked[:5]],
        )
    if filter_result.mode == "drop_before_save":
        candidates = filter_result.kept
    else:
        candidates = filter_result.kept + filter_result.blocked

    # Persist delivery eligibility on every saved article.  Articles blocked
    # by content_filter or military noise in exclude_from_delivery mode are
    # stored but marked ineligible so export/backfill/Word later honor the
    # same business rule without relying on an in-memory URL set.
    military_blocked_urls = {a.url for a in military_from_delivery}
    content_blocked_urls = {a.url for a in filter_result.blocked}
    content_filter_version = str(
        (content_filter_config or {}).get("filter_version") or "current"
    )
    military_filter_version = str((military_config or {}).get("filter_version") or "current")
    for article in candidates:
        if article.url in military_blocked_urls:
            article.delivery_eligible = False
            article.filter_reason = "military_noise"
            article.filter_version = military_filter_version
        elif article.url in content_blocked_urls:
            article.delivery_eligible = False
            article.filter_reason = "content_filter"
            article.filter_version = content_filter_version

    inserted = db.save_articles(candidates)

    election_annotations = _classify_and_persist_election2026(
        inserted, db, election_config, election_entities
    )
    military_topic_rows = [
        {
            "url": article.url,
            "topic": "military",
            "source_type": military_source_types[article.url],
        }
        for article in inserted
        if article.url in military_source_types
    ]
    if military_topic_rows:
        try:
            db.save_topics(military_topic_rows)
        except AttributeError:
            for row in military_topic_rows:
                db.save_military_topic(row["url"], source_type=row["source_type"])
        except Exception as exc:  # noqa: BLE001 - topic metadata is best-effort
            logger.warning("Military topic persistence failed: %s", exc)

    result = CollectionResult(
        fetched_articles=fetched_articles,
        inserted_articles=inserted,
        promoted_articles=promoted_articles,
        promoted_delivery_articles=promoted_delivery_articles,
        filtered_before_save=filtered_before_save,
        filtered_from_delivery=filtered_from_delivery,
        failed_sources=failed,
        run_url_duplicates=len(run_dups),
        run_identity_duplicates=len(identity_run_dups),
        historical_url_duplicates=len(hist_url_dups),
        historical_identity_duplicates=len(hist_id_dups),
    )
    result.election_annotations = election_annotations
    logger.info(
        "Total: fetched=%d, run_url_removed=%d, run_id_removed=%d, "
        "hist_url_dup=%d, hist_id_dup=%d, filtered=%d, inserted=%d, "
        "promoted=%d, failed=%d",
        total_fetched, len(run_dups), len(identity_run_dups),
        len(hist_url_dups), len(hist_id_dups),
        result.filtered_count, len(inserted), len(promoted_articles), len(failed),
    )
    return result


def show_db_stats(db: Database) -> None:
    total = db.count_articles()
    print(f"数据库文章总数: {total}")
    print()
    by_source = db.count_by_source()
    if by_source:
        print("各来源数量:")
        for sid, cnt in sorted(by_source.items()):
            print(f"  {sid}: {cnt}")
    print()
    by_cat = db.count_by_category()
    if by_cat:
        print("各栏目数量:")
        for cat, cnt in sorted(by_cat.items()):
            print(f"  {cat}: {cnt}")


def _classify_delivery_articles(
    inserted_articles: list,
    source_baselines: dict[str, int],
    run_started_at: datetime,
    catch_up_enabled: bool = False,
    catch_up_max_minutes: int = 720,
    source_continuity: dict[str, bool] | None = None,
):
    continuity = source_continuity or {}
    freshness = filter_fresh_articles(
        inserted_articles, run_started_at,
        catch_up_enabled=catch_up_enabled,
        catch_up_max_minutes=catch_up_max_minutes,
    )
    fresh_articles = list(freshness.fresh_articles)
    catch_up_articles = list(freshness.catch_up_articles)
    stale_articles = list(freshness.stale_articles)
    unknown_articles = list(freshness.unknown_time_articles)
    future_articles = list(freshness.future_time_articles)
    catch_up_eligible = [
        a for a in catch_up_articles
        if source_baselines.get(a.source_id, 0) > 0
    ]
    baseline_excluded = [
        a for a in catch_up_articles
        if source_baselines.get(a.source_id, 0) == 0
    ]
    date_only_today = list(freshness.date_only_today_articles)
    date_only_eligible = [
        a for a in date_only_today
        if source_baselines.get(a.source_id, 0) > 0
        and bool(continuity.get(a.source_id, False))
    ]
    date_only_baseline_excluded = [
        a for a in date_only_today
        if source_baselines.get(a.source_id, 0) == 0
        or not bool(continuity.get(a.source_id, False))
    ]
    stale_articles = stale_articles + baseline_excluded + date_only_baseline_excluded
    catch_up_urls = {a.url for a in catch_up_eligible}
    return {
        'fresh_articles': fresh_articles,
        'date_only_eligible': date_only_eligible,
        'catch_up_eligible': catch_up_eligible,
        'catch_up_urls': catch_up_urls,
        'stale_articles': stale_articles,
        'unknown_articles': unknown_articles,
        'future_articles': future_articles,
        'baseline_excluded': baseline_excluded,
        'date_only_today': date_only_today,
    }


def _get_source_baselines(db: Database, sources: list[dict]) -> dict[str, int]:
    """Return per-source row counts before the current collection starts."""
    baselines: dict[str, int] = {}
    for source in sources:
        source_id = source["id"]
        try:
            baselines[source_id] = db.conn.execute(
                "SELECT COUNT(*) FROM articles WHERE source_id = ?", (source_id,)
            ).fetchone()[0]
        except Exception:
            baselines[source_id] = 0
    return baselines


def _get_source_continuity(
    health_store: SourceHealthStore | None,
    sources: list[dict],
    now: datetime,
    max_gap_hours: int,
) -> dict[str, bool]:
    """Return whether each source had a recent successful collection.

    A missing/old last_success means the source is treated as just recovering,
    so same-day date-only articles are held back for this run.  Exact articles
    are unaffected and still use normal freshness/catch-up logic.
    """
    continuity: dict[str, bool] = {}
    max_gap = timedelta(hours=max(1, int(max_gap_hours)))
    for source in sources:
        source_id = str(source.get("id") or "")
        if not source_id:
            continue
        last_success = None
        if health_store is not None:
            try:
                last_success = health_store.get(source_id).last_success
            except Exception:
                last_success = None
        if last_success is None:
            continuity[source_id] = False
            continue
        if last_success.tzinfo is None:
            last_success = last_success.replace(tzinfo=TAIPEI)
        else:
            last_success = last_success.astimezone(TAIPEI)
        continuity[source_id] = (now - last_success) <= max_gap
    return continuity


def prepare_international_delivery(
    delivery_articles: list,
    international_config: dict | None,
) -> tuple[list, dict]:
    """国际媒体层（Phase I）：相关度过滤 + 跨媒体事件去重（显示层）。

    仅作用于“即将进入 digest/Word/高亮”的文章集合；DB 落库（collect_all 内
    完成）不受影响——所有国际媒体文章（含被判定不相关的）仍按 URL 去重后
    正常入库。非国际媒体文章始终原样通过。

    容错：层配置缺失或层内异常时回退为原样放行（与未启用行为一致），
    绝不中断主流程；单国际源采集失败由 collect_all 的逐源 try/except 隔离。

    返回 (digest_articles, coverage)：
    - digest_articles：进入简报的文章（国际媒体 canonical + 非国际媒体文章）；
    - coverage：以 canonical.url 为键的事件成员表（含 canonical 自身）。
    """
    cfg = international_config or {}
    if not cfg.get("enabled", False):
        return list(delivery_articles), {a.url: [a] for a in delivery_articles}
    try:
        included, excluded = filter_international(delivery_articles, cfg)
        if excluded:
            logger.info(
                "International layer excluded %d irrelevant article(s): %s",
                len(excluded), [a.title[:40] for a in excluded[:5]],
            )
        clusters, coverage = cluster_international_articles(included, cfg)
        canonical = [cluster.canonical for cluster in clusters]
        merged = sum(1 for members in coverage.values() if len(members) > 1)
        logger.info(
            "International layer: delivered=%d (merged events=%d)",
            len(canonical), merged,
        )
        return canonical, coverage
    except Exception as exc:
        logger.warning(
            "International layer failed, delivering unprocessed articles: %s", exc
        )
        return list(delivery_articles), {}

def main() -> None:
    parser = argparse.ArgumentParser(
        description="Taiwan News Monitor - Simple Edition"
    )
    parser.add_argument(
        "--bootstrap", action="store_true",
        help="Initial collect + save, no notification sent",
    )
    parser.add_argument(
        "--dry-run", action="store_true",
        help="Collect and print, no save or notify",
    )
    parser.add_argument(
        "--test-notify", action="store_true",
        help="Send test notification",
    )
    parser.add_argument(
        "--db-stats", action="store_true",
        help="Show database statistics",
    )
    parser.add_argument(
        "--export-word", nargs="?", const=30, type=int, default=None,
        metavar="N",
        help="Export latest N articles to Word (default: 30)",
    )
    parser.add_argument(
        "--backfill-run", type=str, default=None,
        metavar="BATCH_ID",
        help="Catch-up delivery for previously inserted but unpushed articles",
    )
    parser.add_argument(
        "--send", action="store_true",
        help="Actually send when used with --backfill-run",
    )
    parser.add_argument(
        "--list-feishu-chats", action="store_true",
        help="List Feishu group chats the bot has joined",
    )
    parser.add_argument(
        "--test-feishu-app", action="store_true",
        help="Send a test text message via Feishu App bot",
    )
    parser.add_argument(
        "--test-feishu-file", action="store_true",
        help="Send a test Word file to Feishu chat",
    )
    parser.add_argument(
        "--diagnose-collection", action="store_true",
        help="Collect and diagnose duplicates and time issues (READ-ONLY)",
    )
    parser.add_argument(
        "--diagnose-source", type=str, default=None,
        metavar="SOURCE_ID",
        help="Diagnose a specific source by ID (read-only, no DB write)",
    )
    parser.add_argument(
        "--diagnose-file", type=str, default=None,
        metavar="PATH",
        help="Replay diagnosis from saved JSON (no network)",
    )
    args = parser.parse_args()

    project_root = Path(__file__).resolve().parent.parent
    load_environment(project_root)
    settings = get_settings(project_root, load_env=False)
    config_path = settings.sources_config_path
    sources = load_sources(config_path)
    validate_sources_config(sources, COLLECTOR_MAP)
    registry_missing = validate_registry_against_sources(sources)
    if registry_missing:
        logger.warning(
            "Source registry active entries missing from config: %s",
            ", ".join(registry_missing),
        )
    importance_rules_path = project_root / 'config' / 'importance_rules.yaml'
    if importance_rules_path.exists():
        importance_rules_config = load_rules(importance_rules_path)
    else:
        importance_rules_config = {'enabled': False, 'thresholds': {}, 'rules': []}
    importance_errors = validate_rules_config(importance_rules_config)
    if importance_errors:
        msg = "重要度规则配置无效：" + "；".join(importance_errors)
        logger.error(msg)
        print(msg)
        return
    content_filter_config = load_content_filter(
        project_root / "config" / "content_filter.yaml"
    )
    if content_filter_config.get("enabled", False):
        logger.info(
            "Content filter enabled (mode=%s)",
            content_filter_config.get("mode", "drop_before_save"),
        )
    military_config = load_military_config(
        project_root / "config" / "military.yaml"
    )
    if military_config.get("enabled", False):
        logger.info("Military topic filtering enabled")
    else:
        logger.info("Military topic filtering disabled (config missing or invalid)")
    international_config = load_international_config(
        project_root / "config" / "international_media.yaml"
    )
    if international_config.get("enabled", False):
        logger.info("International media layer enabled")
    else:
        logger.info("International media layer disabled (config missing or disabled)")
    election2026_config = load_election2026_config(
        project_root / "config" / "election_2026.yaml"
    )
    election2026_entities = load_election2026_entities(
        project_root / "config" / "election_2026_entities.yaml"
    )
    if election2026_config.get("enabled", False):
        logger.info("2026 九合一选举专题 enabled")
    else:
        logger.info("2026 九合一选举专题 disabled (config missing or disabled)")
    db_path = settings.news_db_path
    health_path = Path(os.getenv("INTERNATIONAL_SOURCE_HEALTH_PATH", "")) if os.getenv("INTERNATIONAL_SOURCE_HEALTH_PATH", "").strip() else project_root / "data" / "international_source_health.json"
    health_store = SourceHealthStore(health_path)

    # Setup file logging (console stays as print())
    setup_logging(project_root / "data" / "monitor.log", settings.log_level)
    logger.info("=" * 50)
    logger.info("Taiwan News Monitor started")
    logger.info(
        "Args: %s",
        " ".join(sys.argv[1:]) if len(sys.argv) > 1 else "(default)",
    )

    # ---- Bootstrap ----------------------------------------------------
    if args.bootstrap:
        db = Database(db_path)
        db.connect()
        db.create_tables()
        collection = collect_all(
            sources, db, content_filter_config, military_config,
            health_store=health_store,
            election_config=election2026_config,
            election_entities=election2026_entities,
        )
        inserted = collection.inserted_articles
        total = collection.total_fetched
        dup = collection.duplicate_count
        failed = collection.failed_sources
        run_removed = collection.run_removed
        hist_id_dup = collection.historical_identity_duplicates
        filtered_count = collection.filtered_count
        print()
        print("初始化完成：")
        print(f"  本轮采集：{total}条")
        print(f"  新增入库：{len(inserted)}条")
        print(f"  重复跳过：{dup}条")
        if filtered_count:
            print(f"  内容过滤：{filtered_count}条")
        if failed:
            print(f"\n失败来源：{len(failed)}个（{', '.join(failed)}）")
        else:
            print("  失败来源：0个")
        logger.info(
            "Bootstrap done: total=%d, new=%d, dup=%d, filtered=%d, failed=%d",
            total, len(inserted), dup, filtered_count, len(failed),
        )
        db.close()
        logger.info("Taiwan News Monitor ended")
        return

    # ---- Test Notify --------------------------------------------------
    if args.test_notify:
        notifier = create_notifier()
        test_msg = (
            "【台湾新闻监测 - 测试消息】\n\n"
            "这是一条测试通知，用于验证通知渠道是否正常。\n"
            f"发送时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        )
        notifier.send(test_msg)
        print("测试通知已发送。")
        return

    # ---- DB Stats -----------------------------------------------------
    if args.db_stats:
        if not db_path.exists():
            print("数据库不存在，请先运行 python -m app.main --bootstrap")
            return
        db = Database(db_path)
        db.connect()
        show_db_stats(db)
        db.close()
        return


    # ---- List Feishu Chats --------------------------------------------
    if args.list_feishu_chats:

        app_id = os.getenv("FEISHU_APP_ID", "").strip()
        app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        if not app_id or not app_secret:
            print("\u8bf7\u8bbe\u7f6e FEISHU_APP_ID \u548c FEISHU_APP_SECRET \u73af\u5883\u53d8\u91cf\u3002")
            return
        try:
            from .feishu import list_bot_chats
            chats = list_bot_chats(app_id, app_secret)
            if not chats:
                print("\u5f53\u524d\u5e94\u7528\u673a\u5668\u4eba\u5c1a\u672a\u52a0\u5165\u4efb\u4f55\u7fa4\u804a\u3002")
                return
            print("\u673a\u5668\u4eba\u6240\u5728\u7fa4\u804a\uff1a\n")
            for i, chat in enumerate(chats, 1):
                name = chat.get("name", "(\u672a\u547d\u540d)")
                chat_id = chat.get("chat_id", "")
                print(f"{i}. {name}")
                print(f"   chat_id: {chat_id}")
                print()
        except RuntimeError as e:
            print(f"\u83b7\u53d6\u7fa4\u804a\u5217\u8868\u5931\u8d25: {e}")
        return


    # ---- Test Feishu App ----------------------------------------------
    if args.test_feishu_app:

        app_id = os.getenv("FEISHU_APP_ID", "").strip()
        app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        chat_id = os.getenv("FEISHU_CHAT_ID", "").strip()
        missing = []
        if not app_id:
            missing.append("FEISHU_APP_ID")
        if not app_secret:
            missing.append("FEISHU_APP_SECRET")
        if not chat_id:
            missing.append("FEISHU_CHAT_ID")
        if missing:
            print(f"\u8bf7\u8bbe\u7f6e\u73af\u5883\u53d8\u91cf: {', '.join(missing)}")
            return
        try:
            from .feishu import send_text
            send_text("\u53f0\u6e7e\u65b0\u95fb\u76d1\u6d4b\u673a\u5668\u4eba\u8fde\u63a5\u6d4b\u8bd5\u6210\u529f", app_id, app_secret, chat_id)
            print("\u6d4b\u8bd5\u6d88\u606f\u5df2\u53d1\u9001\uff0c\u8bf7\u68c0\u67e5\u624b\u673a\u98de\u4e66\u7fa4\u3002")
        except RuntimeError as e:
            print(f"\u53d1\u9001\u5931\u8d25: {e}")
        return

    # ---- Test Feishu File ---------------------------------------------
    if args.test_feishu_file:

        app_id = os.getenv("FEISHU_APP_ID", "").strip()
        app_secret = os.getenv("FEISHU_APP_SECRET", "").strip()
        chat_id = os.getenv("FEISHU_CHAT_ID", "").strip()
        missing = []
        if not app_id: missing.append("FEISHU_APP_ID")
        if not app_secret: missing.append("FEISHU_APP_SECRET")
        if not chat_id: missing.append("FEISHU_CHAT_ID")
        if missing:
            print(f"\u8bf7\u8bbe\u7f6e\u73af\u5883\u53d8\u91cf: {', '.join(missing)}")
            return
        db_path = settings.news_db_path
        if not db_path.exists():
            print("\u6570\u636e\u5e93\u4e0d\u5b58\u5728\uff0c\u8bf7\u5148\u8fd0\u884c python -m app.main --bootstrap")
            return
        db = Database(db_path)
        db.connect()
        now = datetime.now(TAIPEI)
        articles = db.get_articles_since(
            datetime(2000, 1, 1), eligible_only=True
        )
        articles = articles[-10:] if len(articles) > 10 else articles
        if not articles:
            print("\u6570\u636e\u5e93\u4e2d\u6682\u65e0\u65b0\u95fb\u3002")
            db.close()
            return
        output_dir = project_root / "data" / "reports"
        enrich_summaries_safe(articles, db)
        output_path = build_word_digest(
            articles,
            output_dir,
            generated_at=now,
            military_topic_urls=_get_topic_urls_safe(articles, db, "military"),
            social_items=_get_social_word_items(db),
        )
        test_name = f"\u53f0\u6e7e\u65b0\u95fb\u76d1\u6d4b_\u6d4b\u8bd5_{now.strftime('%Y-%m-%d_%H%M')}.docx"
        test_path = output_path.parent / test_name
        if test_path.exists():
            test_path.unlink()
        output_path.rename(test_path)
        try:
            caption = "\u3010\u53f0\u6e7e\u65b0\u95fb\u76d1\u6d4b\uff5cWord\u9644\u4ef6\u6d4b\u8bd5\u3011"
            send_document(test_path, app_id, app_secret, chat_id, caption=caption)
            print(f"Word\u9644\u4ef6\u6d4b\u8bd5\u5b8c\u6210\u3002" + "\n" + f"\u6587\u4ef6\uff1a{test_path}")
            logger.info("Feishu file test complete: %s", test_path)
        except Exception as e:
            print(f"\u53d1\u9001\u5931\u8d25: {e}" + "\n" + f"\u672c\u5730\u6587\u4ef6\u4ecd\u4fdd\u7559: {test_path}")
            logger.warning("Feishu file test failed: %s", e)
        db.close()
        return

    # ---- Diagnose Collection ------------------------------------------
    if args.diagnose_collection:

        dbp = settings.database_path
        diag_db = dbp
        if not diag_db.exists():
            print(f"诊断数据库不存在: {diag_db}")
            print("请将备份数据库复制到配置的 NEWS_DB_PATH/DATABASE_PATH")
            return
        db_obj = Database(diag_db, read_only=True)
        db_obj.connect()
        print()
        print("运行环境：development")
        print(f"数据库：{dbp}")
        print("通知渠道：console")
        print("飞书发送：已禁用")
        print()
        out_dir = project_root / "data" / "diagnostics"
        from .diagnose import run_diagnosis
        run_diagnosis(sources, db_obj, out_dir)
        print()
        print(f"诊断CSV: {out_dir / 'latest_collection.csv'}")
        print(f"诊断报告: {out_dir / 'latest_diagnosis.md'}")
        db_obj.close()
        return

    # ---- Diagnose File (offline replay; read-only for production DB) ----
    if args.diagnose_file:
        json_path = Path(args.diagnose_file)
        if not json_path.exists():
            print(f"诊断文件不存在: {json_path}")
            return
        out_dir = project_root / "data" / "diagnostics"
        from .diagnose import run_diagnosis_from_file

        db_obj = None
        if settings.news_db_path.exists():
            db_obj = Database(settings.news_db_path, read_only=True)
            db_obj.connect()
        try:
            run_diagnosis_from_file(json_path, db_obj, out_dir)
            print(f"诊断回放完成: {out_dir / 'latest_diagnosis.md'}")
        finally:
            if db_obj is not None:
                db_obj.close()
        return

    # ---- Dry Run ------------------------------------------------------
    if args.dry_run:
        # Dry-run is a full international delivery rehearsal, but all state
        # is isolated under a disposable temporary workspace and notifications
        # are no-op.  In particular it must not read/write production DB,
        # reports, health or notification-dedup state.
        dry_root = Path(tempfile.mkdtemp(prefix="tw-news-monitor-dry-run-"))
        tmp_path = dry_root / "news.db"
        tmp_reports = dry_root / "reports"
        tmp_health = SourceHealthStore(dry_root / "international_source_health.json")
        db = Database(tmp_path)
        db.connect()
        try:
            db.create_tables()
            source_baselines = _get_source_baselines(db, sources)
            continuity_now = datetime.now(TAIPEI)
            source_continuity = _get_source_continuity(
                tmp_health, sources, continuity_now,
                settings.date_only_recovery_max_gap_hours,
            )
            collection = collect_all(
                sources, db, content_filter_config, military_config,
                health_store=tmp_health,
                election_config=election2026_config,
                election_entities=election2026_entities,
            )
            inserted = collection.inserted_articles
            total = collection.total_fetched
            dup = collection.duplicate_count
            failed = collection.failed_sources
            run_removed = collection.run_removed
            hist_id_dup = collection.historical_identity_duplicates
            filtered_count = collection.filtered_count
            now = datetime.now(TAIPEI)
            delivery_input = list(inserted) + list(collection.promoted_delivery_articles)
            excluded_delivery_urls = {a.url for a in collection.filtered_from_delivery}
            delivery = run_delivery_core(
                delivery_input,
                source_baselines,
                now,
                international_config=international_config,
                importance_rules_config=importance_rules_config,
                prepare_international_delivery=prepare_international_delivery,
                enrich_summaries=lambda articles: enrich_summaries_safe(articles, db),
                excluded_delivery_urls=excluded_delivery_urls,
                source_continuity=source_continuity,
                catch_up_enabled=False,
            )
            fresh_articles = delivery.fresh_articles
            delivery_articles = delivery.delivery_articles
            digest_articles = delivery.digest_articles
            intl_coverage = delivery.international_coverage
            importance_results = delivery.importance_results
            election_annotations = collection.election_annotations
            translator = _build_international_translator()
            translation_articles = _translation_articles_for_delivery(
                delivery_articles, intl_coverage
            )
            international_translations = _precompute_international_translations(
                translation_articles, international_config, translator=translator
            )
            notification_translator = _build_notification_translation_lookup(
                translation_articles, international_translations, intl_coverage
            )
            event_candidates = []
            try:
                relevant_delivery, _ = filter_international(
                    delivery_articles, international_config or {}
                )
                event_clusters, _ = cluster_international_articles(
                    relevant_delivery, international_config or {}
                )
                event_candidates = build_notification_candidates(
                    event_clusters,
                    importance_results,
                    {
                        "fresh_articles": fresh_articles,
                        "catch_up_urls": delivery.catch_up_urls,
                        "baseline_excluded": delivery.baseline_excluded,
                        "stale_articles": delivery.stale_articles,
                        "unknown_articles": delivery.unknown_time_articles,
                        "future_articles": delivery.future_articles,
                    },
                    now,
                    translator=notification_translator,
                    dedup_store=None,
                )
                NullNotifier().send_event_candidates(event_candidates)
            except Exception as event_err:
                logger.warning("Dry-run international event path failed safely: %s", event_err)

            long_digest_articles = [
                article for article in digest_articles
                if not is_international_media(article.source_name, international_config)
            ]
            if long_digest_articles:
                digest = build_digest(
                    long_digest_articles, now,
                    international_coverage=intl_coverage,
                    international_config=international_config,
                    include_international_media=False,
                )
            else:
                digest = build_digest([], now)
            digest += (
                f"\n本轮收集：{total}条\n"
                f"新增入库：{len(inserted)}条\n"
                f"重复跳过：{dup}条\n"
                f"相关/Word：{len(digest_articles)}条\n"
                f"国际事件候选：{len(event_candidates)}条\n"
            )
            if filtered_count:
                digest += f"内容过滤：{filtered_count}条\n"
            digest += f"失败来源：{len(failed)}个"
            print(digest)

            if digest_articles:
                word_path = build_word_digest(
                    digest_articles,
                    tmp_reports,
                    generated_at=now,
                    catch_up_urls=delivery.catch_up_urls,
                    importance_results=importance_results,
                    international_config=international_config,
                    international_coverage=intl_coverage,
                    international_translations=international_translations,
                    election_config=election2026_config,
                    election_entities=election2026_entities,
                    election_annotations=election_annotations,
                    military_topic_urls=_get_topic_urls_safe(
                        digest_articles, db, "military"
                    ),
                    social_items=_get_social_word_items(db),
                )
                print(f"Dry-run Word：{word_path}")
        finally:
            db.close()
            shutil.rmtree(dry_root, ignore_errors=True)
        return


    # ---- Diagnose Source ---------------------------------------------
    if args.diagnose_source:
        #from .article_identity import article_identity_key
        #from .freshness import filter_fresh_articles
        #from .time_utils import TAIPEI
        print(f"\n=== Diagnose source: {args.diagnose_source} ===\n")
        matched = [s for s in sources if s["id"] == args.diagnose_source]
        if not matched:
            print(f"Source not found: {args.diagnose_source}")
            return
        source = matched[0]
        cls = COLLECTOR_MAP.get(source.get("type", ""))
        if not cls:
            print(f"Unknown collector type: {source.get('type')}")
            return
        if source.get("enabled") is False:
            print(f"  [WARN] Source is disabled (enabled=false)")
        collector = cls(source)
        try:
            articles = collector.collect()
            print(f"  HTTP: 200 (via collector)")
            print(f"  Items in response: {len(articles)}")
            print(f"  Successfully parsed: {len(articles)}")
            aware_count = sum(1 for a in articles if a.published_at and a.published_at.tzinfo)
            naive_count = sum(1 for a in articles if a.published_at and not a.published_at.tzinfo)
            unknown_count = sum(1 for a in articles if not a.published_at)
            now = datetime.now(TAIPEI)
            fr = filter_fresh_articles(articles, now)
            print(f"  Aware times: {aware_count}")
            print(f"  Naive times: {naive_count}")
            print(f"  Unknown times: {unknown_count}")
            print(f"  Fresh: {len(fr.fresh_articles)}")
            if fr.fresh_articles:
                print(f"  Latest: {fr.fresh_articles[0].published_at}")
                print(f"  Oldest: {fr.fresh_articles[-1].published_at}")
            print(f"  URL duplicates: {len(articles) - len(set(a.url for a in articles))}")
            print()
            print("  First 5 articles:")
            for a in articles[:5]:
                print(f"    {a.title[:50]}")
                print(f"    {a.url}")
                print(f"    {a.published_at}")
                print()
        except Exception as e:
            print(f"  ERROR: {e}")
        finally:
            collector.close()
        print("=== Diagnosis complete (read-only, no DB written) ===\n")
        return
    # ---- Export Word ------------------------------------------------
    # ---- Export Word ---------------------------------------------------
    if args.export_word is not None:
        if not db_path.exists():
            print("数据库不存在或为空，请先运行 python -m app.main --bootstrap")
            return
        db = Database(db_path)
        db.connect()
        now = datetime.now(TAIPEI)
        articles = db.get_articles_since(
            datetime(2000, 1, 1), eligible_only=True
        )
        if len(articles) > args.export_word:
            articles = articles[-args.export_word:]
        if not articles:
            print("数据库中暂无新闻。")
            db.close()
            return
        output_dir = project_root / "data" / "reports"
        enrich_summaries_safe(articles, db)
        word_articles, intl_coverage = prepare_international_delivery(
            articles, international_config
        )
        if not word_articles:
            print("没有符合简报范围的新闻。")
            db.close()
            return
        international_translator = _build_international_translator()
        translation_articles = _translation_articles_for_delivery(
            articles, intl_coverage
        )
        international_translations = _precompute_international_translations(
            translation_articles,
            international_config,
            translator=international_translator,
        )
        election_annotations = _classify_and_persist_election2026(
            word_articles, db,
            election2026_config, election2026_entities,
        )
        output_path = build_word_digest(
            word_articles, output_dir, generated_at=now,
            international_config=international_config,
            international_coverage=intl_coverage,
            international_translations=international_translations,
            election_config=election2026_config,
            election_entities=election2026_entities,
            election_annotations=election_annotations,
            military_topic_urls=_get_topic_urls_safe(
                word_articles, db, "military"
            ),
            social_items=_get_social_word_items(db),
        )
        if hasattr(db, "mark_articles_delivered"):
            db.mark_articles_delivered(
                [article.url for article in word_articles], now
            )
        print(f"Word简报已生成：\n{output_path}")
        logger.info("Word export complete: %s", output_path)
        db.close()
        return

    # ---- Backfill Run -------------------------------------------------
    if args.backfill_run:
        if not db_path.exists():
            print("数据库不存在，请先运行 python -m app.main --bootstrap")
            return
        db = Database(db_path)
        db.connect()

        batch_id = args.backfill_run

        # Check idempotency marker
        marker_dir = project_root / "data" / "backfill_markers"
        marker_path = marker_dir / f"catchup_{batch_id}.json"
        if marker_path.exists():
            # json already imported above
            try:
                marker = json.loads(marker_path.read_text(encoding="utf-8"))
                if marker.get("sent_at") and marker.get("feishu_result") == "success":
                    print(f"批次 {batch_id} 已于 {marker['sent_at']} 成功发送。")
                    print("如需重发，请先删除标记文件：")
                    print(f"  Remove-Item '{marker_path}'")
                    db.close()
                    return
            except Exception:
                pass

        # Get this batch's articles: from a specific time range
        # The batch_id is the run timestamp in format like "20260719_0842"
        # json already imported above
        print(f"=== Backfill run: {batch_id} ===\n")

        now = datetime.now(TAIPEI)
        # Try to parse batch_id as a datetime range marker
        # Format: YYYYMMDD_HHMM (run time)
        batch_dt = None
        try:
            batch_dt = datetime.strptime(batch_id, "%Y%m%d_%H%M")
            batch_dt = batch_dt.replace(tzinfo=TAIPEI)
        except (ValueError, TypeError):
            print(f"批次格式无效: {batch_id}，应为 YYYYMMDD_HHMM")
            db.close()
            return

        # Fetch articles inserted in a 2-hour window around the batch time.
        # Use the canonical Database parser so precision/eligibility/filter
        # metadata survives the backfill round-trip.
        batch_start = batch_dt - timedelta(hours=1)
        batch_end = batch_dt + timedelta(hours=1)
        try:
            articles = db.get_articles_between(
                batch_start, batch_end, eligible_only=True
            )
        except Exception as e:
            print(f"查询失败: {e}")
            db.close()
            return

        if not articles:
            print(f"批次 {batch_id} 窗口内没有文章。")
            db.close()
            return

        print(f"Candidate count: {len(articles)}")
        print(f"Will send: {'YES' if args.send else 'NO'}")
        print()
        for i, a in enumerate(articles, 1):
            age = "N/A"
            if a.published_at:
                age_mins = int((now.astimezone(TAIPEI) - a.published_at.astimezone(TAIPEI)).total_seconds() / 60)
                age = f"{age_mins}分钟"
            print(f"{i}. ID={a.url[:40]} / {a.source_name} / {a.published_at} / {age}")
            print(f"   {a.title[:60]}")
            print()

        if not args.send:
            print("=== Dry-run complete (use --send to actually send) ===\n")
            db.close()
            return

        # Actually generate Word and send
        print("=== Generating backfill Word document ===\n")
        try:
            # Mark ALL articles as catch_up for the backfill Word
            all_catch_up = {a.url for a in articles}
            output_dir = project_root / "data" / "reports"
            output_dir.mkdir(parents=True, exist_ok=True)
            enrich_summaries_safe(articles, db)
            word_path = build_word_digest(
                articles, output_dir, generated_at=now,
                catch_up_urls=all_catch_up,
                election_config=election2026_config,
                election_entities=election2026_entities,
                military_topic_urls=_get_topic_urls_safe(
                    articles, db, "military"
                ),
                social_items=_get_social_word_items(db),
            )
            if hasattr(db, "mark_articles_delivered"):
                db.mark_articles_delivered([article.url for article in articles], now)
            print(f"Word generated: {word_path}")

            # Send to Feishu
            import os as _os2
            fs_id = _os2.getenv("FEISHU_APP_ID", "").strip()
            fs_secret = _os2.getenv("FEISHU_APP_SECRET", "").strip()
            fs_chat = _os2.getenv("FEISHU_CHAT_ID", "").strip()
            feishu_result = "not_configured"
            if fs_id and fs_secret and fs_chat:
                caption = "【台湾新闻监测｜补发简报】"
                send_document(word_path, fs_id, fs_secret, fs_chat, caption=caption)
                feishu_result = "success"
                print("Feishu: sent successfully")
            else:
                print("Feishu: not configured, skipping send")

            # Write idempotency marker
            marker_dir.mkdir(parents=True, exist_ok=True)
            marker = {
                "batch_id": batch_id,
                "article_count": len(articles),
                "article_ids": [a.url[:60] for a in articles],
                "word_path": str(word_path),
                "sent_at": now.isoformat(),
                "feishu_result": feishu_result,
            }
            marker_path.write_text(json.dumps(marker, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\nIdempotency marker written: {marker_path}")

        except Exception as e:
            print(f"Backfill failed: {e}")
            logger.exception("Backfill run failed: batch_id=%s", batch_id)

        db.close()
        return

        # ---- Default: collect + save + notify with lock -------------------
    lock_path = project_root / "data" / "monitor.lock"
    lock = InstanceLock(lock_path)
    if not lock.acquire():
        msg = "已有另一个采集实例在运行，本次跳过。"
        print(msg)
        logger.warning("Lock acquisition failed, another instance is running")
        return

    db = None
    try:
        db = Database(db_path)
        db.connect()
        db.create_tables()

        # Capture baselines before insertion. A newly enabled source with no
        # history must not deliver old catch-up entries on its first run.
        source_baselines = _get_source_baselines(db, sources)
        continuity_now = datetime.now(TAIPEI)
        source_continuity = _get_source_continuity(
            health_store, sources, continuity_now,
            settings.date_only_recovery_max_gap_hours,
        )

        collection = collect_all(
            sources, db, content_filter_config, military_config,
            health_store=health_store,
            election_config=election2026_config,
            election_entities=election2026_entities,
        )
        inserted = collection.inserted_articles
        total = collection.total_fetched
        dup = collection.duplicate_count
        failed = collection.failed_sources
        run_removed = collection.run_removed
        hist_id_dup = collection.historical_identity_duplicates
        filtered_count = collection.filtered_count
        now = datetime.now(TAIPEI)
        delivery_input = list(inserted) + list(collection.promoted_delivery_articles)

        notifier = create_notifier()
        international_translator = _build_international_translator()

        # Load catch-up configuration
        catch_up_enabled = settings.news_catchup_enabled
        catch_up_max_minutes = settings.news_catchup_max_minutes

        # Log configuration
        logger.info(
            "Catch-up delivery: %s | Freshness window: %d min | Catch-up window: %d min",
            "enabled" if catch_up_enabled else "disabled",
            90, catch_up_max_minutes,
        )

        # Validate catch-up configuration
        if catch_up_enabled and catch_up_max_minutes <= 90:
            msg = (
                f"NEWS_CATCHUP_MAX_MINUTES ({catch_up_max_minutes}) must be greater than "
                f"NEWS_FRESHNESS_MINUTES (90)"
            )
            logger.error(msg)
            print(msg)
            return

        # Freshness, international routing, enrichment and importance use the
        # same core in production and dry-run.  Only their stateful callbacks
        # differ (production DB versus temporary DB).
        db_existing = dup - run_removed
        excluded_delivery_urls = {a.url for a in collection.filtered_from_delivery}
        delivery = run_delivery_core(
            delivery_input,
            source_baselines,
            now,
            international_config=international_config,
            importance_rules_config=importance_rules_config,
            prepare_international_delivery=prepare_international_delivery,
            enrich_summaries=lambda articles: enrich_summaries_safe(articles, db),
            excluded_delivery_urls=excluded_delivery_urls,
            source_continuity=source_continuity,
            catch_up_enabled=catch_up_enabled,
            catch_up_max_minutes=catch_up_max_minutes,
        )
        fresh_articles = delivery.fresh_articles
        catch_up_eligible = delivery.catch_up_articles
        catch_up_urls = delivery.catch_up_urls
        stale_articles = delivery.stale_articles
        unknown_articles = delivery.unknown_time_articles
        future_articles = delivery.future_articles
        baseline_excluded = delivery.baseline_excluded
        delivery_articles = delivery.delivery_articles
        digest_articles = delivery.digest_articles
        intl_coverage = delivery.international_coverage
        importance_results = delivery.importance_results
        pre_cap_summary = delivery.pre_cap_importance_summary
        final_word_count = len(digest_articles)
        translation_articles = _translation_articles_for_delivery(
            delivery_articles, intl_coverage
        )
        international_translations = _precompute_international_translations(
            translation_articles,
            international_config,
            translator=international_translator,
        )
        notification_translator = _build_notification_translation_lookup(
            translation_articles, international_translations, intl_coverage
        )
        notification_dedup_store = _notification_dedup_store(
            project_root, international_config, dry_run=False
        )
        logger.info(
            "%s | after cap: %s",
            pre_cap_summary,
            importance_summary(importance_results),
        )

        # International alerts are event-level and deliberately separate from
        # the existing Taiwan notification path.  The candidate builder only
        # sees fresh, relevant clusters at the final importance score; normal
        # international coverage remains Word-only.
        event_candidates = []
        try:
            relevant_delivery, _ = filter_international(
                delivery_articles, international_config or {}
            )
            event_clusters, _ = cluster_international_articles(
                relevant_delivery, international_config or {}
            )
            event_candidates = build_notification_candidates(
                event_clusters,
                importance_results,
                {
                    "fresh_articles": fresh_articles,
                    "catch_up_urls": catch_up_urls,
                    "baseline_excluded": baseline_excluded,
                    "stale_articles": stale_articles,
                    "unknown_articles": unknown_articles,
                    "future_articles": future_articles,
                },
                now,
                translator=notification_translator,
                dedup_store=notification_dedup_store,
            )
            send_succeeded = _deliver_event_candidates(
                notifier, event_candidates, notification_dedup_store, now
            )
            logger.info(
                "International event candidates: %d (sent=%s)",
                len(event_candidates), send_succeeded,
            )
            if send_succeeded and hasattr(db, "mark_articles_delivered"):
                delivered_event_urls = set()
                for candidate in event_candidates:
                    delivered_event_urls.add(candidate.canonical_url)
                    delivered_event_urls.update(candidate.coverage_urls)
                db.mark_articles_delivered(
                    [url for url in delivered_event_urls if url], now
                )
        except Exception as event_err:
            logger.warning("International event notification failed safely: %s", event_err)

        if digest_articles:
            # Ordinary relevant international stories are Word-only.  The
            # existing Taiwan/local digest keeps its historical categories and
            # importance behavior; international event alerts use the
            # event-candidate lane above.
            long_digest_articles = [
                article for article in digest_articles
                if not is_international_media(article.source_name, international_config)
            ]
            if long_digest_articles:
                digest = build_digest(
                    long_digest_articles, now,
                    international_coverage=intl_coverage,
                    international_config=international_config,
                    include_international_media=False,
                )
                stats = (
                    f"\n本轮收集：{total}条"
                )
                if failed:
                    _sep = "、"
                    stats += f"\n失败来源：{len(failed)}个（{_sep.join(failed)}）"
                stats += (
                    f"\nURL去重：{run_removed}条"
                    f"\n历史去重：{db_existing}条"
                    f"\n新增入库：{len(inserted)}条"
                    f"\n内容过滤：{filtered_count}条"
                    f"\n正常新闻：{len(fresh_articles)}条"
                    f"\n补发新闻：{len(catch_up_eligible)}条"
                    f"\n过期跳过：{len(stale_articles)}条"
                    f"\n未知时间：{len(unknown_articles)}条"
                    f"\n未来时间：{len(future_articles)}条"
                    f"\n简报Word：{final_word_count}条"
                )
                digest += stats
                notifier.send_long(digest)
            # Auto-generate Word digest for the international-filtered article set
            try:
                output_dir = project_root / "data" / "reports"
                election_annotations = collection.election_annotations
                word_path = build_word_digest(
                    digest_articles, output_dir, generated_at=now,
                    catch_up_urls=catch_up_urls,
                    importance_results=importance_results,
                    international_config=international_config,
                    international_coverage=intl_coverage,
                    international_translations=international_translations,
                    election_config=election2026_config,
                    election_entities=election2026_entities,
                    election_annotations=election_annotations,
                    military_topic_urls=_get_topic_urls_safe(
                        digest_articles, db, "military"
                    ),
                    social_items=_get_social_word_items(db),
                )
                if hasattr(db, "mark_articles_delivered"):
                    db.mark_articles_delivered(
                        [article.url for article in digest_articles], now
                    )
                logger.info("Word digest saved: %s", word_path)
                # Auto-send to Feishu if credentials are available
                try:
                    import os as _os2
                    fs_id = _os2.getenv("FEISHU_APP_ID", "").strip()
                    fs_secret = _os2.getenv("FEISHU_APP_SECRET", "").strip()
                    fs_chat = _os2.getenv("FEISHU_CHAT_ID", "").strip()
                    if fs_id and fs_secret and fs_chat:
                        if not settings.disable_feishu_send:
                            send_document(
                                word_path, fs_id, fs_secret, fs_chat,
                            )
                            logger.info("Word digest sent to Feishu")

                            # Send highlight card if enabled and has highlights
                            card_cfg = importance_rules_config.get("feishu_highlight_card", {})
                            if card_cfg.get("enabled", True):
                                max_h = importance_rules_config.get("display", {}).get("max_highlights", 10)
                                # 高亮卡片只用进入 digest/Word 的集合（canonical +
                                # 非国际文章），排除不相关国际文章与 coverage 成员
                                digest_urls = {
                                    a.url for a in digest_articles
                                    if not is_international_media(
                                        a.source_name, international_config
                                    )
                                }
                                highlights = select_highlights(
                                    [
                                        (a, r) for a, r in importance_results
                                        if a.url in digest_urls
                                    ],
                                    max_highlights=max_h,
                                )
                                if highlights:
                                    # Send directly via the Feishu App bot so the
                                    # card works even when NOTIFIER=console
                                    # (ConsoleNotifier.send_highlight_card returns
                                    # False by design and would silently skip it).
                                    card = build_highlight_card(
                                        highlights,
                                        title=card_cfg.get("title", "本期重点新闻提示"),
                                        show_summary=card_cfg.get("show_summary", False),
                                        show_source=card_cfg.get("show_source", False),
                                        show_published_at=card_cfg.get("show_published_at", False),
                                    )
                                    send_card(card, fs_id, fs_secret, fs_chat)
                                    logger.info(
                                        "Highlight card sent via Feishu App bot "
                                        "(items=%d, critical=%d)",
                                        len(highlights),
                                        sum(1 for _, r in highlights if r.level == "critical"),
                                    )
                        else:
                            logger.info("Feishu send disabled by DISABLE_FEISHU_SEND")
                except Exception as fs_err:
                    logger.warning("Feishu send failed: %s", fs_err)
            except Exception as e:
                logger.warning("Word digest generation failed: %s", e)
            logger.info(
                "Notified: fresh=%d, catch_up=%d, total=%d",
                len(fresh_articles), len(catch_up_eligible), total,
            )
        else:
            if inserted:
                if delivery_articles and not digest_articles:
                    print(
                        f"\n新增{len(inserted)}条，但国际相关性过滤后无可投递新闻："
                        f"\n  Fresh={len(fresh_articles)}, "
                        f"Catch_up={len(catch_up_eligible)}, "
                        f"Stale={len(stale_articles)}, "
                        f"Unknown={len(unknown_articles)}, Future={len(future_articles)}"
                    )
                elif baseline_excluded and not catch_up_eligible:
                    print(
                        f"\n新增{len(inserted)}条，但无符合交付条件的新闻（详情见日志）："
                        f"\n  Fresh=0, Catch_up_ineligible={len(baseline_excluded)}, "
                        f"Stale={len(stale_articles)}, "
                        f"Unknown={len(unknown_articles)}, Future={len(future_articles)}"
                    )
                else:
                    print(
                        f"\n新增{len(inserted)}条，但无符合交付条件的新闻："
                        f"\n  Fresh=0, Stale={len(stale_articles)}, "
                        f"Unknown={len(unknown_articles)}, Future={len(future_articles)}"
                    )
                logger.info(
                    "No notifiable articles after freshness/relevance: inserted=%d, fresh=%d, catch_up_eligible=%d, stale=%d, unknown=%d, future=%d",
                    len(inserted), len(fresh_articles), len(catch_up_eligible),
                    len(stale_articles), len(unknown_articles), len(future_articles),
                )
            else:
                print("本轮没有新增新闻。")
                logger.info("No new articles inserted")
    except SystemExit:
        raise
    except Exception:
        logger.exception("Unhandled exception")
        raise
    finally:
        if db is not None:
            db.close()
        lock.release()
        logger.info("Taiwan News Monitor ended")



if __name__ == "__main__":
    main()
