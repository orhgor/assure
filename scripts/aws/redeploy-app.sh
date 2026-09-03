#!/usr/bin/env bash
# Safe production redeploy: sync git, pull GHCR image, restart (no on-box build).
# Does NOT remove volumes (data/history.sqlite stays intact).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
COMPOSE_GHCR=(docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ghcr.yml)
BRANCH="${ASSURE_DEPLOY_BRANCH:-p4-account-wallet}"
IMAGE_REPO="${ASSURE_IMAGE_REPO:-ghcr.io/orhgor/assure-app}"

echo "==> Git sync (${BRANCH})"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull origin "$BRANCH"
FULL_SHA="$(git rev-parse HEAD)"
SHORT_SHA="$(git rev-parse --short HEAD)"
echo "    HEAD: ${SHORT_SHA} $(git log -1 --oneline)"

export ASSURE_BUILD_SHA="$SHORT_SHA"
export ASSURE_IMAGE_TAG="$FULL_SHA"
export ASSURE_IMAGE="${IMAGE_REPO}:${FULL_SHA}"

echo "==> GHCR login"
# shellcheck disable=SC1091
source "$ROOT/scripts/aws/ghcr-login.sh"

echo "==> Stop assure-app (keep volumes)"
"${COMPOSE[@]}" stop assure-app || true

echo "==> Pull ${ASSURE_IMAGE}"
if docker pull "$ASSURE_IMAGE"; then
  echo "==> Start assure-app (pull-only, no build)"
  "${COMPOSE_GHCR[@]}" up -d --no-build --pull never assure-app
else
  echo "WARN: GHCR pull failed (add GHCR_TOKEN with read:packages to .env.production)."
  if [[ "${ASSURE_DEPLOY_PULL_ONLY:-}" == "1" ]]; then
    echo "ASSURE_DEPLOY_PULL_ONLY=1 — aborting." >&2
    exit 1
  fi
  echo "==> Fallback: build on EC2 (slow)"
  DOCKER_BUILDKIT=1 "${COMPOSE[@]}" build --build-arg "ASSURE_BUILD_SHA=${FULL_SHA}" assure-app
  "${COMPOSE[@]}" up -d assure-app
fi

echo "==> Wait for health"
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:8765/health" >/tmp/assure-health.json 2>/dev/null; then
    break
  fi
  sleep 2
done

echo "==> Health / UI manifest"
if [[ -f /tmp/assure-health.json ]]; then
  python3 -c "
import json
data = json.load(open('/tmp/assure-health.json'))
ui = data.get('ui') or {}
print('status:', data.get('status'))
print('build_sha:', data.get('build_sha', '(not set)'))
print('css_version:', ui.get('css_version'))
print('js_version:', ui.get('js_version'))
print('jdf_workbench:', ui.get('jdf_workbench'))
"
else
  echo "health check failed — container may still be starting"
fi

echo "==> Template check (workspace-shell in container)"
if "${COMPOSE[@]}" exec -T assure-app grep -q 'workspace-shell' /app/prompt_matrix/templates/index.html; then
  echo "    OK: JDF workspace-shell present in index.html"
else
  echo "    FAIL: workspace-shell missing — wrong image or old checkout"
  exit 1
fi

echo ""
echo "Done. Image: ${ASSURE_IMAGE}"
