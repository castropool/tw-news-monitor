from __future__ import annotations

import os
from pathlib import Path

from app.election_candidates.run_retention import enforce_run_retention


def _run(root: Path, name: str, size: int, mtime: int) -> Path:
    path = root / name
    path.mkdir(parents=True)
    (path / "payload.jsonl").write_bytes(b"x" * size)
    os.utime(path, (mtime, mtime))
    return path


def test_deletes_oldest_and_protects_current(tmp_path):
    root = tmp_path / "runs"
    oldest = _run(root, "run_001", 10, 1)
    _run(root, "run_002", 10, 2)
    current = _run(root, "run_003", 10, 3)

    report = enforce_run_retention(
        root,
        current_run_dir=current,
        enabled=True,
        keep_latest=2,
        max_total_mb=40,
    )

    assert not oldest.exists()
    assert current.exists()
    assert report.deleted == ("run_001",)
    assert report.remaining_count == 2
    assert report.over_limit is False


def test_ignores_files_non_run_directories_and_disabled_mode(tmp_path):
    root = tmp_path / "runs"
    kept = _run(root, "manual_export", 10, 1)
    marker = root / "run_file"
    marker.write_text("not a directory", encoding="utf-8")
    candidate = _run(root, "run_001", 10, 2)

    report = enforce_run_retention(
        root,
        current_run_dir=candidate,
        enabled=False,
        keep_latest=1,
        max_total_mb=1,
    )

    assert kept.exists() and marker.exists() and candidate.exists()
    assert report.enabled is False
    assert report.deleted == ()


def test_protected_floor_reports_byte_overage(tmp_path):
    root = tmp_path / "runs"
    current = _run(root, "run_001", 2 * 1024 * 1024, 1)

    report = enforce_run_retention(
        root,
        current_run_dir=current,
        enabled=True,
        keep_latest=1,
        max_total_mb=1,
    )

    assert current.exists()
    assert report.over_limit is True
    assert report.remaining_count == 1


def test_delete_error_is_reported_not_raised(tmp_path, monkeypatch):
    root = tmp_path / "runs"
    old = _run(root, "run_001", 10, 1)
    current = _run(root, "run_002", 10, 2)

    def fail_delete(path):
        raise PermissionError(str(path))

    monkeypatch.setattr("app.election_candidates.run_retention._remove_tree", fail_delete)
    report = enforce_run_retention(
        root,
        current_run_dir=current,
        enabled=True,
        keep_latest=1,
        max_total_mb=40,
    )

    assert old.exists() and current.exists()
    assert report.deleted == ()
    assert report.warnings


def test_invalid_limits_return_warning_without_mutation(tmp_path):
    root = tmp_path / "runs"
    current = _run(root, "run_001", 10, 1)

    report = enforce_run_retention(
        root,
        current_run_dir=current,
        enabled=True,
        keep_latest=0,
        max_total_mb=0,
    )

    assert current.exists()
    assert report.deleted == ()
    assert report.warnings
