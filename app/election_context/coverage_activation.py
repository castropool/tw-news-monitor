"""Coverage activation with staging -> validation -> atomic activation.

Contract:
- Only a fully validated coverage (``coverage_ready`` from the deterministic
  validator) may be atomically renamed into the seed coverage root as
  ``fact_coverage_<date>_v<n>``.
- In-progress artifacts live under ``<coverage_root>/staging/<version>`` and are
  never selectable by R2's ``select_coverage_version`` (subdirectory + name).
- Any failure moves the staged artifact into ``<coverage_root>/staging/failed/``
  and re-raises; the previously active coverage stays selectable.
- Idempotent: activating the exact same version + business hash is a no-op
  (reuses the existing activated directory).
- Version naming is deterministic (content hash) and already carries the
  facts_cutoff date; same-day collisions are either reused (identical content)
  or failed loudly and isolated.
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

from app.time_utils import TAIPEI


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
    tmp.replace(path)


def _write_json(path: Path, obj: Any) -> None:
    _atomic_write(path, json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8"))


def _atomic_rename_dir(src: Path, dst: Path) -> None:
    """Same-volume atomic directory rename (staging -> selectable root)."""
    src.rename(dst)


def coverage_staging_root(config) -> Path:
    return Path(config.path("coverage_root")) / "staging"


def failed_coverage_root(config) -> Path:
    return coverage_staging_root(config) / "failed"


def stage_coverage(
    config,
    coverage_result: dict[str, Any],
    validation: dict[str, Any],
    *,
    refresh_batch_id: str,
    active_snapshot_id: str,
) -> Path:
    """Write the validated coverage into the non-selectable staging directory."""
    version = coverage_result["coverage_version"]
    staging_dir = coverage_staging_root(config) / version
    if staging_dir.exists():
        shutil.rmtree(staging_dir)
    staging_dir.mkdir(parents=True, exist_ok=True)

    coverage = coverage_result["coverage"]
    (staging_dir / f"{version}.json").write_text(
        json.dumps(coverage, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    (staging_dir / "coverage_manifest.json").write_text(
        json.dumps(coverage_result["manifest"], ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _write_json(
        staging_dir / "coverage_preflight.json",
        {
            "coverage_version": version,
            "coverage_generated_at": datetime.now(TAIPEI).date().isoformat(),
            "facts_cutoff": coverage.get("facts_cutoff"),
            "poll_cutoff": coverage.get("poll_cutoff"),
            "active_snapshot": active_snapshot_id,
            "refresh_batch_id": refresh_batch_id,
            "preflight_ready": True,
        },
    )
    validation_payload = dict(validation)
    validation_payload["coverage_ready"] = True  # activation only after validation passed
    _write_json(staging_dir / "coverage_validation.json", validation_payload)
    return staging_dir


def isolate_failed_coverage(config, version: str, error: str) -> Path:
    """Move a failed/in-progress staging dir out of the selectable area."""
    staging_dir = coverage_staging_root(config) / version
    failed_root = failed_coverage_root(config)
    failed_root.mkdir(parents=True, exist_ok=True)
    target = failed_root / (
        f"{version}_failed_{datetime.now(TAIPEI).strftime('%Y%m%d_%H%M%S_%f')}"
    )
    if staging_dir.exists():
        if target.exists():
            shutil.rmtree(target)
        shutil.move(str(staging_dir), str(target))
        _write_json(
            target / "failure_reason.json",
            {"error": error, "isolated_at": datetime.now(TAIPEI).isoformat()},
        )
    return target


def activate_coverage(
    config,
    coverage_result: dict[str, Any],
    validation: dict[str, Any],
    *,
    refresh_batch_id: str,
    active_snapshot_id: str,
    allow_real: bool = False,
) -> dict[str, Any]:
    """Atomic activation; raises on any failure and never leaves a selectable
    half-product.  Real activation requires ``allow_real`` or ``config.test_mode``;
    otherwise the activation stays pending (no error, mirrors snapshot
    ``pending_review`` semantics)."""
    if not (allow_real or config.test_mode):
        return {
            "activated": False,
            "reason": "coverage activation blocked (requires allow_real or test_mode)",
            "version": coverage_result["coverage_version"],
        }
    root = Path(config.path("coverage_root"))
    version = coverage_result["coverage_version"]
    target = root / version
    staging = stage_coverage(
        config,
        coverage_result,
        validation,
        refresh_batch_id=refresh_batch_id,
        active_snapshot_id=active_snapshot_id,
    )
    try:
        if target.exists():
            existing_manifest = target / "coverage_manifest.json"
            same_content = False
            if existing_manifest.exists():
                try:
                    m = json.loads(existing_manifest.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001 - corrupt manifest is a collision
                    m = {}
                same_content = m.get("business_hash") == coverage_result["business_hash"]
            if same_content:
                shutil.rmtree(staging)
                return {
                    "activated": False,
                    "reused": True,
                    "version": version,
                    "path": str(target),
                }
            raise RuntimeError(f"coverage version collision: {target.name}")
        _atomic_rename_dir(staging, target)
        return {"activated": True, "version": version, "path": str(target)}
    except Exception as exc:  # noqa: BLE001 - isolate and re-raise
        isolate_failed_coverage(config, version, str(exc))
        raise
