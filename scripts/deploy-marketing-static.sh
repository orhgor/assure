#!/usr/bin/env bash
# Deploy dist/ to Cloudflare Pages (marketing only; /app stays on EC2).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist}"
PROJECT_NAME="${CF_PAGES_PROJECT:-assure-marketing}"

if [ ! -d "$DIST_DIR" ] || [ ! -f "$DIST_DIR/index.html" ]; then
  echo "Run scripts/build-marketing-static.sh first." >&2
  exit 1
fi

if ! command -v npx >/dev/null 2>&1; then
  echo "npx required for wrangler pages deploy." >&2
  exit 1
fi

echo "→ Deploying $DIST_DIR to Cloudflare Pages project: $PROJECT_NAME"
npx wrangler@4 pages deploy "$DIST_DIR" --project-name "$PROJECT_NAME" --commit-dirty=true

echo "✅ Pages deploy submitted. Configure apex DNS + /app /api redirects in Cloudflare dashboard."
