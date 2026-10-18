#!/usr/bin/env bash
# Build static marketing site, upload changed files to R2, deploy Worker only if needed.
# Designed to stay within Cloudflare free tier (see docs/runbooks/marketing-r2-free-tier.md).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIST_DIR="${DIST_DIR:-$ROOT/dist}"
BUCKET="${MARKETING_R2_BUCKET:-assure-marketing-prod}"
CF_DIR="$ROOT/scripts/cloudflare"
CACHE_DIR="$ROOT/.cache"
LAST_DEPLOY="$CACHE_DIR/marketing-r2-last-upload"
MIN_INTERVAL="${MARKETING_DEPLOY_MIN_INTERVAL:-120}" # seconds between full uploads
PYTHON="${PYTHON:-$ROOT/.venv/bin/python}"
DRY_RUN=0
SKIP_BUILD=0
FORCE_WORKER=0
APPLY=1

usage() {
  cat <<'EOF'
Usage: scripts/deploy-marketing-r2.sh [options]

  --dry-run       Show uploads / worker actions only (no wrangler remote calls)
  --skip-build    Use existing dist/ (do not run build-marketing-static.sh)
  --worker        Force Worker redeploy even if sources unchanged
  --no-apply      Build + plan only; never upload or deploy Worker

Free tier: uploads skip unchanged files; Worker redeploys only when JS/config change.
Set MARKETING_DEPLOY_FORCE=1 to bypass the cooldown guard.
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
    --worker) FORCE_WORKER=1 ;;
    --no-apply) APPLY=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "Unknown option: $1" >&2; usage; exit 1 ;;
  esac
  shift
done

if [[ ! -x "$PYTHON" ]]; then
  PYTHON=python3
fi

cd "$ROOT"

if [[ "$SKIP_BUILD" -eq 0 ]]; then
  bash "$ROOT/scripts/build-marketing-static.sh"
fi

if [[ ! -d "$DIST_DIR" ]]; then
  echo "dist missing: $DIST_DIR (run build first)" >&2
  exit 1
fi

# Cooldown guard — avoids agent/CI hammering R2 Class A ops
if [[ "$APPLY" -eq 1 && "$DRY_RUN" -eq 0 && "${MARKETING_DEPLOY_FORCE:-}" != "1" && -f "$LAST_DEPLOY" ]]; then
  now=$(date +%s)
  last=$(cat "$LAST_DEPLOY" 2>/dev/null || echo 0)
  delta=$((now - last))
  if [[ "$delta" -lt "$MIN_INTERVAL" ]]; then
    echo "⏸  Last R2 upload ${delta}s ago (min interval ${MIN_INTERVAL}s)."
    echo "   Unchanged files are skipped; set MARKETING_DEPLOY_FORCE=1 to upload anyway."
    echo "   Or use --dry-run to preview."
    exit 0
  fi
fi

if [[ ! -x "$CF_DIR/node_modules/.bin/wrangler" ]]; then
  echo "→ Install wrangler (once, local only — no Cloudflare billing)…"
  npm install --prefix "$CF_DIR" --no-fund --no-audit
fi
WRANGLER="$CF_DIR/node_modules/.bin/wrangler"

if [[ "$APPLY" -eq 0 ]]; then
  echo "→ --no-apply: skipping R2 upload and Worker deploy"
  "$PYTHON" "$ROOT/scripts/r2_sync_marketing.py" \
    --dist "$DIST_DIR" --bucket "$BUCKET" --wrangler "$WRANGLER" --dry-run
  exit 0
fi

echo "→ Ensure R2 bucket: $BUCKET (idempotent; free tier)"
if [[ "$DRY_RUN" -eq 0 ]]; then
  "$WRANGLER" r2 bucket create "$BUCKET" 2>/dev/null || true
fi

SYNC_ARGS=(--dist "$DIST_DIR" --bucket "$BUCKET" --wrangler "$WRANGLER")
if [[ "$DRY_RUN" -eq 1 ]]; then
  SYNC_ARGS+=(--dry-run)
fi

echo "→ Sync dist/ → R2 ($BUCKET) — changed files only…"
"$PYTHON" "$ROOT/scripts/r2_sync_marketing.py" "${SYNC_ARGS[@]}"

DEPLOY_WORKER=0
if [[ "$FORCE_WORKER" -eq 1 ]]; then
  DEPLOY_WORKER=1
elif "$PYTHON" "$ROOT/scripts/marketing_worker_changed.py"; then
  DEPLOY_WORKER=1
fi

if [[ "$DEPLOY_WORKER" -eq 1 ]]; then
  if [[ "$DRY_RUN" -eq 1 ]]; then
    echo "→ would deploy marketing Worker (assure-marketing-proxy)"
  else
    echo "→ Deploy marketing Worker (sources changed)…"
    bash "$ROOT/scripts/cloudflare/deploy-marketing-proxy.sh"
    "$PYTHON" "$ROOT/scripts/marketing_worker_changed.py" --record
  fi
else
  echo "→ Skip Worker deploy (unchanged — saves deploy API calls)"
fi

if [[ "$DRY_RUN" -eq 0 ]]; then
  mkdir -p "$CACHE_DIR"
  date +%s > "$LAST_DEPLOY"
fi

echo "✅ Marketing on R2 ($BUCKET). Workbench unchanged on EC2 (/app via Tunnel)."
echo "   Free-tier notes: docs/runbooks/marketing-r2-free-tier.md"
