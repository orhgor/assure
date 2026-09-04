#!/usr/bin/env bash
# Install health-based auto-heal cron on the EC2 host (safe to re-run).
set -euo pipefail

CRON_LINE='*/5 * * * * curl -sf http://127.0.0.1:8765/health >/dev/null || (cd /home/ubuntu/assure && docker compose -f docker-compose.yml -f docker-compose.prod.yml rm -f -s assure-app 2>/dev/null; docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d assure-app)'

(
  crontab -l 2>/dev/null | grep -v '127.0.0.1:8765/health' | grep -v 'docker compose up -d assure-app' || true
  echo "$CRON_LINE"
) | crontab -

echo "Installed auto-heal cron:"
crontab -l | grep '127.0.0.1:8765/health'
