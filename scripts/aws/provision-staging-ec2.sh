#!/usr/bin/env bash
# Launch staging t4g.small with assure-staging tunnel and staging compose.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

export INSTANCE_NAME="${INSTANCE_NAME:-assure-staging}"
export ENV_FILE="${ENV_FILE:-$ROOT/.env.staging}"
export ENV_TARGET="${ENV_TARGET:-.env.staging}"
export COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml -f docker-compose.staging.yml}"
export TUNNEL_NAME="${TUNNEL_NAME:-assure-staging}"
export APP_HOST="${APP_HOST:-staging.getassureai.com}"
export ASSURE_GIT_REF="${ASSURE_GIT_REF:-staging}"
export GIT_BRANCH="${GIT_BRANCH:-staging}"
export CLOUD_INIT_OUTPUT="${CLOUD_INIT_OUTPUT:-$ROOT/scripts/aws/cloud_init.staging.sh}"

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing $ENV_FILE — copy from .env.production and set staging tunnel vars." >&2
  echo "Or run: TUNNEL_NAME=assure-staging APP_HOST=staging.getassureai.com ENV_FILE=$ENV_FILE bash scripts/aws/setup-tunnel.sh" >&2
  exit 1
fi

exec bash "$ROOT/scripts/aws/provision-ec2.sh"
