"""Small, atomic, provider-neutral source health sidecar.

The health file is deliberately separate from SQLite and the article model.  A
broken feed must not prevent the main Taiwan pipeline from running, and a
health write must never partially replace the previous state.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal


HealthStatus = Literal["healthy", "degraded", "stale", "broken", "disabled"]


@dataclass(frozen=True, slots=True)
class SourceOutcome:
    """One collector attempt; schema validity is independent of item count."""

    http_status: int
    schema_valid: bool
    item_count: int
    error_code: str | None = None


class ValidEmptyFeed(SourceOutcome):
    def __init__(self, http_status: int = 200):
        super().__init__(http_status=http_status, schema_valid=True, item_count=0)


@dataclass(frozen=True, slots=True)
class HealthRecord:
    source_id: str
    status: HealthStatus
    last_success: datetime | None = None
    last_item_at: datetime | None = None
    items_fetched: int = 0
    parse_errors: int = 0
    consecutive_failures: int = 0
    # Internal counter: kept in the sidecar so a valid empty feed is not
    # confused with a malformed feed after process restarts.
    zero_item_runs: int = 0
    last_error_code: str | None = None
    updated_at: datetime | None = None

    def to_dict(self) -> dict:
        return {
            "source_id": self.source_id,
            "status": self.status,
            "last_success": _dump_dt(self.last_success),
            "last_item_at": _dump_dt(self.last_item_at),
            "items_fetched": self.items_fetched,
            "parse_errors": self.parse_errors,
            "consecutive_failures": self.consecutive_failures,
            "zero_item_runs": self.zero_item_runs,
            "last_error_code": self.last_error_code,
            "updated_at": _dump_dt(self.updated_at),
        }

    @classmethod
    def from_dict(cls, value: dict) -> "HealthRecord":
        return cls(
            source_id=str(value.get("source_id", "")),
            status=_status(value.get("status", "disabled")),
            last_success=_load_dt(value.get("last_success")),
            last_item_at=_load_dt(value.get("last_item_at")),
            items_fetched=_nonnegative_int(value.get("items_fetched", 0)),
            parse_errors=_nonnegative_int(value.get("parse_errors", 0)),
            consecutive_failures=_nonnegative_int(value.get("consecutive_failures", 0)),
            zero_item_runs=_nonnegative_int(value.get("zero_item_runs", 0)),
            last_error_code=_error_code(value.get("last_error_code")),
            # Migrate old sidecars which have no updated_at using the last
            # successful observation as the only compatible timestamp.
            updated_at=_load_dt(value.get("updated_at"))
            or _load_dt(value.get("last_success")),
        )


class SourceHealthStore:
    """JSON sidecar store with fail-closed reads and atomic replacement writes."""

    STALE_EMPTY_RUNS = 3
    STALE_AFTER = timedelta(hours=48)

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def get(self, source_id: str) -> HealthRecord:
        records = self._read()
        raw = records.get(source_id)
        if not isinstance(raw, dict):
            return HealthRecord(source_id=source_id, status="disabled")
        return HealthRecord.from_dict(raw)

    def update(
        self,
        source_id: str,
        outcome: SourceOutcome,
        *,
        now: datetime | None = None,
    ) -> HealthRecord:
        if not isinstance(outcome, SourceOutcome):
            raise TypeError("outcome must be SourceOutcome")
        if outcome.item_count < 0:
            raise ValueError("item_count must be non-negative")
        now_utc = _as_utc(now or datetime.now(timezone.utc))
        previous = self.get(source_id)
        valid = (
            200 <= int(outcome.http_status) < 300
            and outcome.schema_valid is True
            and not outcome.error_code
        )
        if not valid:
            error_code = _failure_code(outcome)
            parse_error = error_code in {"parse", "schema", "structure"}
            record = HealthRecord(
                source_id=source_id,
                status=("broken" if previous.consecutive_failures + 1 >= 3 else "degraded"),
                last_success=previous.last_success,
                last_item_at=previous.last_item_at,
                items_fetched=0,
                parse_errors=previous.parse_errors + (1 if parse_error else 0),
                consecutive_failures=previous.consecutive_failures + 1,
                zero_item_runs=previous.zero_item_runs,
                last_error_code=error_code,
                updated_at=now_utc,
            )
        else:
            zero_runs = previous.zero_item_runs + 1 if outcome.item_count == 0 else 0
            status: HealthStatus = "healthy"
            if zero_runs >= self.STALE_EMPTY_RUNS:
                status = "stale"
            else:
                # A source that has never yielded an item still needs a
                # freshness clock.  Use the previous successful check as the
                # anchor until ``last_item_at`` exists; otherwise a valid but
                # permanently empty feed could remain healthy forever.
                freshness_anchor = previous.last_item_at or previous.last_success
                if freshness_anchor is not None and now_utc - freshness_anchor >= self.STALE_AFTER:
                    status = "stale"
            record = HealthRecord(
                source_id=source_id,
                status=status,
                last_success=now_utc,
                last_item_at=(now_utc if outcome.item_count > 0 else previous.last_item_at),
                items_fetched=outcome.item_count,
                parse_errors=previous.parse_errors,
                consecutive_failures=0,
                zero_item_runs=zero_runs,
                last_error_code=None,
                updated_at=now_utc,
            )
        self._write(source_id, record)
        return record

    def disable(self, source_id: str) -> HealthRecord:
        previous = self.get(source_id)
        record = HealthRecord(
            source_id=source_id,
            status="disabled",
            last_success=previous.last_success,
            last_item_at=previous.last_item_at,
            items_fetched=previous.items_fetched,
            parse_errors=previous.parse_errors,
            consecutive_failures=previous.consecutive_failures,
            zero_item_runs=previous.zero_item_runs,
            last_error_code=previous.last_error_code,
            updated_at=datetime.now(timezone.utc),
        )
        self._write(source_id, record)
        return record

    def _read(self) -> dict[str, dict]:
        try:
            value = json.loads(self.path.read_text(encoding="utf-8"))
        except (FileNotFoundError, OSError, ValueError, TypeError):
            return {}
        return value if isinstance(value, dict) else {}

    def _write(self, source_id: str, record: HealthRecord) -> None:
        records = self._read()
        records[source_id] = record.to_dict()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=f".{self.path.name}.", suffix=".tmp", dir=self.path.parent)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                json.dump(records, handle, ensure_ascii=False, indent=2, sort_keys=True)
                handle.write("\n")
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp_name, self.path)
        finally:
            try:
                if os.path.exists(tmp_name):
                    os.unlink(tmp_name)
            except OSError:
                pass


def _status(value: object) -> HealthStatus:
    return value if value in {"healthy", "degraded", "stale", "broken", "disabled"} else "degraded"


def _error_code(value: object) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def _failure_code(outcome: SourceOutcome) -> str:
    """Classify invalid attempts without mixing transport and parse errors."""

    explicit = _error_code(outcome.error_code)
    if explicit:
        return explicit
    if outcome.http_status <= 0:
        return "config"
    if not (200 <= int(outcome.http_status) < 300):
        return "http"
    return "parse"


def _nonnegative_int(value: object) -> int:
    try:
        return max(0, int(value))
    except (TypeError, ValueError, OverflowError):
        return 0


def _dump_dt(value: datetime | None) -> str | None:
    return _as_utc(value).isoformat() if value is not None else None


def _load_dt(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return _as_utc(parsed)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


__all__ = ["HealthRecord", "SourceHealthStore", "SourceOutcome", "ValidEmptyFeed"]
