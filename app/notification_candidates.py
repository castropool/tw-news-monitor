"""Event-level, freshness-aware international notification candidates.

The module is deliberately delivery-only: it does not send anything.  A
caller may inject :class:`NotificationDedupStore` and call ``mark_sent`` only
after its notifier reports success.  With no injected path/store, no file is
created and the normal in-memory candidate behavior is preserved.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Any, Literal

from .importance import ImportanceResult
from .international import evaluate_relevance, is_international_media, load_international_config
from .international_events import EventCluster, normalize_canonical_url
from .international_translation import InternationalNewsTranslator, translate_article


_MIN_TTL_SECONDS = 24 * 60 * 60
_FAIL_CLOSED_PATHS: set[str] = set()
_LOCK_TIMEOUT_SECONDS = 2.0
_LOCK_STALE_SECONDS = 30.0
_LOCK_POLL_SECONDS = 0.01


class NotificationDedupStore:
    """Optional atomic JSON store for successful event notifications.

    ``path`` is intentionally caller-injected.  ``None`` keeps the store
    memory-only, so merely importing or using candidate construction cannot
    write a production file.  Writes use a same-directory temporary file and
    ``os.replace``; a read/write error is fail-closed for the current process
    and suppresses a duplicate rather than risking a second alert.

    The store exposes only the delivery lifecycle operations required by the
    caller: ``is_seen``, ``mark_sent`` and ``prune``.  ``mark_sent`` is never
    called by ``build_notification_candidates``.
    """

    def __init__(
        self,
        path: str | os.PathLike[str] | None = None,
        *,
        ttl: timedelta | int | float = timedelta(days=1),
        lock_timeout_seconds: float = _LOCK_TIMEOUT_SECONDS,
        stale_lock_seconds: float = _LOCK_STALE_SECONDS,
    ) -> None:
        self._path = Path(path) if path is not None else None
        ttl_seconds = ttl.total_seconds() if isinstance(ttl, timedelta) else float(ttl)
        if ttl_seconds < _MIN_TTL_SECONDS:
            raise ValueError("notification dedup TTL must be at least 24 hours")
        self._ttl_seconds = ttl_seconds
        self._lock_timeout_seconds = max(0.0, float(lock_timeout_seconds))
        self._stale_lock_seconds = max(1.0, float(stale_lock_seconds))
        self._entries: dict[str, str] = {}
        path_marker = str(self._path.resolve()) if self._path is not None else ""
        self._fail_closed = bool(path_marker and path_marker in _FAIL_CLOSED_PATHS)

    @property
    def ttl_seconds(self) -> float:
        return self._ttl_seconds

    @staticmethod
    def _utc(value: datetime | None) -> datetime:
        current = value or datetime.now(timezone.utc)
        if current.tzinfo is None:
            return current.replace(tzinfo=timezone.utc)
        return current.astimezone(timezone.utc)

    @staticmethod
    def _parse(value: str) -> datetime:
        parsed = datetime.fromisoformat(str(value))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)

    def _read(self) -> dict[str, str]:
        if self._path is None:
            return dict(self._entries)
        if not self._path.exists():
            return {}
        payload = json.loads(self._path.read_text(encoding="utf-8"))
        entries = payload.get("entries") if isinstance(payload, dict) else None
        if not isinstance(entries, dict):
            raise ValueError("invalid notification dedup JSON")
        if not all(isinstance(key, str) and isinstance(value, str) for key, value in entries.items()):
            raise ValueError("invalid notification dedup entries")
        # Validate timestamps before treating a corrupt file as usable.
        for value in entries.values():
            self._parse(value)
        return dict(entries)

    def _lock_path(self) -> Path | None:
        return self._path.with_name(f"{self._path.name}.lock") if self._path is not None else None

    def _mark_fail_closed(self) -> None:
        self._fail_closed = True
        if self._path is not None:
            _FAIL_CLOSED_PATHS.add(str(self._path.resolve()))

    def _acquire_lock(self) -> str | None:
        """Acquire a Windows-compatible O_EXCL lock, or return ``None``."""

        lock_path = self._lock_path()
        if lock_path is None:
            return "memory-only"
        try:
            lock_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception:
            return None
        token = f"{os.getpid()}:{threading.get_ident()}:{time.monotonic_ns()}"
        deadline = time.monotonic() + self._lock_timeout_seconds
        while True:
            try:
                descriptor = os.open(
                    str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY
                )
                try:
                    with os.fdopen(descriptor, "w", encoding="ascii", newline="\n") as handle:
                        handle.write(token)
                        handle.flush()
                        os.fsync(handle.fileno())
                except Exception:
                    try:
                        lock_path.unlink()
                    except OSError:
                        pass
                    return None
                return token
            except FileExistsError:
                try:
                    age = time.time() - lock_path.stat().st_mtime
                    if age >= self._stale_lock_seconds:
                        lock_path.unlink()
                        continue
                except FileNotFoundError:
                    continue
                except OSError:
                    # A lock that cannot be inspected is unsafe to bypass.
                    return None
                if time.monotonic() >= deadline:
                    return None
                time.sleep(_LOCK_POLL_SECONDS)
            except OSError:
                return None

    def _release_lock(self, token: str | None) -> None:
        lock_path = self._lock_path()
        if lock_path is None or token in (None, "memory-only"):
            return
        try:
            if lock_path.read_text(encoding="ascii") == token:
                lock_path.unlink()
        except (FileNotFoundError, OSError, UnicodeError):
            pass

    def _atomic_write(self, entries: dict[str, str]) -> None:
        if self._path is None:
            self._entries = dict(entries)
            return
        self._path.parent.mkdir(parents=True, exist_ok=True)
        temp_name: str | None = None
        try:
            fd, temp_name = tempfile.mkstemp(
                prefix=f".{self._path.name}.", suffix=".tmp", dir=str(self._path.parent)
            )
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(
                    {"version": 1, "entries": dict(sorted(entries.items()))},
                    handle,
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                )
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temp_name, self._path)
            temp_name = None
            self._entries = dict(entries)
        finally:
            if temp_name:
                try:
                    os.unlink(temp_name)
                except OSError:
                    pass

    def is_seen(self, dedup_key: str, now: datetime | None = None) -> bool:
        """Return whether a successful send is still within the TTL."""

        key = str(dedup_key or "").strip()
        if not key or self._fail_closed:
            return True
        try:
            entries = self._read()
            stamp = entries.get(key)
            if stamp is None:
                return False
            age = (self._utc(now) - self._parse(stamp)).total_seconds()
            return age < self._ttl_seconds
        except Exception:
            self._mark_fail_closed()
            return True

    def mark_sent(self, dedup_key: str, now: datetime | None = None) -> None:
        """Record a key after a notifier confirms successful delivery."""

        key = str(dedup_key or "").strip()
        if not key:
            self._fail_closed = True
            return
        lock_token: str | None = None
        try:
            lock_token = self._acquire_lock()
            if lock_token is None:
                raise OSError("notification dedup lock unavailable")
            entries = self._read()
            entries[key] = self._utc(now).isoformat()
            self._atomic_write(entries)
        except Exception:
            # A failed persistence operation must not make a retry look safe.
            self._mark_fail_closed()
        finally:
            self._release_lock(lock_token)

    def prune(self, now: datetime | None = None) -> None:
        """Remove entries older than the configured TTL."""

        if self._fail_closed:
            return
        lock_token: str | None = None
        try:
            lock_token = self._acquire_lock()
            if lock_token is None:
                raise OSError("notification dedup lock unavailable")
            entries = self._read()
            cutoff = self._utc(now).timestamp() - self._ttl_seconds
            retained = {
                key: stamp
                for key, stamp in entries.items()
                if self._parse(stamp).timestamp() >= cutoff
            }
            if retained != entries:
                self._atomic_write(retained)
            else:
                self._entries = dict(entries)
        except Exception:
            self._mark_fail_closed()
        finally:
            self._release_lock(lock_token)


@dataclass(frozen=True, slots=True)
class NotificationCandidate:
    event_id: str
    canonical_url: str
    cn_title: str
    importance_level: Literal["important", "critical"]
    score: int
    relevance_reason: str
    coverage_source_ids: list[str]
    coverage_urls: list[str]
    dedup_key: str
    notifiable: bool
    freshness_status: str = "fresh"
    baseline_excluded: bool = False
    evidence_url: str = ""
    evidence_source_id: str = ""

    @property
    def notification_evidence_url(self) -> str:
        """Compatibility alias for delivery owners consuming the evidence."""

        return self.evidence_url


def _urls(values: Any) -> set[str]:
    if values is None:
        return set()
    if isinstance(values, dict):
        values = values.values()
    if isinstance(values, (str, bytes)):
        values = [values]
    result: set[str] = set()
    for value in values or []:
        result.add(str(getattr(value, "url", value)))
    return result


def _event_articles(cluster: EventCluster) -> list[Any]:
    """Return canonical/member/coverage articles once, preserving event data."""

    result: list[Any] = []
    seen: set[str] = set()
    canonical = getattr(cluster, "canonical", None)
    values = ([canonical] if canonical is not None else []) + list(getattr(cluster, "members", []) or []) + list(getattr(cluster, "coverage", []) or [])
    for article in values:
        if article is None:
            continue
        marker = str(getattr(article, "url", ""))
        if marker in seen:
            continue
        seen.add(marker)
        result.append(article)
    return result


def _url_matches(url: str, candidates: set[str]) -> bool:
    if url in candidates:
        return True
    normalized = normalize_canonical_url(url)
    return bool(normalized and any(normalized == normalize_canonical_url(item) for item in candidates))


def _fresh_and_excluded_urls(
    freshness_state: Any,
) -> tuple[set[str], set[str], set[str]] | None:
    """Return fresh, excluded and explicitly classified URLs.

    Missing state is deliberately different from an empty exclusion set:
    delivery must not infer freshness from the current clock or from the
    article timestamp.  Every event member/coverage item must occur in one of
    the explicit freshness buckets before an event can be notified.
    """

    if freshness_state is None:
        return None
    if isinstance(freshness_state, dict):
        fresh = _urls(
            freshness_state.get(
                "fresh_articles",
                freshness_state.get("fresh_urls", freshness_state.get("fresh")),
            )
        )
        excluded = set()
        classified = set(fresh)
        # Catch-up is a known, non-fresh state.  It is not itself eligible for
        # the alert, but it may be the old canonical beside a fresh coverage.
        for key in ("catch_up_urls", "catch_up_articles", "catch_up"):
            classified.update(_urls(freshness_state.get(key)))
        for key in (
            "catch_up_urls", "catch_up_articles", "baseline_excluded", "stale_articles",
            "unknown_articles", "future_articles", "old_articles", "unknown_time_articles",
            "future_time_articles",
        ):
            values = _urls(freshness_state.get(key))
            classified.update(values)
            if key != "catch_up_urls" and key != "catch_up_articles":
                excluded.update(values)
        for mapping_key in ("status_by_url", "statuses", "state_by_url"):
            mapping = freshness_state.get(mapping_key)
            if isinstance(mapping, dict):
                for url, status in mapping.items():
                    marker = str(url)
                    state = str(status or "").strip().lower()
                    if marker:
                        classified.add(marker)
                        if state == "fresh":
                            fresh.add(marker)
                        elif state:
                            excluded.add(marker)
        # A state object that carries no classification is treated as unknown;
        # callers must explicitly opt into fresh delivery rather than widening
        # an alert after a failed freshness check.
        return fresh, excluded, classified
    fresh_values = getattr(
        freshness_state,
        "fresh_articles",
        getattr(freshness_state, "fresh_urls", getattr(freshness_state, "fresh", None)),
    )
    fresh = _urls(fresh_values)
    excluded = set()
    classified = set(fresh)
    classified.update(_urls(getattr(freshness_state, "catch_up_articles", None)))
    for attr in ("stale_articles", "unknown_time_articles", "future_time_articles"):
        values = _urls(getattr(freshness_state, attr, None))
        classified.update(values)
        excluded.update(values)
    return fresh, excluded, classified


def _importance_matches(cluster: EventCluster, importance_results: Any) -> list[ImportanceResult]:
    members = _event_articles(cluster)
    by_url = {str(article.url): article for article in members}
    matched: list[ImportanceResult] = []
    if isinstance(importance_results, dict):
        values = importance_results.items()
        for key, value in values:
            if str(getattr(key, "url", key)) in by_url or key == getattr(cluster, "event_id", None):
                result = value[1] if isinstance(value, tuple) and len(value) == 2 else value
                if isinstance(result, ImportanceResult) or hasattr(result, "level"):
                    matched.append(result)
        return matched
    for item in importance_results or []:
        article = result = None
        if isinstance(item, tuple) and len(item) == 2:
            article, result = item
        elif hasattr(item, "level"):
            result = item
        if result is None or not hasattr(result, "level"):
            continue
        if article is None or str(getattr(article, "url", "")) in by_url:
            matched.append(result)
    return matched


def _best_importance(cluster: EventCluster, importance_results: Any) -> ImportanceResult | None:
    results = _importance_matches(cluster, importance_results)
    if not results:
        return None
    rank = {"critical": 0, "important": 1, "normal": 2}
    return max(results, key=lambda result: (int(getattr(result, "score", 0)), -rank.get(getattr(result, "level", "normal"), 2)))


def _relevance(cluster: EventCluster) -> tuple[bool, str]:
    explicit = getattr(cluster, "relevance", None) or getattr(cluster, "relevance_decision", None)
    if explicit is not None and hasattr(explicit, "relevant"):
        return bool(explicit.relevant), str(getattr(explicit, "reason", ""))
    if hasattr(cluster, "relevant"):
        return bool(getattr(cluster, "relevant")), str(
            getattr(cluster, "relevance_reason", getattr(cluster, "reason", ""))
        )
    canonical = getattr(cluster, "canonical", None)
    if canonical is None:
        return False, "cluster has no canonical article"
    decision = evaluate_relevance(
        canonical.title,
        canonical.summary,
        canonical.source_name,
        load_international_config(),
    )
    return bool(decision.relevant), decision.reason


def _cn_title(
    cluster: EventCluster,
    evidence: Any,
    translator: InternationalNewsTranslator | None,
) -> str:
    value = getattr(cluster, "cn_title", None)
    if isinstance(value, str) and value.strip():
        return value.strip()
    translated = translate_article(evidence or cluster.canonical, translator=translator)
    return translated.cn_title or str(getattr(evidence or cluster.canonical, "title", ""))


def _latest_fresh_member(articles: list[Any], fresh_urls: set[str], excluded_urls: set[str]) -> Any | None:
    fresh = [
        article
        for article in articles
        if _url_matches(str(getattr(article, "url", "")), fresh_urls)
        and not _url_matches(str(getattr(article, "url", "")), excluded_urls)
    ]
    if not fresh:
        return None

    def key(article: Any) -> tuple[float, str, str]:
        value = getattr(article, "published_at", None) or getattr(article, "fetched_at", None)
        try:
            timestamp = value.timestamp() if value is not None else float("-inf")
        except (AttributeError, OverflowError, ValueError):
            timestamp = float("-inf")
        return timestamp, str(getattr(article, "source_id", "")), str(getattr(article, "url", ""))

    return max(fresh, key=key)


def _stable_dedup_key(cluster: EventCluster, canonical_url: str) -> str:
    event_id = str(getattr(cluster, "event_id", "") or "").strip()
    if event_id:
        return f"event:{event_id}"
    normalized = normalize_canonical_url(canonical_url) or canonical_url
    return f"url:{normalized}"


def build_notification_candidates(
    clusters: list[EventCluster],
    importance_results: Any,
    freshness_state: Any,
    now: datetime,
    *,
    translator: InternationalNewsTranslator | None = None,
    dedup_store: NotificationDedupStore | None = None,
) -> list[NotificationCandidate]:
    """Build at most one candidate per eligible event.

    ``now`` is accepted as part of the public contract; freshness itself is
    already classified by the main pipeline so the function does not invent a
    second clock or silently broaden the delivery window.
    """

    candidates: list[NotificationCandidate] = []
    config = load_international_config()
    for cluster in clusters or []:
        members = _event_articles(cluster)
        canonical = getattr(cluster, "canonical", None)
        if canonical is None or not members:
            continue
        # Taiwan/local articles retain their existing notification lane.  This
        # builder is exclusively the international event delivery lane.
        if not any(
            str(getattr(article, "source_id", "")).strip().lower() in {
                "reuters_international", "ft_alphaville", "wsj_newsletter", "bloomberg_newsletter",
            }
            or is_international_media(getattr(article, "source_name", ""), config)
            for article in members
        ):
            continue
        freshness = _fresh_and_excluded_urls(freshness_state)
        if freshness is None:
            continue
        fresh_urls, excluded_urls, classified_urls = freshness
        # Fail closed for any member/coverage whose status was not explicitly
        # classified.  A fresh coverage can still rescue an old canonical,
        # provided that canonical is explicitly catch-up/stale/baseline state.
        if any(
            not _url_matches(str(getattr(article, "url", "")), classified_urls)
            for article in members
        ):
            continue
        # Freshness belongs to the event's complete member/coverage set.  A
        # legacy canonical plus a newly arrived coverage item is therefore
        # eligible exactly once; a baseline-excluded fresh item remains out.
        evidence = _latest_fresh_member(members, fresh_urls, excluded_urls)
        if evidence is None:
            continue
        relevant, reason = _relevance(cluster)
        if not relevant:
            continue
        importance = _best_importance(cluster, importance_results)
        if importance is None:
            continue
        level = str(getattr(importance, "level", "normal"))
        score = int(getattr(importance, "score", 0))
        if level not in {"important", "critical"} or score < 65:
            continue
        event_id = str(getattr(cluster, "event_id", ""))
        canonical_url = str(canonical.url)
        source_ids = list(dict.fromkeys(str(article.source_id) for article in members))
        coverage_urls = list(dict.fromkeys(str(article.url) for article in members))
        dedup_key = _stable_dedup_key(cluster, canonical_url)
        if dedup_store is not None:
            try:
                seen = dedup_store.is_seen(dedup_key, now=now)
            except TypeError:
                # Permit tiny injected test doubles with the one-argument
                # interface while retaining fail-closed behavior on errors.
                try:
                    seen = dedup_store.is_seen(dedup_key)
                except Exception:
                    seen = True
            except Exception:
                seen = True
            if seen:
                continue
        candidate = NotificationCandidate(
            event_id=event_id,
            canonical_url=canonical_url,
            cn_title=_cn_title(cluster, evidence, translator),
            importance_level=level,  # type: ignore[arg-type]
            score=score,
            relevance_reason=reason,
            coverage_source_ids=source_ids,
            coverage_urls=coverage_urls,
            dedup_key=dedup_key,
            notifiable=True,
            freshness_status="fresh",
            baseline_excluded=False,
            evidence_url=str(getattr(evidence, "url", "")),
            evidence_source_id=str(getattr(evidence, "source_id", "")),
        )
        candidates.append(candidate)
    return deduplicate_notification_candidates(candidates)


def deduplicate_notification_candidates(
    candidates: list[NotificationCandidate],
) -> list[NotificationCandidate]:
    """Keep the first notifiable candidate for each event/key."""

    result: list[NotificationCandidate] = []
    seen: set[str] = set()
    for candidate in candidates or []:
        if not candidate.notifiable:
            continue
        event_id = str(candidate.event_id or "").strip()
        marker = str(candidate.dedup_key or "").strip() or (
            f"event:{event_id}" if event_id else f"url:{candidate.canonical_url}"
        )
        if marker in seen or (event_id and event_id in seen):
            continue
        seen.add(marker)
        if event_id:
            seen.add(event_id)
        result.append(candidate)
    return result


__all__ = [
    "NotificationCandidate",
    "build_notification_candidates",
    "deduplicate_notification_candidates",
    "NotificationDedupStore",
]
