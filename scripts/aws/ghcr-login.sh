#!/usr/bin/env bash
# Log Docker into ghcr.io using GHCR_TOKEN / GHCR_USER (from env or .env.production).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="${ASSURE_ENV_FILE:-$ROOT/.env.production}"

# Save the workflow-passed token BEFORE sourcing the file, so the file's
# stale GHCR_TOKEN cannot override it. The file is still sourced for other
# values (GHCR_USER, etc.), but GHCR_TOKEN from the workflow wins.
SAVED_WORKFLOW_TOKEN="${GHCR_TOKEN:-}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  source "$ENV_FILE"
  set +a
fi

USER_NAME="${GHCR_USER:-orhgor}"
# Workflow token wins; fall back to file/token-only if workflow didn't pass one.
TOKEN="${SAVED_WORKFLOW_TOKEN:-${GHCR_TOKEN:-}}"

if [[ -z "$TOKEN" ]]; then
  echo "GHCR_TOKEN not set — cannot pull private images from ghcr.io." >&2
  echo "Pass GHCR_TOKEN (read:packages) in the workflow, or add it to .env.production on EC2." >&2
  exit 1
fi

echo "$TOKEN" | docker login ghcr.io -u "$USER_NAME" --password-stdin
