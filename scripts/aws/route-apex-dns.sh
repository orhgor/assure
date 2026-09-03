#!/usr/bin/env bash
# Point getassureai.com + www at the assure-prod tunnel (run on Mac with cloudflared login).
set -euo pipefail

TUNNEL_NAME="${TUNNEL_NAME:-assure-prod}"

if ! command -v cloudflared >/dev/null 2>&1; then
  echo "Install cloudflared, then: cloudflared tunnel login" >&2
  exit 1
fi

for host in getassureai.com www.getassureai.com; do
  echo "Routing $host -> tunnel $TUNNEL_NAME"
  cloudflared tunnel route dns "$TUNNEL_NAME" "$host" || true
done

echo "Done. Remove Worker custom domains for getassureai.com in Cloudflare if they still conflict."
