#!/usr/bin/env bash
# Build the static marketing site and ship it as Worker static assets.
#
# The marketing Worker (assure-marketing-proxy) serves dist/ via the ASSETS
# binding: dist is uploaded with the deploy (no R2, no bucket sync). The
# Worker itself handles the redirects (/contact, /government, legacy /app,
# /api), the contact form, and the security headers.
#
# Usage:
#   bash marketing/deploy.sh                 # build + deploy
#   bash marketing/deploy.sh --skip-build    # reuse an existing dist/
#   bash marketing/deploy.sh --dry-run       # wrangler dry-run, no deploy
#
# Requires CLOUDFLARE_API_TOKEN in the env on CI (Workers Scripts Edit +
# Workers Routes Edit on both zones). Locally, `npx wrangler login` works too.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
CF_DIR="$ROOT/marketing/cloudflare"
SKIP_BUILD=0
DRY_RUN=0

while [[ $# -gt 0 ]]; do
  case "$1" in
    --skip-build) SKIP_BUILD=1 ;;
    --dry-run) DRY_RUN=1 ;;
    -h|--help)
      sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
  shift
done

cd "$ROOT"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  bash "$ROOT/marketing/build.sh"
fi
if [[ ! -d "$ROOT/dist" ]]; then
  echo "dist missing: $ROOT/dist (run marketing/build.sh first)" >&2
  exit 1
fi

if [[ ! -x "$CF_DIR/node_modules/.bin/wrangler" ]]; then
  echo "→ Installing wrangler (marketing/cloudflare)…"
  npm install --prefix "$CF_DIR" --no-fund --no-audit
fi
WRANGLER="$CF_DIR/node_modules/.bin/wrangler"

ARGS=(deploy --config "$CF_DIR/wrangler-marketing-proxy.jsonc")
if [[ "$DRY_RUN" -eq 1 ]]; then
  ARGS+=(--dry-run)
fi

echo "→ Deploy marketing Worker + static assets (dist/)…"
"$WRANGLER" "${ARGS[@]}"

echo "✅ Marketing site deployed (Worker static assets — no R2 in the path)."
