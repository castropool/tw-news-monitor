# Project Size Control Design

## Goal

Reduce the production project directory from about 1.07 GiB to approximately
180-190 MiB, keep it below 200 MiB during normal operation, and preserve the
ability to recover historical artifacts without interrupting the three active
scheduled workflows.

## Current Evidence

- `data`: 628.1 MiB.
- `.venv-clean-newsletter-wave2`: 229.3 MiB.
- `deployment`: 201.0 MiB.
- `data/election_candidates/tainan_2026/runs`: 470.0 MiB across 460 run
  directories and 9,596 JSON, JSONL, and Markdown files.
- `data/election_candidates/tainan_2026/phase25_migration`: 37.7 MiB.
- The candidate monitor runs every 30 minutes and creates roughly 1.5-2.3 MiB
  of run artifacts per invocation.
- The active scheduled tasks call the project root scripts and system Python;
  none references `.venv-clean-newsletter-wave2` or `deployment`.

## Safety Constraints

The following items are production state and must not be deleted, archived, or
rewritten by the size-control operation:

- `.env`, source code, configuration, prompts, and operator documentation.
- `data/news.db`, `data/election_watch.db`, `data/election_context.db`, and all
  SQLite `-wal` and `-shm` companions.
- `data/election_candidates/tainan_2026/candidate_fact_pipeline.db` and its
  companions.
- `data/election_seed`, active production assessment periods, review state,
  current reports, publication staging, publication batches, post-publication
  state, locks, and logs.
- Scheduled-task definitions, triggers, working directories, and enabled state.

No source is removed until its external archive has been created, inventoried,
and verified. The unused virtual environment is the only item that may be
removed without archiving because it is reproducible from `requirements.txt`
and is not referenced by production launchers.

## Selected Approach

Use an external recoverable archive for historical artifacts, remove the
unused virtual environment, and add bounded retention for future candidate run
directories.

The sibling archive root is:

`D:\WXWorkLocal\TW News-Monitor111\tw-news-monitor-simple-archive\20260903-project-size-control`

It is outside the production project directory, so archive bytes do not count
toward the 200 MiB production target.

## One-Time Archive and Cleanup

1. Record current scheduled-task state and project size.
2. Temporarily disable only `Tainan Election Candidate Monitor`. If it is
   already running, wait for that invocation to finish before touching `runs`.
   The news monitor and assessment task remain unchanged.
3. Keep the newest 12 direct child directories named `run_*` under
   `data/election_candidates/tainan_2026/runs` and archive all older direct
   children.
4. Archive the complete `deployment` directory.
5. Archive
   `data/election_candidates/tainan_2026/phase25_migration`.
6. For every archive, write a manifest containing source path, file count,
   uncompressed byte count, archive path, archive byte count, archive SHA-256,
   and creation timestamp.
7. Verify each archive can be listed and that its recorded SHA-256 matches the
   generated file. Only then remove the archived source items.
8. Remove `.venv-clean-newsletter-wave2`, Python `__pycache__` directories,
   `.pyc` files, and `.pytest_cache`. These are reproducible and are not
   production state.
9. Recreate required empty runtime directories when necessary and restore the
   candidate task to exactly its original enabled state.

If archive creation, manifest generation, verification, or task-state restore
fails, stop immediately, retain all unverified source data, and report the
failure. A cleanup failure must never trigger deletion of active databases or
formal production artifacts.

## Ongoing Run Retention

Add a focused module at `app/election_candidates/run_retention.py`. It owns only
direct child directories of the configured `runs` root and exposes a function
that returns a structured cleanup report.

The production configuration in `config/election_candidate_pipeline.yaml`
will contain:

```yaml
run_retention:
  enabled: true
  keep_latest: 12
  max_total_mb: 40
```

Retention rules:

- Run only after a candidate pipeline invocation has completed successfully
  and all current outputs have been written.
- Protect the current run directory unconditionally.
- Inspect only direct children whose names start with `run_`.
- Skip symbolic links, files, malformed paths, and paths that resolve outside
  the configured runs root.
- Delete oldest eligible directories first until both the count and total-byte
  limits are satisfied.
- Never delete the newest configured number merely to satisfy the byte cap;
  the count floor protects current diagnostics. If those protected runs exceed
  40 MiB, report the overage without deleting them.
- A retention error is logged as a warning and does not convert a successful
  candidate pipeline run into a failed run.

The retention call is added to both the normal successful pipeline path and
the successful latest-run rerender path in
`app/election_candidates/build_candidate_queue.py`.

## Data Flow

```text
candidate run succeeds
  -> write database state and run artifacts
  -> invoke run retention with current run protected
  -> calculate direct-child count and bytes
  -> remove oldest eligible run directories
  -> log structured cleanup summary
  -> exit with the original successful status
```

The one-time archive is independent from runtime retention. Historical data is
archived once; newly expired run directories are deleted because their durable
business state remains in SQLite and operational logs.

## Recovery

- Historical runs, deployment evidence, and migration evidence can be restored
  by extracting the verified archives back to their recorded source paths.
- `.venv-clean-newsletter-wave2` can be rebuilt with a fresh virtual
  environment and `requirements.txt`; production tasks do not require it.
- If the retention feature causes an unexpected issue, set
  `run_retention.enabled: false`. No database rollback is required because the
  feature does not write business tables.

## Verification

Verification must cover independent channels:

1. Unit tests for oldest-first deletion, current-run protection, count limit,
   byte limit, malformed entry handling, path containment, disabled mode, and
   non-fatal cleanup errors.
2. Existing candidate CLI and production-wiring tests.
3. Import and help/check-only smoke checks using the same system Python paths
   used by scheduled tasks.
4. Scheduled-task comparison before and after cleanup: action, arguments,
   working directory, trigger, enabled state, and last result must remain
   unchanged except for a normal later run timestamp.
5. Archive manifest verification and archive listing.
6. Final recursive size measurement of the production project directory.

Acceptance criteria:

- Production project size is between 160 and 200 MiB immediately after the
  operation.
- The newest 12 candidate run directories remain available.
- All archives pass listing and SHA-256 verification.
- Active databases and formal production paths still exist with their original
  byte sizes before post-cleanup smoke checks.
- Relevant tests and smoke checks pass.
- The candidate task is restored to its original state and future successful
  runs enforce the configured retention bounds.

