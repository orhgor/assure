#!/usr/bin/env bash
# Restore the previously healthy GHCR image after a failed health check.
# Reads /tmp/assure-last-deploy.txt (PREVIOUS= / CURRENT=).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

STATE_FILE="${ASSURE_DEPLOY_STATE:-/tmp/assure-last-deploy.txt}"
HEALTH_URL="${ASSURE_HEALTH_URL:-http://127.0.0.1:8765/health}"
IMAGE_REPO="${ASSURE_IMAGE_REPO:-ghcr.io/orhgor/assure-app}"

valid_image_ref() {
  local ref="${1:-}"
  [[ -n "$ref" && "$ref" == "${IMAGE_REPO}:"* ]] || return 1
  local tag="${ref##*:}"
  [[ -n "$tag" && "$tag" != "assure-assure-app" && "$tag" != "unknown" ]] || return 1
}

fallback_image() {
  if [[ "${ASSURE_ENVIRONMENT:-}" == "staging" ]]; then
    echo "${IMAGE_REPO}:staging"
    return 0
  fi
  if valid_image_ref "${CURRENT:-}"; then
    echo "${CURRENT}"
    return 0
  fi
  return 1
}

COMPOSE_GHCR=(docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ghcr.yml)
if [[ "${ASSURE_ENVIRONMENT:-}" == "staging" ]]; then
  COMPOSE_GHCR=(docker compose -f docker-compose.yml -f docker-compose.staging.yml -f docker-compose.ghcr.yml)
fi

if [[ ! -f "$STATE_FILE" ]]; then
  echo "ERROR: no deploy state at ${STATE_FILE} — cannot roll back." >&2
  exit 1
fi

# shellcheck disable=SC1090
source "$STATE_FILE"

PREVIOUS="${PREVIOUS:-}"
if [[ -z "$PREVIOUS" || "$PREVIOUS" == "unknown" ]] || ! valid_image_ref "$PREVIOUS"; then
  echo "WARN: invalid PREVIOUS=${PREVIOUS:-empty} in ${STATE_FILE}; using fallback." >&2
  if ! PREVIOUS="$(fallback_image)"; then
    echo "ERROR: no valid rollback image (PREVIOUS or CURRENT)." >&2
    exit 1
  fi
fi

echo "==> Rollback to ${PREVIOUS}"
# shellcheck disable=SC1091
source "$ROOT/scripts/aws/ghcr-login.sh"

if ! docker image inspect "$PREVIOUS" >/dev/null 2>&1; then
  docker pull "$PREVIOUS"
fi

TAG="${PREVIOUS##*:}"
export ASSURE_IMAGE_TAG="$TAG"
export ASSURE_IMAGE="$PREVIOUS"
export ASSURE_SKIP_ROLLBACK=1

"${COMPOSE_GHCR[@]}" up -d --no-build --pull never --force-recreate assure-app

echo "==> Wait for health after rollback"
ok=0
for i in $(seq 1 45); do
  if curl -sf "$HEALTH_URL" >/tmp/assure-health.json 2>/dev/null && [[ -s /tmp/assure-health.json ]]; then
    ok=1
    break
  fi
  sleep 2
done

if [[ "$ok" -ne 1 ]]; then
  echo "ERROR: rollback health check failed on ${HEALTH_URL}" >&2
  exit 1
fi

python3 -c "
import json, sys
data = json.load(open('/tmp/assure-health.json'))
print('rollback status:', data.get('status'))
print('rollback build_sha:', data.get('build_sha', '(not set)'))
if not data.get('ok'):
    sys.exit(1)
"

# After a successful rollback, CURRENT is the restored image.
{
  echo "PREVIOUS=${CURRENT:-}"
  echo "CURRENT=${PREVIOUS}"
} > "$STATE_FILE"

echo "Rollback complete. Image: ${PREVIOUS} (logged in ${STATE_FILE})"
