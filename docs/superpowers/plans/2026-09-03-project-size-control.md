# Project Size Control Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reduce the production project directory to 160-200 MiB and keep candidate run artifacts bounded without changing production databases, formal outputs, or scheduled-task behavior.

**Architecture:** Add a small, fail-open retention module that owns only direct `run_*` children beneath the configured candidate runs directory. Integrate it after successful default-output candidate runs, then perform a one-time verified external archive and removal of untracked historical/reproducible directories. Every destructive filesystem step is gated by an archive listing, SHA-256 verification, exact path containment, and scheduled-task state restoration.

**Tech Stack:** Python 3.12, pathlib, dataclasses, shutil, PyYAML configuration, pytest, Windows PowerShell, Task Scheduler, ZIP archives, SHA-256.

**Spec:** `docs/superpowers/specs/2026-09-03-project-size-control-design.md`

## Global Constraints

- Final production project size must be between 160 and 200 MiB.
- Do not delete or archive `.env`, source, config, prompts, operator docs, active SQLite databases or companions, election seeds, active assessment periods, review state, publication state, locks, or logs.
- Keep the newest 12 candidate `run_*` directories.
- Runtime retention is enabled with `keep_latest: 12` and `max_total_mb: 40`.
- Retention runs only after successful default-output candidate operations and never changes a successful pipeline result into failure.
- Archive data to `D:\WXWorkLocal\TW News-Monitor111\tw-news-monitor-simple-archive\20260903-project-size-control` and verify listing plus SHA-256 before deleting the corresponding source.
- Preserve and restore the original Task Scheduler enabled state and action definitions.
- Use native PowerShell end-to-end for moves/deletes, resolve absolute paths first, and pass exact paths through `-LiteralPath`.
- All four bulk cleanup targets are Git-untracked: `deployment`, `.venv-clean-newsletter-wave2`, candidate `runs`, and `phase25_migration`.

## File Structure

- Create `app/election_candidates/run_retention.py`: isolated run-directory inventory and pruning logic.
- Create `tests/election_candidates/test_run_retention.py`: unit coverage for safety and limits.
- Modify `app/election_candidates/build_candidate_queue.py`: invoke fail-open retention after two successful default-output paths.
- Modify `config/election_candidate_pipeline.yaml`: production retention settings.
- Modify `tests/election_candidates/test_candidate_pipeline_e2e.py`: integration coverage for default and explicit output roots.
- Create `docs/superpowers/plans/2026-09-03-project-size-control.md`: this plan.
- Generate external ZIP files and manifests under the approved sibling archive root; these are operational artifacts, not repository files.

---

### Task 1: Run-retention safety unit

**Files:**
- Create: `tests/election_candidates/test_run_retention.py`
- Create: `app/election_candidates/run_retention.py`

**Interfaces:**
- Consumes: `pathlib.Path` runs root, current run path, enabled flag, integer count limit, integer MiB limit.
- Produces: `RunRetentionReport` and `enforce_run_retention(...) -> RunRetentionReport`.

- [ ] **Step 1: Write focused failing tests**

Create tests with a helper that writes deterministic byte counts and timestamps:

```python
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
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```powershell
python -m pytest tests/election_candidates/test_run_retention.py -q
```

Expected: collection fails because `app.election_candidates.run_retention` does not exist.

- [ ] **Step 3: Implement the bounded retention unit**

Implement a slots dataclass with stable serialization fields:

```python
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
```

Implement these helpers in the same module:

```python
def _directory_bytes(path: Path) -> int:
    total = 0
    for item in path.rglob("*"):
        if item.is_file() and not item.is_symlink():
            total += item.stat().st_size
    return total


def _remove_tree(path: Path) -> None:
    shutil.rmtree(path)
