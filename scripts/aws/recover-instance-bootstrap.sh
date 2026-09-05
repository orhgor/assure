#!/usr/bin/env bash
# Manual bootstrap for an EC2 instance when cloud-init user-data failed.
# Run on the instance via SSM: sudo bash recover-instance-bootstrap.sh
set -euxo pipefail

if [ "$(id -u)" -ne 0 ]; then
  echo "Run as root: sudo bash $0" >&2
  exit 1
fi

export DEBIAN_FRONTEND=noninteractive
GIT_BRANCH="${ASSURE_GIT_REF:-p4-account-wallet}"
GIT_URL="${ASSURE_GIT_URL:-https://github.com/orhgor/assure.git}"

if [ ! -f /tmp/assure.env.production ]; then
  echo "Missing /tmp/assure.env.production — create it first, then re-run." >&2
  echo "  cat > /tmp/assure.env.production <<'ENVEOF'" >&2
  echo "  (paste .env.production from laptop)" >&2
  echo "  ENVEOF" >&2
  exit 1
fi

# shellcheck disable=SC1091
source /tmp/assure.env.production
TUNNEL_ID="${TUNNEL_ID:-}"
APP_HOST="${APP_HOST:-getassureai.com}"

if [ -z "$TUNNEL_ID" ]; then
  echo "TUNNEL_ID not set in /tmp/assure.env.production" >&2
  exit 1
fi

apt-get update && apt-get upgrade -y

if [ ! -f /swapfile ]; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile
  mkswap /swapfile
  swapon /swapfile
  grep -q '/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi

if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sh
  usermod -aG docker ubuntu
  systemctl enable docker
  systemctl start docker
fi

if ! command -v cloudflared >/dev/null 2>&1; then
  curl -L --output /tmp/cloudflared.deb \
    https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
  dpkg -i /tmp/cloudflared.deb
  rm -f /tmp/cloudflared.deb
fi

install -d -o ubuntu -g ubuntu /home/ubuntu/assure/data
if [ ! -d /home/ubuntu/assure/.git ]; then
  sudo -u ubuntu git clone --branch "$GIT_BRANCH" --depth 1 "$GIT_URL" /home/ubuntu/assure
fi

install -o ubuntu -g ubuntu -m 600 /tmp/assure.env.production /home/ubuntu/assure/.env.production

CREDS="/etc/cloudflared/${TUNNEL_ID}.json"
mkdir -p /etc/cloudflared

if [ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]; then
  cloudflared service install "$CLOUDFLARE_TUNNEL_TOKEN"
elif [ -f "$CREDS" ]; then
  cat > /etc/cloudflared/config.yml <<EOF
tunnel: ${TUNNEL_ID}
credentials-file: ${CREDS}

ingress:
  - hostname: ${APP_HOST}
    service: http://localhost:8765
  - service: http_status:404
EOF
  cloudflared --config /etc/cloudflared/config.yml service install
else
  echo "Set CLOUDFLARE_TUNNEL_TOKEN in /tmp/assure.env.production or place credentials at $CREDS" >&2
  exit 1
fi
systemctl daemon-reload
systemctl enable --now cloudflared

cd /home/ubuntu/assure
sudo -u ubuntu docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build assure-app

echo "Recovery bootstrap complete at $(date -u +"%Y-%m-%dT%H:%M:%SZ")"
