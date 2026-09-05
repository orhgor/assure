#!/usr/bin/env bash
# Install $0-cost disk maintenance cron on EC2 (safe to re-run).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ASSURE_ROOT="${ASSURE_ROOT:-/home/ubuntu/assure}"
PRUNE_LOGS="$ASSURE_ROOT/scripts/prune_logs.sh"
CLEANUP="$ASSURE_ROOT/scripts/aws/free-disk-cleanup.sh"

PRUNE_CRON='0 3 * * * cd /home/ubuntu/assure && bash scripts/prune_logs.sh >> /var/log/assure-prune.log 2>&1'
DOCKER_CRON='0 4 * * 0 cd /home/ubuntu/assure && bash scripts/aws/free-disk-cleanup.sh >> /var/log/assure-disk-cleanup.log 2>&1'

(
  crontab -l 2>/dev/null \
    | grep -v 'scripts/prune_logs.sh' \
    | grep -v 'free-disk-cleanup.sh' \
    || true
  echo "$PRUNE_CRON"
  echo "$DOCKER_CRON"
) | crontab -

echo "Installed disk maintenance cron:"
crontab -l | grep -E 'prune_logs|free-disk-cleanup'
