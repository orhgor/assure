#!/usr/bin/env bash
# Log Docker into ghcr.io using GHCR_TOKEN / GHCR_USER (from env or .env.production).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${ASSURE_ENV_FILE:-$ROOT/.env.production}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "$ENV_FILE"
  set +a
fi

USER_NAME="${GHCR_USER:-orhgor}"
TOKEN="${GHCR_TOKEN:-}"

if [[ -z "$TOKEN" ]]; then
  echo "GHCR_TOKEN not set — cannot pull private images from ghcr.io." >&2
  echo "Add GHCR_TOKEN (read:packages) to .env.production on EC2, or export before redeploy." >&2
  exit 1
fi

echo "$TOKEN" | docker login ghcr.io -u "$USER_NAME" --password-stdin
