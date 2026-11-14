#!/usr/bin/env bash
# Update the Linux deployment to a branch, safely, in one command.
#
#   sudo scripts/server_update.sh <branch> [expected-commit-prefix]
#
# First time (the deployed version does not have this script yet), from the app folder:
#   git fetch origin <branch> && git show FETCH_HEAD:scripts/server_update.sh > /tmp/server_update.sh
#   sudo bash /tmp/server_update.sh <branch> [expected-commit-prefix]
#
# Steps (stops at the first failure, production untouched until tests pass):
#   1. Back up every data/*.db (SQLite online backup + integrity check) and .env
#      to $BACKUP_ROOT/tw-news-backup-<yyyyMMdd-HHmm>   (default /opt/backups).
#   2. Fetch the branch; refuse if tracked files were edited on the server.
#   3. Install requirements, then run the tests on a clean export of the new
#      commit in a temporary folder (never against production data/).
#   4. Only if tests pass: pause the timer, check out the commit, resume it.
#   5. Print a summary (old/new commit, tests, schema version, article count).
#
# Never deletes or overwrites anything under data/. Does not change .env.
# PYTHON overrides the interpreter (default: .venv/bin/python, venv/bin/python, python3).
set -euo pipefail

BRANCH="${1:?usage: scripts/server_update.sh <branch> [expected-commit-prefix]}"
EXPECT="${2:-}"
# The app folder: APP_DIR, else the repository this script sits in, else the
# current folder (for a first run from a downloaded copy, e.g. /tmp).
ROOT="${APP_DIR:-$(git -C "$(dirname "$0")" rev-parse --show-toplevel 2>/dev/null || pwd)}"
TIMER="${TIMER:-tw-news-monitor.timer}"
BACKUP_ROOT="${BACKUP_ROOT:-/opt/backups}"
cd "$ROOT"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || { echo "!! $ROOT is not a git checkout" >&2; exit 1; }

if [[ -z "${PYTHON:-}" ]]; then
  for candidate in .venv/bin/python venv/bin/python python3; do
    if command -v "$candidate" >/dev/null 2>&1 || [[ -x "$candidate" ]]; then PYTHON="$candidate"; break; fi
  done
fi
echo "== Python: $PYTHON ($("$PYTHON" --version 2>&1))"

# 1. Backup
STAMP="$(date +%Y%m%d-%H%M)"
BACKUP="$BACKUP_ROOT/tw-news-backup-$STAMP"
mkdir -p "$BACKUP"
shopt -s nullglob
for db in data/*.db; do
  "$PYTHON" - "$db" "$BACKUP/$(basename "$db")" <<'PY'
import sqlite3, sys
src, dst = sys.argv[1], sys.argv[2]
s = sqlite3.connect(src); d = sqlite3.connect(dst)
s.backup(d)
ok = d.execute("PRAGMA integrity_check").fetchone()[0]
d.close(); s.close()
print(f"   backed up {src} -> {dst} ({ok})")
if ok != "ok":
    sys.exit(f"integrity check failed for {dst}")
PY
done
[[ -f .env ]] && cp -p .env "$BACKUP/.env"
echo "== Backup: $BACKUP"

# 2. Fetch
OLD="$(git rev-parse HEAD)"
if ! git diff --quiet || ! git diff --cached --quiet; then
  echo "!! Tracked files were edited on the server; not updating. git status:" >&2
  git status --short >&2
  exit 1
fi
git fetch -q origin "+refs/heads/$BRANCH:refs/remotes/origin/$BRANCH"
NEW="$(git rev-parse "origin/$BRANCH")"
if [[ -n "$EXPECT" && "$NEW" != "$EXPECT"* ]]; then
  echo "!! origin/$BRANCH is $NEW, expected $EXPECT; not updating." >&2
  exit 1
fi
echo "== Commit: $OLD -> $NEW ($BRANCH)"

# 3. Requirements and tests on a clean export
git show "$NEW:requirements.txt" > "$BACKUP/requirements.txt"
"$PYTHON" -m pip install -q -r "$BACKUP/requirements.txt"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT
git archive "$NEW" | tar -x -C "$TMP"
set +e
(cd "$TMP" && "$PYTHON" -m pytest -q -p no:cacheprovider) > "$BACKUP/tests.log" 2>&1
STATUS=$?
set -e
SUMMARY="$(tail -n 1 "$BACKUP/tests.log")"
echo "== Tests: $SUMMARY"
if [[ $STATUS -ne 0 ]]; then
  echo "!! Tests failed; production NOT updated. Full log: $BACKUP/tests.log" >&2
  exit 1
fi

# 4. Switch production code, timer paused
PAUSED=0
if command -v systemctl >/dev/null 2>&1 && systemctl is-active --quiet "$TIMER" 2>/dev/null; then
  systemctl stop "$TIMER"; PAUSED=1
fi
resume() { if [[ $PAUSED -eq 1 ]]; then systemctl start "$TIMER"; fi; }
trap 'resume; rm -rf "$TMP"' EXIT
git checkout -q -B "$BRANCH" "$NEW"
resume; PAUSED=0

# 5. Summary
"$PYTHON" - <<'PY'
import sqlite3
c = sqlite3.connect("data/news.db")
v = c.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
n = c.execute("SELECT COUNT(*) FROM articles").fetchone()[0]
print(f"== Database: schema {v[0] if v else '?'}, {n} articles")
PY
if command -v systemctl >/dev/null 2>&1; then
  echo "== Timer: $(systemctl is-active "$TIMER" 2>/dev/null || true)"
fi
echo "== Done. Rollback: git checkout -B $BRANCH $OLD   (backup in $BACKUP)"
