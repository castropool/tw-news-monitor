#!/usr/bin/env bash
# One-command verification run on the Linux server.
#
#   sudo scripts/verify_all.sh            # everything (spends a little of the AI budget)
#   sudo scripts/verify_all.sh --no-ai    # skip steps that call the model
#
# Runs every check without typing long commands, saves all output under
# $VERIFY_ROOT/verify-<stamp>/ (default /opt/backups), and splits REPORT.md
# into report_01.txt, report_02.txt, ... of about 3000 bytes each so they
# can be copied out of a console one at a time.  Read-only except: it
# re-parses stored PLA reports, tags articles to programs, extracts a few
# claims and writes draft files (never approves, never sends anything).
# Never edits .env or tracked files; enabling features is a separate
# command (scripts/enable_features.sh).
set -u

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PY="${PYTHON:-}"
if [[ -z "$PY" ]]; then
  for c in .venv/bin/python venv/bin/python python3; do
    if [[ -x "$c" ]] || command -v "$c" >/dev/null 2>&1; then PY="$c"; break; fi
  done
fi
NO_AI=0
[[ "${1:-}" == "--no-ai" ]] && NO_AI=1
STAMP="$(date +%Y%m%d-%H%M)"
OUT="${VERIFY_ROOT:-/opt/backups}/verify-$STAMP"
mkdir -p "$OUT"
REPORT="$OUT/REPORT.md"
: > "$REPORT"

section() { printf '\n## %s\n\n' "$1" >> "$REPORT"; echo "== $1"; }
# run "label" command...: output goes to the report (truncated to ~6000 bytes) and a full log.
run() {
  local label="$1"; shift
  local log="$OUT/$(echo "$label" | tr -c 'A-Za-z0-9' '_').log"
  timeout "${STEP_TIMEOUT:-900}" "$@" > "$log" 2>&1
  local rc=$?
  { printf '### %s (exit %s)\n\n```text\n' "$label" "$rc"; head -c 6000 "$log"
    [[ $(wc -c < "$log") -gt 6000 ]] && printf '\n... (truncated; full log %s)' "$log"
    printf '\n```\n\n'; } >> "$REPORT"
}
SQLQ="$OUT/sqlq.py"
cat > "$SQLQ" <<'PY'
import sqlite3, sys
conn = sqlite3.connect("data/news.db")
for row in conn.execute(sys.argv[1]).fetchall():
    print(" | ".join("" if v is None else str(v) for v in row))
PY
sql() { "$PY" "$SQLQ" "$@"; }

{ echo "# Verification run $STAMP"; echo; echo "- commit: $(git rev-parse --short HEAD)"; echo "- python: $PY"; } >> "$REPORT"

section "B. PLA data"
run "B1 reparse stored" "$PY" -m app.military.pla_activity --reparse-stored
run "B2 table stats (rows, crossed NULL, balloons set, first, last)" "$PY" "$SQLQ" \
  "SELECT COUNT(*), SUM(aircraft_crossed IS NULL), SUM(balloons IS NOT NULL), MIN(report_date), MAX(report_date) FROM pla_activity_daily"
RANDOM_DATES=$(sql "SELECT report_date FROM (SELECT report_date FROM pla_activity_daily WHERE report_date < '2025-01-01' ORDER BY random() LIMIT 2) UNION ALL SELECT report_date FROM (SELECT report_date FROM pla_activity_daily WHERE report_date >= '2025-01-01' AND report_date NOT IN ('2026-01-15','2025-10-26','2025-02-20','2025-12-10','2026-07-18') ORDER BY random() LIMIT 3)" | tr '\n' ' ')
run "B3 report text and parse for 10 dates" "$PY" -m app.military.pla_activity --text \
  2026-01-15 2025-10-26 2025-02-20 2025-12-10 2026-07-18 $RANDOM_DATES
run "B4 unparsed pages (3 samples)" "$PY" -m app.military.pla_activity --unparsed 3
run "B5 baseline" "$PY" -m app.military.pla_baseline
run "B6 backtest 2024-05-01..2025-06-30" "$PY" -m app.military.pla_backtest --since 2024-05-01 --until 2025-06-30
run "B7 counts around labelled incidents" "$PY" "$SQLQ" \
  "SELECT report_date, aircraft, aircraft_crossed, vessels, official_ships FROM pla_activity_daily WHERE report_date BETWEEN '2024-05-21' AND '2024-05-26' OR report_date BETWEEN '2024-10-12' AND '2024-10-16' OR report_date BETWEEN '2025-03-30' AND '2025-04-04' ORDER BY report_date"

