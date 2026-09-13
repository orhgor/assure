#!/usr/bin/env bash
# lean-deploy.sh — Direct local-to-EC2 sync and native restart (no Docker, no GitHub CI).
#
# Prerequisites on your laptop:
#   - SSH access to the staging/production box (Cloudflare Tunnel does not replace SSH).
#   - One-time on EC2: bash scripts/aws/install-lean-service.sh
#
# Usage:
#   ASSURE_SSH_HOST=ubuntu@<ec2-ip-or-host> ./scripts/lean-deploy.sh
#   ASSURE_ENVIRONMENT=staging ./scripts/lean-deploy.sh
#
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

HOST="${ASSURE_SSH_HOST:-}"
REMOTE_DIR="${ASSURE_REMOTE_DIR:-/home/ubuntu/assure}"
SERVICE_NAME="${ASSURE_SERVICE_NAME:-assure}"
HEALTH_URL="${ASSURE_HEALTH_URL:-http://127.0.0.1:8765/health}"
ENVIRONMENT="${ASSURE_ENVIRONMENT:-staging}"

if [[ -z "$HOST" ]]; then
  echo "Set ASSURE_SSH_HOST (e.g. ubuntu@54.x.x.x or ubuntu@staging-host)." >&2
  echo "Example: ASSURE_SSH_HOST=ubuntu@ec2.example.com $0" >&2
  exit 1
fi

SSH_OPTS=(-o BatchMode=yes -o StrictHostKeyChecking=accept-new)
RSYNC_SSH="ssh ${SSH_OPTS[*]}"

echo "==> Syncing code to EC2 ($HOST:$REMOTE_DIR)..."
rsync -avz --delete \
  --exclude '.git/' \
  --exclude '.venv/' \
  --exclude '.venv312/' \
  --exclude '__pycache__/' \
  --exclude '*.pyc' \
  --exclude '.cursor/' \
  --exclude 'node_modules/' \
  --exclude 'data/' \
  --exclude 'data-staging/' \
  --exclude 'dist/' \
  --exclude 'build/' \
  --exclude '.pytest_cache/' \
  -e "$RSYNC_SSH" \
  ./ "$HOST:$REMOTE_DIR/"

echo "==> Installing Python deps and restarting native service..."
ssh "${SSH_OPTS[@]}" "$HOST" bash -s <<EOF
set -euo pipefail
cd "$REMOTE_DIR"
if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q --upgrade pip
pip install -q -r requirements.txt gunicorn gevent
if [[ "$ENVIRONMENT" == "staging" && -f .env.staging ]]; then
  set -a
  # shellcheck disable=SC1091
  source .env.staging
  set +a
fi
# Release loopback port if legacy Docker app is still bound.
if command -v docker >/dev/null 2>&1; then
  docker compose -f docker-compose.yml -f docker-compose.staging.yml stop assure-app 2>/dev/null || true
  docker compose -f docker-compose.yml -f docker-compose.prod.yml stop assure-app 2>/dev/null || true
fi
sudo systemctl restart "$SERVICE_NAME"
for i in \$(seq 1 30); do
  if curl -sf "$HEALTH_URL" >/dev/null 2>&1; then
    echo "Health OK"
    curl -sf "$HEALTH_URL" | python3 -m json.tool | head -20
    exit 0
  fi
  sleep 1
done
echo "Service restarted but health check did not pass within 30s." >&2
sudo journalctl -u "$SERVICE_NAME" -n 40 --no-pager || true
exit 1
EOF

echo "==> Deployment live via Cloudflare Tunnel → :8765"
