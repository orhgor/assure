#!/usr/bin/env bash
# Free-tier disk cleanup for EC2 — no EBS resize required.
# Safe to run while assure-app is up. Does NOT delete SQLite WAL files.
set -euo pipefail

ROOT="${ASSURE_ROOT:-/home/ubuntu/assure}"
DATA_DIR="${ASSURE_DATA_DIR:-$ROOT/data}"
DB_PATH="${ASSURE_SQLITE_PATH:-$DATA_DIR/history.sqlite}"
MIN_FREE_GB="${ASSURE_MIN_FREE_GB:-2}"

echo "==> Disk before"
df -h / | awk 'NR<=2'

echo "==> Docker prune (unused images of any age; keep the running container image)"
docker image prune -af
docker system prune -f
docker volume prune -f 2>/dev/null || true
docker builder prune -af 2>/dev/null || true

echo "==> Journal vacuum (keep last 100M)"
if command -v journalctl >/dev/null 2>&1; then
  sudo journalctl --rotate 2>/dev/null || true
  sudo journalctl --vacuum-size=100M 2>/dev/null || true
fi

echo "==> Python __pycache__ under repo"
find "$ROOT" -type d -name "__pycache__" -prune -exec rm -rf {} + 2>/dev/null || true

echo "==> SQLite WAL checkpoint (online; do not rm -wal/-shm)"
if [[ -f "$DB_PATH" ]] && command -v sqlite3 >/dev/null 2>&1; then
  sqlite3 "$DB_PATH" "PRAGMA wal_checkpoint(TRUNCATE);" 2>/dev/null || true
fi

echo "==> Optional audit/metrics prune (90d audit, 30d metrics)"
if [[ -f "$ROOT/scripts/prune_logs.sh" ]]; then
  bash "$ROOT/scripts/prune_logs.sh" 2>/dev/null || true
fi

echo "==> Disk after"
df -h / | awk 'NR<=2'
FREE_GB="$(df -BG / 2>/dev/null | awk 'NR==2 {gsub(/G/,"",$4); print $4}' || echo 0)"
echo "Free GB: ${FREE_GB}"
if [[ "${FREE_GB:-0}" -lt "$MIN_FREE_GB" ]]; then
  echo "WARN: still below ${MIN_FREE_GB}GB free — consider removing old GHCR tags manually." >&2
  exit 1
fi
echo "OK: cleanup complete."
