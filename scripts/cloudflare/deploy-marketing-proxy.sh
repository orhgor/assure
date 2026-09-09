#!/usr/bin/env bash
# Deploy marketing Worker (R2 static; /app and /api stay on EC2 Tunnel).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
CF_DIR="$ROOT/scripts/cloudflare"
cd "$CF_DIR"
if [[ ! -x "$CF_DIR/node_modules/.bin/wrangler" ]]; then
  npm install --prefix "$CF_DIR" --no-fund --no-audit
fi
"$CF_DIR/node_modules/.bin/wrangler" deploy --config wrangler-marketing-proxy.jsonc
echo "✅ Marketing proxy Worker deployed with routes on getassureai.com"
