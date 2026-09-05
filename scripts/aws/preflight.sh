#!/usr/bin/env bash
# Verify AWS + Cloudflare CLIs before provisioning.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

fail=0

echo "=== Assure AWS preflight ==="

if command -v aws >/dev/null 2>&1; then
  if aws sts get-caller-identity >/dev/null 2>&1; then
    echo "[OK] AWS authenticated:"
    aws sts get-caller-identity
  else
    echo "[FAIL] AWS CLI found but not authenticated."
    echo "       Run: aws configure"
    echo "       Provide Access Key, Secret Key, and default region (e.g. us-east-1)."
    fail=1
  fi
else
  echo "[FAIL] AWS CLI not installed."
  echo "       Install: https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html"
  fail=1
fi

if command -v cloudflared >/dev/null 2>&1; then
  if cloudflared tunnel list >/dev/null 2>&1; then
    echo "[OK] Cloudflare tunnel CLI authenticated."
    cloudflared tunnel list 2>/dev/null | head -20 || true
  else
    echo "[FAIL] cloudflared found but not authenticated."
    echo "       Run: cloudflared tunnel login"
    fail=1
  fi
else
  echo "[FAIL] cloudflared not installed."
  echo "       Install: https://developers.cloudflare.com/cloudflare-one/connections/connect-networks/downloads/"
  fail=1
fi

if command -v docker >/dev/null 2>&1; then
  echo "[OK] docker $(docker --version 2>/dev/null | awk '{print $3}' | tr -d ',')"
else
  echo "[WARN] docker not installed locally (EC2 bootstrap installs it)."
fi

if [ -f "$ROOT/.env.production" ]; then
  echo "[OK] .env.production exists"
else
  echo "[WARN] .env.production missing — run scripts/aws/setup-tunnel.sh first"
fi

if [ "$fail" -ne 0 ]; then
  echo ""
  echo "Preflight failed. Fix the items above before provisioning."
  exit 1
fi

echo ""
echo "Preflight passed."
