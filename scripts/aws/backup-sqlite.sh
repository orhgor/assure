#!/usr/bin/env bash
# Daily SQLite backup to Cloudflare R2 (S3-compatible API).
# Skips upload when free-tier storage cap would be exceeded; prunes first when possible.
# Credentials: /etc/assure/backup.env (see .env.production.example).
set -euo pipefail

ENV_FILE="${ASSURE_BACKUP_ENV:-/etc/assure/backup.env}"
if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE — run scripts/aws/setup-sqlite-backup.sh" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

: "${R2_ACCOUNT_ID:?R2_ACCOUNT_ID not set}"
: "${R2_BUCKET:?R2_BUCKET not set}"
: "${AWS_ACCESS_KEY_ID:?AWS_ACCESS_KEY_ID not set}"
: "${AWS_SECRET_ACCESS_KEY:?AWS_SECRET_ACCESS_KEY not set}"

DB_PATH="${ASSURE_SQLITE_PATH:-/home/ubuntu/assure/data/history.sqlite}"
R2_PREFIX="${R2_BACKUP_PREFIX:-backups}"
RETAIN_DAYS="${R2_BACKUP_RETAIN_DAYS:-30}"
MIN_KEEP="${R2_BACKUP_MIN_KEEP:-3}"
CAP_GB="${R2_STORAGE_CAP_GB:-8}"
ENDPOINT="https://${R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
S3_PREFIX="s3://${R2_BUCKET}/${R2_PREFIX}/"
START_TIME="$(date +%s%N 2>/dev/null || echo $(($(date +%s) * 1000000000)))"

export AWS_ACCESS_KEY_ID AWS_SECRET_ACCESS_KEY
export AWS_DEFAULT_REGION="${AWS_DEFAULT_REGION:-auto}"

log_backup_audit() {
  local success="$1"
  local duration_ms="${2:-0}"
  local error_message="${3:-}"
  local request_id="cron-backup-$(date +%s)"
  if ! command -v sqlite3 >/dev/null 2>&1 || [ ! -f "$DB_PATH" ]; then
    echo "Audit log skipped: sqlite3 or database unavailable" >&2
    return 0
  fi
  if [ "$success" -eq 1 ]; then
    sqlite3 "$DB_PATH" \
      "INSERT INTO audit_log (id, request_id, action, success, duration_ms, details) \
       VALUES (lower(hex(randomblob(16))), '${request_id}', 'BACKUP', 1, ${duration_ms}, '{\"target\":\"r2_backup\"}');" \
      2>/dev/null || echo "Audit log write failed (success)" >&2
  else
    local safe_err="${error_message//\'/''}"
    sqlite3 "$DB_PATH" \
      "INSERT INTO audit_log (id, request_id, action, success, duration_ms, error_message, details) \
       VALUES (lower(hex(randomblob(16))), '${request_id}', 'BACKUP', 0, ${duration_ms}, '${safe_err}', '{\"target\":\"r2_backup\"}');" \
      2>/dev/null || echo "Audit log write failed (failure)" >&2
  fi
}

backup_duration_ms() {
  local end
  end="$(date +%s%N 2>/dev/null || echo $(($(date +%s) * 1000000000)))"
  echo $(( (end - START_TIME) / 1000000 ))
}

on_backup_err() {
  log_backup_audit 0 "$(backup_duration_ms)" "${1:-Upload failed}"
}
trap 'on_backup_err' ERR

CAP_BYTES=$((CAP_GB * 1024 * 1024 * 1024))

human_bytes() {
  local b="$1"
  if [ "$b" -ge 1073741824 ]; then
    echo "$(awk "BEGIN {printf \"%.2f\", $b/1073741824}") GB"
  elif [ "$b" -ge 1048576 ]; then
    echo "$(awk "BEGIN {printf \"%.1f\", $b/1048576}") MB"
  else
    echo "${b} bytes"
  fi
}

list_backups() {
  aws s3 ls "$S3_PREFIX" --endpoint-url "$ENDPOINT" 2>/dev/null | awk '
    $4 ~ /^history_.*\.db\.gz$/ {
      print $1, $2, $3, $4
    }
  '
}

bucket_bytes() {
  list_backups | awk '{sum += $3} END {print sum+0}'
}

backup_count() {
  list_backups | wc -l | tr -d ' '
}

prune_older_than_days() {
  local days="$1"
  local cutoff_epoch=$(( $(date -u +%s) - days * 86400 ))
  local pruned=0
  while IFS= read -r line; do
    [ -z "$line" ] && continue
    local date_part name file_epoch
    date_part="$(echo "$line" | awk '{print $1, $2}')"
    name="$(echo "$line" | awk '{print $4}')"
    file_epoch="$(date -u -d "$date_part" +%s)"
    if [ "$file_epoch" -lt "$cutoff_epoch" ]; then
      aws s3 rm "${S3_PREFIX}${name}" --endpoint-url "$ENDPOINT"
      pruned=$((pruned + 1))
    fi
  done < <(list_backups)
  echo "$pruned"
}