```

`enforce_run_retention` must:

1. Return without mutation when disabled or the root does not exist.
2. Reject invalid `keep_latest < 1` or `max_total_mb < 1` by returning a warning.
3. Resolve the runs root and current path.
4. Inventory direct children only; accept directories named `run_*`; skip links.
5. Verify each accepted child resolves beneath the runs root.
6. Sort newest-first by `(st_mtime_ns, name)`.
7. Protect the newest `keep_latest` entries plus the current run.
8. Delete older eligible entries oldest-first with `_remove_tree`.
9. Catch per-directory errors, continue, and append redacted path/error text to warnings.
10. Recalculate remaining count and bytes, then set `over_limit` when protected data exceeds the configured byte cap.

- [ ] **Step 4: Run unit tests to verify they pass**

Run:

```powershell
python -m pytest tests/election_candidates/test_run_retention.py -q
```

Expected: all retention tests pass.

- [ ] **Step 5: Run static compilation**

Run:

```powershell
python -m compileall app/election_candidates/run_retention.py
```

Expected: exit code 0.

### Task 2: Production configuration and pipeline integration

**Files:**
- Modify: `config/election_candidate_pipeline.yaml`
- Modify: `app/election_candidates/build_candidate_queue.py`
- Modify: `tests/election_candidates/test_candidate_pipeline_e2e.py`

**Interfaces:**
- Consumes: `enforce_run_retention` and `RunRetentionReport.to_dict()` from Task 1.
- Produces: successful default-output runs prune old artifacts and emit one JSON cleanup line.

- [ ] **Step 1: Add failing integration tests**

Add one test that creates three old `run_*` directories beneath the temporary configured output root, sets `keep_latest` to 2, executes a successful default-output run, and asserts only the generated current run plus the newest old run remain.

Add a second test that executes with `output_root=str(tmp_path / "manual")` and asserts pre-existing configured `run_*` directories are untouched.

The assertions must inspect directory names and retain the existing database hash assertions from `test_full_pipeline_build_and_outputs`.

- [ ] **Step 2: Run integration tests to verify they fail**

Run:

```powershell
python -m pytest tests/election_candidates/test_candidate_pipeline_e2e.py -q
```

Expected: the new retention assertions fail because the pipeline does not call the retention module.

- [ ] **Step 3: Add production configuration**

Add this top-level block near `deployment` in `config/election_candidate_pipeline.yaml`:

```yaml
run_retention:
  enabled: true
  keep_latest: 12
  max_total_mb: 40
