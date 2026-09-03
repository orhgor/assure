#!/usr/bin/env bash
# Create R2 bucket (after R2 is enabled in dashboard), then install EC2 backup cron.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
BUCKET="${R2_BUCKET:-assure-prod-backups}"
ACCOUNT_ID="${R2_ACCOUNT_ID:-381b292d419f2efdc1c85a3636268e91}"

echo "Checking R2 access..."
if ! (cd "$ROOT/landing" && npx wrangler r2 bucket list >/dev/null 2>&1); then
  echo ""
  echo "R2 is not enabled on this Cloudflare account yet."
  echo "Enable it once (free tier includes 10 GB/month):"
  echo "  https://dash.cloudflare.com/${ACCOUNT_ID}/r2/overview"
  echo ""
  echo "Click through to enable R2, then re-run:"
  echo "  bash scripts/aws/setup-r2-backup-full.sh"
  exit 1
fi

if cd "$ROOT/landing" && npx wrangler r2 bucket list 2>/dev/null | grep -q "$BUCKET"; then
  echo "Bucket already exists: $BUCKET"
else
  echo "Creating bucket: $BUCKET"
  if ! (cd "$ROOT/landing" && npx wrangler r2 bucket create "$BUCKET" 2>&1); then
    if cd "$ROOT/landing" && npx wrangler r2 bucket list 2>/dev/null | grep -q "$BUCKET"; then
      echo "Bucket already exists: $BUCKET"
    else
      exit 1
    fi
  fi
fi

ENV_FILE="$ROOT/.env.production"
# shellcheck disable=SC1090
source "$ENV_FILE"

if [ -z "${AWS_ACCESS_KEY_ID:-}" ] || [ -z "${AWS_SECRET_ACCESS_KEY:-}" ]; then
  echo ""
  echo "Create an R2 API token (Object Read & Write, scoped to $BUCKET):"
  echo "  https://dash.cloudflare.com/${ACCOUNT_ID}/r2/overview → Manage R2 API Tokens"
  echo ""
  echo "Add to $ENV_FILE:"
  echo "  AWS_ACCESS_KEY_ID=<Access Key ID>"
  echo "  AWS_SECRET_ACCESS_KEY=<Secret Access Key>"
  echo ""
  echo "Then re-run: bash scripts/aws/setup-r2-backup-full.sh"
  exit 1
fi

bash "$ROOT/scripts/aws/setup-sqlite-backup.sh"