prune_oldest_until_room() {
  local need_bytes="$1"
  local min_keep="$2"
  local pruned=0
  local used
  used="$(bucket_bytes)"
  while [ "$((used + need_bytes))" -gt "$CAP_BYTES" ]; do
    if [ "$(backup_count)" -le "$min_keep" ]; then
      break
    fi
    local oldest
    oldest="$(list_backups | sort -k1,2 | head -1 | awk '{print $4}')"
    [ -z "$oldest" ] && break
    aws s3 rm "${S3_PREFIX}${oldest}" --endpoint-url "$ENDPOINT"
    pruned=$((pruned + 1))
    used="$(bucket_bytes)"
  done
  echo "$pruned"
}

skip_no_upload() {
  local reason="$1"
  rm -f "$2"
  log_backup_audit 0 "$(backup_duration_ms)" "$reason"
  trap - ERR
  echo "BACKUP SKIPPED: $reason" >&2
  echo "Actions: lower R2_BACKUP_RETAIN_DAYS, vacuum/shrink SQLite, or raise R2_STORAGE_CAP_GB (paid tier)." >&2
  exit 1
}

if [ ! -f "$DB_PATH" ]; then
  echo "Database not found: $DB_PATH" >&2
  exit 1
fi

if ! command -v sqlite3 >/dev/null 2>&1; then
  echo "sqlite3 not installed" >&2
  exit 1
fi

if ! command -v aws >/dev/null 2>&1; then
  echo "aws CLI not installed" >&2
  exit 1
fi

DB_BYTES="$(stat -c%s "$DB_PATH")"
echo "Database size: $(human_bytes "$DB_BYTES")"
if [ "$DB_BYTES" -gt "$CAP_BYTES" ]; then
  log_backup_audit 0 "$(backup_duration_ms)" "database exceeds storage cap"
  trap - ERR
  echo "BACKUP SKIPPED: database file alone exceeds ${CAP_GB} GB cap ($(human_bytes "$DB_BYTES"))." >&2
  echo "Actions: run SQLite VACUUM, archive old history locally, or use paid R2 storage." >&2
  exit 1
fi

DATE="$(date -u +%Y%m%d_%H%M%S)"
TMP="/tmp/assure_backup_${DATE}.db"
ARCHIVE="${TMP}.gz"

sqlite3 "$DB_PATH" ".backup '${TMP}'"
gzip -f "$TMP"
NEW_BYTES="$(stat -c%s "$ARCHIVE")"
echo "Compressed backup size: $(human_bytes "$NEW_BYTES")"

if [ "$NEW_BYTES" -gt "$CAP_BYTES" ]; then
  skip_no_upload "single compressed backup exceeds ${CAP_GB} GB cap" "$ARCHIVE"
fi

USED_BYTES="$(bucket_bytes)"
echo "R2 backup prefix usage: $(human_bytes "$USED_BYTES") / $(human_bytes "$CAP_BYTES") cap"

if [ "$((USED_BYTES + NEW_BYTES))" -gt "$CAP_BYTES" ]; then
  echo "Over cap — pruning backups older than ${RETAIN_DAYS} days..."
  RETENTION_PRUNED="$(prune_older_than_days "$RETAIN_DAYS")"
  echo "Retention prune removed ${RETENTION_PRUNED} object(s)"
  USED_BYTES="$(bucket_bytes)"
fi

if [ "$((USED_BYTES + NEW_BYTES))" -gt "$CAP_BYTES" ]; then
  echo "Still over cap — emergency prune of oldest backups (keeping ${MIN_KEEP})..."
  EMERGENCY_PRUNED="$(prune_oldest_until_room "$NEW_BYTES" "$MIN_KEEP")"
  echo "Emergency prune removed ${EMERGENCY_PRUNED} object(s)"
  USED_BYTES="$(bucket_bytes)"
fi

PROJECTED=$((USED_BYTES + NEW_BYTES))
if [ "$PROJECTED" -gt "$CAP_BYTES" ]; then
  skip_no_upload \
    "projected usage $(human_bytes "$PROJECTED") exceeds ${CAP_GB} GB cap after pruning (keeping min ${MIN_KEEP})" \
    "$ARCHIVE"
fi

KEY="${R2_PREFIX}/history_${DATE}.db.gz"
aws s3 cp "$ARCHIVE" "s3://${R2_BUCKET}/${KEY}" --endpoint-url "$ENDPOINT"
rm -f "$ARCHIVE"
BACKUP_DURATION_MS="$(backup_duration_ms)"
log_backup_audit 1 "$BACKUP_DURATION_MS"
trap - ERR
echo "Backup uploaded: s3://${R2_BUCKET}/${KEY}"
echo "Projected R2 usage after upload: $(human_bytes "$PROJECTED")"