```

- [ ] **Step 4: Add fail-open integration helper**

Import `enforce_run_retention` into `build_candidate_queue.py` and add:

```python
def _apply_run_retention(config, args, current_run_dir: Path) -> dict[str, object] | None:
    if args.output_root:
        return None
    report = enforce_run_retention(
        config.path("output_root") / "runs",
        current_run_dir=current_run_dir,
        enabled=bool(config.get("run_retention.enabled", False)),
        keep_latest=int(config.get("run_retention.keep_latest", 12)),
        max_total_mb=int(config.get("run_retention.max_total_mb", 40)),
    )
    payload = report.to_dict()
    print("run_retention=" + json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return payload
```

Call it after `render_run_outputs` in the successful rebuild-preview branch and after `run_idempotency.json` is written in the normal successful branch. Do not call it from validate-only, reset-cursor, blocked, or exception paths.

- [ ] **Step 5: Run focused integration and CLI tests**

Run:

```powershell
python -m pytest tests/election_candidates/test_candidate_pipeline_e2e.py tests/election_candidates/test_build_candidate_queue_cli.py -q
```

Expected: all tests pass and explicit output roots remain untouched.

- [ ] **Step 6: Run all candidate tests**

Run:

```powershell
python -m pytest tests/election_candidates -q
```

Expected: no failures.

- [ ] **Step 7: Commit the retention feature**

```powershell
git add app/election_candidates/run_retention.py app/election_candidates/build_candidate_queue.py config/election_candidate_pipeline.yaml tests/election_candidates/test_run_retention.py tests/election_candidates/test_candidate_pipeline_e2e.py
git commit -m feat:candidate-run-retention
```

Expected: one focused feature commit after green tests.

### Task 3: Capture production baseline and create verified archives

**Files:**
- Read only: active databases, launch scripts, scheduled tasks, cleanup sources.
- Create externally: archive ZIP files, `archive_manifest.json`, `pre_cleanup_state.json`.

**Interfaces:**
- Consumes: approved archive root and exact untracked cleanup targets.
- Produces: verified recoverable archives and a pre-cleanup state record.

- [ ] **Step 1: Recalculate exact target paths and sizes**

Resolve these literal source paths and assert each starts with the resolved production root:

```text
D:\WXWorkLocal\TW News-Monitor111\tw-news-monitor-simple\deployment
D:\WXWorkLocal\TW News-Monitor111\tw-news-monitor-simple\.venv-clean-newsletter-wave2
D:\WXWorkLocal\TW News-Monitor111\tw-news-monitor-simple\data\election_candidates\tainan_2026\runs
D:\WXWorkLocal\TW News-Monitor111\tw-news-monitor-simple\data\election_candidates\tainan_2026\phase25_migration
```

Abort if the resolved production root, source paths, or archive root differ from the approved absolute paths.

- [ ] **Step 2: Record protected database metadata**

Write `pre_cleanup_state.json` outside the project containing path, byte size, and last-write timestamp for:

```text
data/news.db
data/election_watch.db
data/election_context.db
data/election_candidates/tainan_2026/candidate_fact_pipeline.db
```

Also record total project bytes, candidate run count, newest 12 run names, and the complete Task Scheduler CSV rows for the three active tasks.

- [ ] **Step 3: Disable only the candidate monitor and wait for quiescence**

Run with elevated Task Scheduler permissions:

```powershell
schtasks.exe /change /tn "\Tainan Election Candidate Monitor" /disable
```

Poll its status until it is not `Running`. Record whether it was enabled before the change so the same state can be restored in Task 4.

- [ ] **Step 4: Create three archives without deleting sources**

Create the archive root and these files:

```text
candidate-runs-before-20260903.zip
deployment.zip
phase25-migration.zip
```

For candidate runs, sort direct `run_*` directories newest-first and pass every entry after the first 12 to `Compress-Archive -LiteralPath`. Archive `deployment` and `phase25_migration` separately with their exact literal paths.

- [ ] **Step 5: Verify every archive and write the manifest**

Open each ZIP using `System.IO.Compression.ZipFile.OpenRead`, enumerate entries, close it, and calculate SHA-256 with `Get-FileHash -Algorithm SHA256`.

Write `archive_manifest.json` with:

```json
{
  "schema_version": "project-size-control.archive-manifest.v1",
  "production_root": "D:\\WXWorkLocal\\TW News-Monitor111\\tw-news-monitor-simple",
  "archive_root": "D:\\WXWorkLocal\\TW News-Monitor111\\tw-news-monitor-simple-archive\\20260903-project-size-control",
  "kept_run_count": 12,
  "archives": []
}
```

Populate each archive item with source path, source file count, uncompressed bytes, archive path, archive bytes, entry count, SHA-256, and creation timestamp. Abort cleanup if any ZIP cannot be opened, has zero entries for a non-empty source, or its calculated hash differs from the manifest value.

### Task 4: Remove verified sources and restore scheduling

**Files:**
- Delete after verification: archived old run directories, `deployment`, `phase25_migration`.
- Delete as reproducible: `.venv-clean-newsletter-wave2`, `__pycache__`, `.pyc`, `.pytest_cache`.
- Preserve: all protected paths in the design spec.

**Interfaces:**
- Consumes: verified archive manifest from Task 3.
- Produces: reduced production tree with scheduled-task state restored.

- [ ] **Step 1: Revalidate archive hashes immediately before deletion**

Recompute every manifest ZIP SHA-256 and compare it byte-for-byte with `archive_manifest.json`. Stop without deleting if any comparison fails.

- [ ] **Step 2: Delete only manifest-covered historical sources**

Using one native PowerShell process and explicit `-LiteralPath` values:

1. Remove the old run directories listed in the verified manifest; do not remove the `runs` root or newest 12 directories.
2. Remove `deployment`.
3. Remove `data/election_candidates/tainan_2026/phase25_migration`.

After each group, confirm the protected databases still exist and their byte sizes still match `pre_cleanup_state.json`.

- [ ] **Step 3: Remove reproducible environment and caches**

Remove the exact `.venv-clean-newsletter-wave2` directory, direct `.pytest_cache`, all `__pycache__` directories below the production root, and `.pyc` files. Do not follow directory links and do not use an unresolved wildcard as a delete target.

- [ ] **Step 4: Restore candidate task state in a finally block**

If the task was originally enabled, run:

```powershell
schtasks.exe /change /tn "\Tainan Election Candidate Monitor" /enable
```

If it was originally disabled, leave it disabled. Query the task afterward and compare action, working directory, trigger, and enabled state with `pre_cleanup_state.json`.

- [ ] **Step 5: Measure the post-cleanup project**

Recursively sum file bytes. Expected: 160-200 MiB. If the result is above 200 MiB, report the remaining largest directories and stop; do not expand cleanup scope without a new explicit decision.

### Task 5: Functional verification and handoff

**Files:**
- Read: production config, launch scripts, active databases, Task Scheduler state.
- Update only if needed: operator documentation describing run retention.

**Interfaces:**
- Consumes: cleaned production tree and retention feature commit.
- Produces: evidence that production entry points and protected state remain valid.

- [ ] **Step 1: Verify protected database metadata**

Compare the four protected database paths and byte sizes with `pre_cleanup_state.json`. WAL/SHM files may change only if a normal scheduled operation ran after task restoration; if so, compare SQLite integrity instead of byte identity.

- [ ] **Step 2: Run SQLite integrity checks**

Open each active database and execute `PRAGMA quick_check`; expected result is exactly `ok` for each database.

- [ ] **Step 3: Run focused and full tests**

```powershell
python -m pytest tests/election_candidates/test_run_retention.py tests/election_candidates/test_candidate_pipeline_e2e.py tests/election_candidates/test_phase_f1_production_wiring.py -q
python -m pytest tests -q
```

Expected: no failures. If the full suite has a pre-existing environmental failure, record it separately and require all retention/candidate tests to pass.

- [ ] **Step 4: Run no-send entry-point smoke checks**

Use the production system Python to run:

```powershell
python -m app.election_candidates.build_candidate_queue --help
python -m app.assessment.research_driven.scheduled --config config/election_assessment.yaml --check-only
py -m app.main --dry-run
```

The news smoke check must remain dry-run so it cannot send a digest. Record exit codes and do not expose environment-secret values.

- [ ] **Step 5: Verify Task Scheduler and retention bounds**

Confirm all three active tasks still point to the same root scripts and working directory. Confirm the candidate task is in its original enabled state. Run or observe one successful candidate invocation, then assert the runs root contains at most 12 `run_*` directories and no more than 40 MiB unless the protected newest 12 themselves exceed the soft byte cap.

- [ ] **Step 6: Record final evidence**

Report:

- Final project MiB.
- Bytes archived and archive ZIP sizes.
- Removed reproducible bytes.
- Kept candidate run names.
- Archive manifest and SHA-256 verification status.
- Database integrity results.
- Test and smoke-check results.
- Scheduled-task restoration status.
- Remaining risks, including the archive root consuming disk outside the production project.

- [ ] **Step 7: Commit any final documentation update**

If operator documentation changed, commit only that documentation. Do not commit generated archives, manifests containing local absolute paths, logs, databases, or runtime outputs.

