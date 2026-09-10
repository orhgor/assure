#!/usr/bin/env bash
# Health-based auto-heal for native systemd Assure (replaces Docker compose cron).
set -euo pipefail

SERVICE_NAME="${ASSURE_SERVICE_NAME:-assure}"
CRON_LINE="*/5 * * * * curl -sf http://127.0.0.1:8765/health >/dev/null || sudo systemctl restart ${SERVICE_NAME}"

(
  crontab -l 2>/dev/null | grep -v '127.0.0.1:8765/health' | grep -v 'docker compose up -d assure-app' || true
  echo "$CRON_LINE"
) | crontab -

echo "Installed lean auto-heal cron:"
crontab -l | grep '127.0.0.1:8765/health'
