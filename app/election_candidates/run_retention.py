from __future__ import annotations

import shutil
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class RunRetentionReport:
    enabled: bool
    examined: int
    protected: tuple[str, ...]
    deleted: tuple[str, ...]
    deleted_bytes: int
    remaining_count: int
    remaining_bytes: int
    over_limit: bool
    warnings: tuple[str, ...]

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _directory_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file() and not item.is_symlink():
            total += item.stat().st_size
    return total


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path)


def _empty_report(
    *,
    enabled: bool,
    warnings: list[str] | None = None,
) -> RunRetentionReport:
    return RunRetentionReport(
        enabled=enabled,
        examined=0,
        protected=(),
        deleted=(),
        deleted_bytes=0,
        remaining_count=0,
        remaining_bytes=0,
        over_limit=False,
        warnings=tuple(warnings or ()),
    )


def enforce_run_retention(
    runs_root: str | Path,
    *,
    current_run_dir: str | Path | None,
    enabled: bool,
    keep_latest: int,
    max_total_mb: int,
) -> RunRetentionReport:
    """Prune old candidate run artifacts without failing the caller.

    Only direct, non-link ``run_*`` directories below ``runs_root`` are ever
    eligible. The newest ``keep_latest`` directories and the current run are
    always protected, so ``max_total_mb`` is a soft cap when protected runs are
    themselves larger than the configured limit.
    """

    if not enabled:
        return _empty_report(enabled=False)

    warnings: list[str] = []
    if keep_latest < 1 or max_total_mb < 1:
        warnings.append("invalid_limits:keep_latest_and_max_total_mb_must_be_positive")
        return _empty_report(enabled=True, warnings=warnings)

    root = Path(runs_root)
    try:
        root_resolved = root.resolve(strict=False)
    except OSError as exc:
        warnings.append(f"root_resolve_failed:{type(exc).__name__}")
        return _empty_report(enabled=True, warnings=warnings)

    if not root.exists():
        return _empty_report(enabled=True)

    current_resolved: Path | None = None
    if current_run_dir is not None:
        try:
            current_resolved = Path(current_run_dir).resolve(strict=False)
            current_resolved.relative_to(root_resolved)
        except (OSError, ValueError) as exc:
            warnings.append(f"current_run_outside_root:{type(exc).__name__}")
            current_resolved = None

    candidates: list[tuple[Path, int, int]] = []
    examined = 0
    try:
        children = list(root.iterdir())
    except OSError as exc:
        warnings.append(f"root_list_failed:{type(exc).__name__}")
        report = _empty_report(enabled=True, warnings=warnings)
        return RunRetentionReport(
            enabled=report.enabled,
            examined=report.examined,
            protected=report.protected,
            deleted=report.deleted,
            deleted_bytes=report.deleted_bytes,
            remaining_count=report.remaining_count,
            remaining_bytes=report.remaining_bytes,
            over_limit=report.over_limit,
            warnings=report.warnings,
        )

    for child in children:
        try:
            if child.is_symlink() or not child.is_dir() or not child.name.startswith("run_"):
                continue
            examined += 1
            resolved = child.resolve(strict=True)
            resolved.relative_to(root_resolved)
            size = _directory_bytes(child)
            candidates.append((child, child.stat().st_mtime_ns, size))
        except (OSError, ValueError) as exc:
            warnings.append(f"inventory_failed:{child.name}:{type(exc).__name__}")

    candidates.sort(key=lambda item: (item[1], item[0].name), reverse=True)
    protected_paths = {item[0].resolve(strict=False) for item in candidates[:keep_latest]}
    if current_resolved is not None:
        protected_paths.add(current_resolved)

    deleted: list[str] = []
    deleted_bytes = 0
    for path, _mtime, size in reversed(candidates):
        if path.resolve(strict=False) in protected_paths:
            continue
        try:
            _remove_tree(path)
            deleted.append(path.name)
            deleted_bytes += size
        except OSError as exc:
            warnings.append(f"delete_failed:{path.name}:{type(exc).__name__}")

    remaining: list[tuple[Path, int]] = []
    for path, _mtime, previous_size in candidates:
        if not path.exists():
            continue
        try:
            remaining.append((path, _directory_bytes(path)))
        except OSError as exc:
            warnings.append(f"remaining_size_failed:{path.name}:{type(exc).__name__}")
            remaining.append((path, previous_size))

    remaining_bytes = sum(size for _path, size in remaining)
    max_total_bytes = max_total_mb * 1024 * 1024
    protected_names = tuple(
        path.name
        for path, _mtime, _size in candidates
        if path.resolve(strict=False) in protected_paths and path.exists()
    )
    return RunRetentionReport(
        enabled=True,
        examined=examined,
        protected=protected_names,
        deleted=tuple(deleted),
        deleted_bytes=deleted_bytes,
        remaining_count=len(remaining),
        remaining_bytes=remaining_bytes,
        over_limit=remaining_bytes > max_total_bytes,
        warnings=tuple(warnings),
    )