section "C. Program tagging"
run "C1 tag last 14 days" "$PY" -m app.programs.tagging --days 14
for P in P1 P2 P3 P5 P6; do
  run "C2 $P samples" "$PY" "$SQLQ" "SELECT a.title FROM program_tags t JOIN articles a ON a.url = t.url WHERE t.program = '$P' ORDER BY random() LIMIT 5"
done

if [[ $NO_AI -eq 0 ]]; then
  section "D. Fact extraction"
  run "D0 AI usage before" "$PY" -m app.ai_usage
  run "D1 extract P4" "$PY" -m app.pilot.claims extract --program P4 --days 7 --limit 10
  run "D2 extract P3" "$PY" -m app.pilot.claims extract --program P3 --days 14 --limit 5
  run "D3 P4 facts with article context" "$PY" - <<'PY'
import sqlite3
conn = sqlite3.connect("data/news.db")
for program in ("P4", "P3"):
    print(f"===== {program}")
    rows = conn.execute(
        "SELECT c.id, c.valid, c.reject_reason, c.claim_type, c.kind, c.ladder, c.actor, c.action, c.quantity, c.unit, "
        "c.event_date, c.quote, c.quote_start, a.title, t.text FROM claims c JOIN articles a ON a.url = c.article_url "
        "LEFT JOIN article_texts t ON t.url = c.article_url WHERE c.program = ? ORDER BY c.id DESC LIMIT 15", (program,))
    for (cid, valid, reason, ctype, kind, ladder, actor, action, qty, unit, day, quote, start, title, text) in rows:
        print(f"#{cid} valid={valid} {reason or ''} [{ctype}/{kind}/{ladder}] {day} {actor} {action} {qty or ''}{unit or ''}")
        print(f"  title: {title}")
        print(f"  quote: {quote}")
        if text and start is not None:
            print(f"  context: ...{text[max(0, start - 120): start + len(quote) + 120]}...")
PY
  run "D4 AI usage after" "$PY" -m app.ai_usage

  section "E. Weekly drafts"
  run "E1 P4 draft" "$PY" -m app.pilot.weekly generate --week-ending 2026-11-14 --force
  run "E1 P4 draft text" cat data/assessments/p4/2026-11-14/draft.md
  run "E2 P3 draft" "$PY" -m app.programs.weekly generate --program P3 --week-ending 2026-11-14 --force
  run "E2 P3 draft text" cat data/assessments/p3/2026-11-14/draft.md

  section "F. Assistant"
  STEP_TIMEOUT=1200 run "F1 assistant ask" "$PY" -m app.programs.assistant ask "最近共機逾越中線的情況如何？" --program P4
  run "F2 assistant eval" "$PY" -m app.programs.assistant eval
fi

section "F. Tools"
run "F3 leads gaps P3" "$PY" -m app.programs.leads gaps --program P3
run "F4 registry seed" "$PY" -m app.programs.registry seed
run "F5 daily digest" "$PY" -m app.programs.daily
run "F6 search" "$PY" -m app.search 國防部 共機 --limit 5
run "F7 AI usage" "$PY" -m app.ai_usage

section "G. News run and settings"
if command -v systemctl >/dev/null 2>&1; then
  run "G1 news cycle" systemctl start tw-news-monitor.service
  run "G2 journal tail" journalctl -u tw-news-monitor.service -n 40 --no-pager
  run "G3 timer" systemctl list-timers tw-news-monitor.timer --no-pager
fi
{
  echo "### G4 feature switches in .env"
  echo
  for flag in PLA_ACTIVITY_ENABLED PROGRAMS_TAGGING_ENABLED PILOT_EXTRACTION_ENABLED WEEKLY_AUTO_DRAFT PROGRAM_DELIVERY_ENABLED PROGRAM_NOTIFIER; do
    if grep -q "^${flag}=" .env 2>/dev/null; then echo "- $flag: set"; else echo "- $flag: not set"; fi
  done
} >> "$REPORT"

split -C 3000 -d -a 2 --additional-suffix=.txt "$REPORT" "$OUT/report_"
PARTS=$(ls "$OUT"/report_*.txt | wc -l)
echo
echo "Done. Report: $REPORT ($PARTS parts)."
echo "Show part 1 with:  cat $OUT/report_00.txt"
