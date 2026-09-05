#!/usr/bin/env bash
# Create Cloudflare named tunnel assure-prod and write .env.production.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

TUNNEL_NAME="${TUNNEL_NAME:-assure-prod}"
APP_HOST="${APP_HOST:-getassureai.com}"
APP_ORIGIN="${APP_ORIGIN:-http://localhost:8765}"
ENV_FILE="${ENV_FILE:-$ROOT/.env.production}"
CREDS_DIR="$ROOT/scripts/aws/credentials"
CONFIG_TEMPLATE="$ROOT/scripts/aws/cloudflared-config.yml"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "cloudflared not found. Install it, then run: cloudflared tunnel login" >&2
  exit 1
fi

if ! cloudflared tunnel list >/dev/null 2>&1; then
  echo "Cloudflare not authenticated. Run: cloudflared tunnel login" >&2
  exit 1
fi

mkdir -p "$CREDS_DIR"

if cloudflared tunnel list 2>/dev/null | awk '{print $1}' | grep -qx "$TUNNEL_NAME"; then
  echo "Tunnel '$TUNNEL_NAME' already exists."
else
  echo "Creating tunnel '$TUNNEL_NAME'..."
  cloudflared tunnel create "$TUNNEL_NAME"
fi

TUNNEL_ID="$(cloudflared tunnel list 2>/dev/null | awk -v n="$TUNNEL_NAME" '$0 ~ n {print $1; exit}')"
if [ -z "$TUNNEL_ID" ]; then
  echo "Could not resolve tunnel ID for '$TUNNEL_NAME'." >&2
  exit 1
fi

echo "Tunnel ID: $TUNNEL_ID"

SRC_CREDS="$HOME/.cloudflared/${TUNNEL_ID}.json"
DEST_CREDS="$CREDS_DIR/${TUNNEL_ID}.json"
if [ -f "$SRC_CREDS" ]; then
  cp "$SRC_CREDS" "$DEST_CREDS"
  chmod 600 "$DEST_CREDS"
  echo "Copied credentials to $DEST_CREDS"
else
  echo "Warning: credentials file not found at $SRC_CREDS" >&2
fi

echo "Routing DNS: $APP_HOST -> $TUNNEL_NAME"
cloudflared tunnel route dns "$TUNNEL_NAME" "$APP_HOST" || true

cat > "$CONFIG_TEMPLATE" <<EOF
# Installed on EC2 at /etc/cloudflared/config.yml
tunnel: ${TUNNEL_ID}
credentials-file: /etc/cloudflared/credentials.json

ingress:
  - hostname: ${APP_HOST}
    service: ${APP_ORIGIN}
  - service: http_status:404
EOF

TOKEN="$(cloudflared tunnel token "$TUNNEL_NAME")"

touch "$ENV_FILE"
if grep -q '^CLOUDFLARE_TUNNEL_TOKEN=' "$ENV_FILE" 2>/dev/null; then
  if [[ "$(uname -s)" == "Darwin" ]]; then
    sed -i '' "s|^CLOUDFLARE_TUNNEL_TOKEN=.*|CLOUDFLARE_TUNNEL_TOKEN=${TOKEN}|" "$ENV_FILE"
  else
    sed -i "s|^CLOUDFLARE_TUNNEL_TOKEN=.*|CLOUDFLARE_TUNNEL_TOKEN=${TOKEN}|" "$ENV_FILE"
  fi
else
  echo "CLOUDFLARE_TUNNEL_TOKEN=${TOKEN}" >> "$ENV_FILE"
fi

if ! grep -q '^CLOUDFLARE_TUNNEL_ID=' "$ENV_FILE" 2>/dev/null; then
  echo "CLOUDFLARE_TUNNEL_ID=${TUNNEL_ID}" >> "$ENV_FILE"
else
  if [[ "$(uname -s)" == "Darwin" ]]; then
    sed -i '' "s|^CLOUDFLARE_TUNNEL_ID=.*|CLOUDFLARE_TUNNEL_ID=${TUNNEL_ID}|" "$ENV_FILE"
  else
    sed -i "s|^CLOUDFLARE_TUNNEL_ID=.*|CLOUDFLARE_TUNNEL_ID=${TUNNEL_ID}|" "$ENV_FILE"
  fi
fi
if ! grep -q '^TUNNEL_ID=' "$ENV_FILE" 2>/dev/null; then
  echo "TUNNEL_ID=${TUNNEL_ID}" >> "$ENV_FILE"
fi
if ! grep -q '^APP_HOST=' "$ENV_FILE" 2>/dev/null; then
  echo "APP_HOST=${APP_HOST}" >> "$ENV_FILE"
fi

chmod 600 "$ENV_FILE"
bash "$ROOT/scripts/aws/generate-cloud-init.sh"
echo ""
echo "Wrote CLOUDFLARE_TUNNEL_TOKEN and CLOUDFLARE_TUNNEL_ID to $ENV_FILE (gitignored)."
echo "Generated injected cloud_init.sh via scripts/aws/generate-cloud-init.sh"
echo "Ingress template: $CONFIG_TEMPLATE"
echo ""
echo "Next: bash scripts/aws/provision-ec2.sh"
