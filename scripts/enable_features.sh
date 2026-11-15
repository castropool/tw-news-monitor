#!/usr/bin/env bash
# Switch on reviewed features in .env with one short command.
#
#   sudo scripts/enable_features.sh pla tagging extraction weekly
#
# Names: pla (PLA_ACTIVITY_ENABLED), tagging (PROGRAMS_TAGGING_ENABLED),
# extraction (PILOT_EXTRACTION_ENABLED), weekly (WEEKLY_AUTO_DRAFT),
# delivery (PROGRAM_DELIVERY_ENABLED; also needs PROGRAM_NOTIFIER or NOTIFIER).
# Appends NAME=true only when NAME is not already set; backs .env up first;
# never prints .env values.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
ENV_FILE="$ROOT/.env"
[[ -f "$ENV_FILE" ]] || { echo "!! $ENV_FILE not found" >&2; exit 1; }
[[ $# -gt 0 ]] || { echo "usage: scripts/enable_features.sh pla|tagging|extraction|weekly|delivery ..." >&2; exit 1; }
cp -p "$ENV_FILE" "$ENV_FILE.bak-$(date +%Y%m%d-%H%M%S)"
for name in "$@"; do
  case "$name" in
    pla) flag=PLA_ACTIVITY_ENABLED ;;
    tagging) flag=PROGRAMS_TAGGING_ENABLED ;;
    extraction) flag=PILOT_EXTRACTION_ENABLED ;;
    weekly) flag=WEEKLY_AUTO_DRAFT ;;
    delivery) flag=PROGRAM_DELIVERY_ENABLED ;;
    *) echo "!! unknown feature: $name" >&2; exit 1 ;;
  esac
  if grep -q "^${flag}=" "$ENV_FILE"; then
    echo "$flag already set (left unchanged)"
  else
    printf '\n%s=true\n' "$flag" >> "$ENV_FILE"
    echo "$flag=true added"
  fi
done
