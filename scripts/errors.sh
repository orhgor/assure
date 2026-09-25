#!/usr/bin/env bash
# One command for "what is wrong right now":
#
#   ./scripts/errors.sh            # last 30 minutes of errors from every service
#   ./scripts/errors.sh 2h         # a different window (docker --since syntax)
#   ./scripts/errors.sh -f         # keep following, errors only
#   ./scripts/errors.sh --all      # full logs of the window, not just errors
#
# Prints: `docker compose ps`, the /health summary (status, db, models,
# degraded), then every log line that carries ERROR / CRITICAL / Traceback /
# exception / failed / denied / refused / unhealthy — with its service name —
# plus the 12 lines after each Traceback so the cause is visible. Read-only.
set -uo pipefail
cd "$(dirname "$0")/.."
SINCE="30m"; FOLLOW=""; ALL=0
for a in "$@"; do
  case "$a" in
    -f|--follow) FOLLOW="-f" ;;
    --all) ALL=1 ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) SINCE="$a" ;;
  esac
done

bold() { printf '\033[1m%s\033[0m\n' "$*"; }

bold "== services"
docker compose ps --format 'table {{.Name}}\t{{.Status}}\t{{.Ports}}' 2>&1

bold "== health (http://127.0.0.1:8765/health)"
if command -v python3 >/dev/null 2>&1; then
  curl -s --max-time 5 http://127.0.0.1:8765/health | python3 -c '
import json, sys
try:
    d = json.load(sys.stdin)
except Exception:
    print("  no answer from the API (container down or still starting)"); sys.exit(0)
c = d.get("checks", {})
m = c.get("models") or {}
print("  status:", d.get("status"), "| degraded:", d.get("degraded"), "| db:", c.get("db"), "| disk:", c.get("disk"), "free_gb:", c.get("disk_free_gb"))
print("  models:", m.get("status"), "| present:", m.get("present"), "| missing:", m.get("missing") or m.get("error"))
' 2>/dev/null || echo "  no answer from the API"
else
  curl -s --max-time 5 http://127.0.0.1:8765/health || echo "  no answer from the API"
fi

bold "== errors in the last $SINCE (all services)"
# Word-bounded and without task-result dumps: a *succeeded* Celery line that
# prints `'chunks_failed': 0` is not an error (false positive seen 2026-09-25).
PATTERN='\b(ERROR|CRITICAL|Traceback|Exception|exception|failed|Failed|FAILED|denied|refused|unhealthy|Cannot|cannot|timed out|PoolTimeout|OperationalError)\b|No such|not found'
EXCLUDE='inspect ping|succeeded in|_failed.: 0|failed.: 0'
if [[ "$ALL" == "1" ]]; then
  exec docker compose logs $FOLLOW --since "$SINCE" --timestamps
fi
if [[ -n "$FOLLOW" ]]; then
  exec docker compose logs -f --since "$SINCE" --timestamps 2>&1 | grep -E --line-buffered "$PATTERN" | grep -Ev --line-buffered "$EXCLUDE"
fi
out=$(docker compose logs --since "$SINCE" --timestamps 2>&1 | grep -Ev "$EXCLUDE")
matches=$(printf '%s\n' "$out" | grep -E -A12 "Traceback" ; printf '%s\n' "$out" | grep -E "$PATTERN" | grep -v "Traceback")
if [[ -z "${matches// }" ]]; then
  echo "  none"
else
  printf '%s\n' "$matches" | awk '!seen[$0]++' | tail -n 400
fi
