#!/usr/bin/env bash
# One-time EC2 setup: native systemd service + lean auto-heal (no Docker for the app).
# Run ON the box as ubuntu, or: ssh ubuntu@HOST 'bash -s' < scripts/aws/install-lean-service.sh
set -euo pipefail

ASSURE_ROOT="${ASSURE_ROOT:-/home/ubuntu/assure}"
SERVICE_NAME="${ASSURE_SERVICE_NAME:-assure}"
ENVIRONMENT="${ASSURE_ENVIRONMENT:-staging}"
UNIT_SRC="${ASSURE_ROOT}/scripts/aws/assure.service"
UNIT_DST="/etc/systemd/system/${SERVICE_NAME}.service"

if [[ "$ENVIRONMENT" == "production" ]]; then
  DATA_DIR="$ASSURE_ROOT/data"
  ENV_FILE="$ASSURE_ROOT/.env.production"
  FREE_MODELS="0"
else
  DATA_DIR="$ASSURE_ROOT/data-staging"
  ENV_FILE="$ASSURE_ROOT/.env.staging"
  FREE_MODELS="1"
fi

if [[ ! -f "$UNIT_SRC" ]]; then
  echo "Missing $UNIT_SRC — sync the repo first (scripts/lean-deploy.sh)." >&2
  exit 1
fi

echo "==> Python venv"
cd "$ASSURE_ROOT"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt gunicorn gevent

echo "==> Data directory ($ENVIRONMENT SQLite)"
mkdir -p "$DATA_DIR"

echo "==> Install systemd unit ($ENVIRONMENT)"
tmp_unit="$(mktemp)"
sed \
  -e "s|EnvironmentFile=-/home/ubuntu/assure/.env.staging|EnvironmentFile=-${ENV_FILE}|" \
  -e "s|Environment=ASSURE_ENV=staging|Environment=ASSURE_ENV=${ENVIRONMENT}|" \
  -e "s|Environment=ENVIRONMENT=staging|Environment=ENVIRONMENT=${ENVIRONMENT}|" \
  -e "s|Environment=ASSURE_USE_FREE_MODELS=1|Environment=ASSURE_USE_FREE_MODELS=${FREE_MODELS}|" \
  -e "s|/home/ubuntu/assure/data-staging/history.sqlite|${DATA_DIR}/history.sqlite|" \
  "$UNIT_SRC" > "$tmp_unit"
sudo cp "$tmp_unit" "$UNIT_DST"
rm -f "$tmp_unit"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"

echo "==> Stop legacy Docker app (free port 8765)"
if command -v docker >/dev/null 2>&1; then
  docker compose -f docker-compose.yml -f docker-compose.staging.yml stop assure-app 2>/dev/null || true
  docker compose -f docker-compose.yml -f docker-compose.prod.yml stop assure-app 2>/dev/null || true
fi

echo "==> Lean auto-heal cron (systemd restart, not docker compose)"
CRON_LINE="*/5 * * * * curl -sf http://127.0.0.1:8765/health >/dev/null || sudo systemctl restart ${SERVICE_NAME}"
(
  crontab -l 2>/dev/null | grep -v '127.0.0.1:8765/health' | grep -v 'docker compose up -d assure-app' || true
  echo "$CRON_LINE"
) | crontab -

echo "==> Start native service"
sudo systemctl restart "$SERVICE_NAME"
for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8765/health >/dev/null 2>&1; then
    curl -sf http://127.0.0.1:8765/health | python3 -m json.tool | head -25
    sudo systemctl --no-pager status "$SERVICE_NAME" || true
    exit 0
  fi
  sleep 1
done
sudo journalctl -u "$SERVICE_NAME" -n 50 --no-pager
exit 1

echo "OK: Assure running natively on :8765 (cloudflared unchanged)."
