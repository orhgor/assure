#!/usr/bin/env bash
# Generate a one-liner to paste into an SSM session (run on your Mac, NOT on EC2).
# Usage: bash scripts/aws/emit-ssm-bootstrap.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$ROOT/.env.production"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"

if [ -z "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]; then
  echo "CLOUDFLARE_TUNNEL_TOKEN not set in $ENV_FILE" >&2
  exit 1
fi

GIT_BRANCH="${ASSURE_GIT_REF:-p4-account-wallet}"
GIT_URL="${ASSURE_GIT_URL:-https://github.com/orhgor/assure.git}"

# Build inner script; token injected at generation time (never committed).
INNER="$(mktemp)"
trap 'rm -f "$INNER"' EXIT

cat > "$INNER" <<SCRIPT
#!/usr/bin/env bash
set -euxo pipefail
export DEBIAN_FRONTEND=noninteractive
export CLOUDFLARE_TUNNEL_TOKEN='${CLOUDFLARE_TUNNEL_TOKEN}'

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
  curl -L -o /tmp/cloudflared.deb https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-arm64.deb
  dpkg -i /tmp/cloudflared.deb
  rm -f /tmp/cloudflared.deb
fi

if ! systemctl is-active --quiet cloudflared 2>/dev/null; then
  cloudflared service install "\$CLOUDFLARE_TUNNEL_TOKEN"
  systemctl enable --now cloudflared
fi

install -d -o ubuntu -g ubuntu /home/ubuntu/assure/data
if [ ! -d /home/ubuntu/assure/.git ]; then
  sudo -u ubuntu git clone --branch ${GIT_BRANCH} --depth 1 ${GIT_URL} /home/ubuntu/assure
fi

cat > /home/ubuntu/assure/.env.production <<'ENVEOF'
$(cat "$ENV_FILE")
ENVEOF
chown ubuntu:ubuntu /home/ubuntu/assure/.env.production
chmod 600 /home/ubuntu/assure/.env.production

cd /home/ubuntu/assure
sudo -u ubuntu docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build assure-app

echo "Bootstrap done. Tunnel:"
systemctl is-active cloudflared || true
docker ps
SCRIPT

B64="$(base64 < "$INNER" | tr -d '\n')"

echo "=== Paste this ONE line into your SSM session ==="
echo ""
echo "sudo bash -c 'echo ${B64} | base64 -d | bash'"
echo ""
echo "=== Or: copy to clipboard on Mac ==="
echo "bash scripts/aws/emit-ssm-bootstrap.sh 2>/dev/null | tail -1 | pbcopy"
