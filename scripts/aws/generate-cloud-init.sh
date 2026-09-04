#!/usr/bin/env bash
# Generate scripts/aws/cloud_init.sh with real tunnel + git values injected.
# Run after Phase 2 (setup-tunnel.sh) completes.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

ENV_FILE="${ENV_FILE:-$ROOT/.env.production}"
TEMPLATE="$ROOT/scripts/aws/cloud_init.template.sh"
OUTPUT="${OUTPUT:-$ROOT/scripts/aws/cloud_init.sh}"
CREDS_DIR="$ROOT/scripts/aws/credentials"

TUNNEL_NAME="${TUNNEL_NAME:-assure-prod}"
APP_HOST="${APP_HOST:-getassureai.com}"
GIT_CLONE_URL="${GIT_CLONE_URL:-https://github.com/orhgor/assure.git}"
GIT_BRANCH="${GIT_BRANCH:-${ASSURE_GIT_REF:-p4-account-wallet}}"
ENV_TARGET="${ENV_TARGET:-.env.production}"
COMPOSE_FILES="${COMPOSE_FILES:--f docker-compose.yml -f docker-compose.prod.yml}"

if [ -f "$ENV_FILE" ]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

TUNNEL_ID="${CLOUDFLARE_TUNNEL_ID:-${TUNNEL_ID:-}}"
if [ -z "$TUNNEL_ID" ] && command -v cloudflared >/dev/null 2>&1; then
  TUNNEL_ID="$(cloudflared tunnel list 2>/dev/null | awk -v n="$TUNNEL_NAME" '$0 ~ n {print $1; exit}')"
fi

if [ -z "$TUNNEL_ID" ]; then
  echo "TUNNEL_ID missing. Run scripts/aws/setup-tunnel.sh first." >&2
  exit 1
fi

CREDS_FILE="${CREDS_DIR}/${TUNNEL_ID}.json"
if [ ! -f "$CREDS_FILE" ]; then
  CREDS_FILE="$HOME/.cloudflared/${TUNNEL_ID}.json"
fi
if [ ! -f "$CREDS_FILE" ]; then
  echo "Credentials JSON not found for tunnel $TUNNEL_ID" >&2
  exit 1
fi

if [ ! -f "$TEMPLATE" ]; then
  echo "Missing template: $TEMPLATE" >&2
  exit 1
fi

python3 - "$TEMPLATE" "$OUTPUT" "$TUNNEL_ID" "$CREDS_FILE" "$GIT_CLONE_URL" "$GIT_BRANCH" "$APP_HOST" "$ENV_TARGET" "$COMPOSE_FILES" <<'PY'
import json, pathlib, sys
(
    template_path,
    output_path,
    tunnel_id,
    creds_path,
    git_url,
    git_branch,
    app_host,
    env_target,
    compose_files,
) = sys.argv[1:10]
creds = pathlib.Path(creds_path).read_text(encoding="utf-8").strip()
text = pathlib.Path(template_path).read_text(encoding="utf-8")
replacements = {
    "__TUNNEL_ID__": tunnel_id,
    "__TUNNEL_CREDENTIALS_JSON_CONTENT__": creds,
    "__GIT_CLONE_URL__": git_url,
    "__GIT_BRANCH__": git_branch,
    "__APP_HOST__": app_host,
    "__ENV_FILE__": env_target,
    "__COMPOSE_FILES__": compose_files,
}
for key, val in replacements.items():
    text = text.replace(key, val)
pathlib.Path(output_path).write_text(text, encoding="utf-8")
print(f"Wrote {output_path} (tunnel={tunnel_id}, git={git_branch}, env={env_target})")
PY

chmod +x "$OUTPUT"
