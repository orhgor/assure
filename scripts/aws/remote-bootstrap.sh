#!/usr/bin/env bash
# Run bootstrap on EC2 via SSM SendCommand (non-interactive). Run from your Mac.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$ROOT/.env.production"
INSTANCE_ID="${1:-i-09d0ad0b561113abe}"
REGION="${AWS_REGION:-us-east-1}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE" >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$ENV_FILE"
GIT_BRANCH="${ASSURE_GIT_REF:-p4-account-wallet}"

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
if [ -d /home/ubuntu/assure/.git ]; then
  cd /home/ubuntu/assure
  sudo -u ubuntu git fetch --depth 1 origin "${GIT_BRANCH}"
  sudo -u ubuntu git checkout "${GIT_BRANCH}"
  sudo -u ubuntu git reset --hard "origin/${GIT_BRANCH}"
elif [ -d /home/ubuntu/assure ] && [ "$(ls -A /home/ubuntu/assure 2>/dev/null | wc -l)" -gt 0 ]; then
  rm -rf /home/ubuntu/assure
  sudo -u ubuntu git clone --branch "${GIT_BRANCH}" --depth 1 https://github.com/orhgor/assure.git /home/ubuntu/assure
else
  sudo -u ubuntu git clone --branch "${GIT_BRANCH}" --depth 1 https://github.com/orhgor/assure.git /home/ubuntu/assure
fi

cat > /home/ubuntu/assure/.env.production <<'ENVEOF'
$(cat "$ENV_FILE")
ENVEOF
chown ubuntu:ubuntu /home/ubuntu/assure/.env.production
chmod 600 /home/ubuntu/assure/.env.production

cd /home/ubuntu/assure
sudo -u ubuntu docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d --build assure-app

echo BOOTSTRAP_DONE
SCRIPT

B64="$(base64 < "$INNER" | tr -d '\n')"
CMD="echo ${B64} | base64 -d | bash"

echo "Sending bootstrap to $INSTANCE_ID in $REGION..."
CMD_ID="$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "Assure remote bootstrap" \
  --timeout-seconds 3600 \
  --parameters "commands=$CMD" \
  --query 'Command.CommandId' \
  --output text)"

echo "Command ID: $CMD_ID"
echo "Polling (Docker build may take 15-30 min)..."

for i in $(seq 1 120); do
  STATUS="$(aws ssm get-command-invocation \
    --region "$REGION" \
    --command-id "$CMD_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'Status' \
    --output text 2>/dev/null || echo Pending)"
  echo "  [$i] status=$STATUS"
  case "$STATUS" in
    Success|Failed|Cancelled|TimedOut)
      aws ssm get-command-invocation \
        --region "$REGION" \
        --command-id "$CMD_ID" \
        --instance-id "$INSTANCE_ID" \
        --query '{Status:Status,Stdout:StandardOutputContent,Stderr:StandardErrorContent}' \
        --output text
      exit 0
      ;;
  esac
  sleep 30
done

echo "Still running after 60 min — check AWS Console → Systems Manager → Run Command" >&2
