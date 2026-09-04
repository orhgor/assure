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
LOCK_FILE="${ASSURE_REDEPLOY_LOCK:-/tmp/assure-redeploy.lock}"
MIN_DISK_GB_FOR_BUILD="${ASSURE_MIN_DISK_GB_FOR_BUILD:-3}"
GHCR_PULL_RETRIES="${ASSURE_GHCR_PULL_RETRIES:-5}"
GHCR_PULL_WAIT_SEC="${ASSURE_GHCR_PULL_WAIT_SEC:-30}"

remove_stale_app_container() {
  echo "==> Remove stale assure-app containers (keep volumes)"
  "${COMPOSE[@]}" rm -f -s assure-app 2>/dev/null || true
  "${COMPOSE_GHCR[@]}" rm -f -s assure-app 2>/dev/null || true
  # Orphaned compose-prefixed names (e.g. 1e307ee48e8f_assure-assure-app-1) block recreate.
  while IFS= read -r cid; do
    [[ -n "$cid" ]] || continue
    docker rm -f "$cid" 2>/dev/null || true
  done < <(docker ps -aq --filter "name=assure-assure-app" 2>/dev/null || true)
}

pull_image_with_retry() {
  local image="$1"
  local attempt=1
  local wait_sec="$GHCR_PULL_WAIT_SEC"
  while [[ "$attempt" -le "$GHCR_PULL_RETRIES" ]]; do
    if docker pull "$image"; then
      return 0
    fi
    if [[ "$attempt" -ge "$GHCR_PULL_RETRIES" ]]; then
      return 1
    fi
    echo "WARN: GHCR pull attempt ${attempt}/${GHCR_PULL_RETRIES} failed; retry in ${wait_sec}s..."
    sleep "$wait_sec"
    wait_sec=$((wait_sec * 2))
    attempt=$((attempt + 1))
  done
  return 1
}

disk_free_gb() {
  df -BG / 2>/dev/null | awk 'NR==2 {gsub(/G/, "", $4); print $4}'
}

acquire_redeploy_lock() {
  exec 9>"$LOCK_FILE"
  if ! flock -n 9; then
    echo "ERROR: another redeploy holds ${LOCK_FILE} — aborting." >&2
    exit 1
  fi
}

echo "==> Git sync (${BRANCH})"
acquire_redeploy_lock
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
"${COMPOSE_GHCR[@]}" stop assure-app || true
remove_stale_app_container

echo "==> Pull ${ASSURE_IMAGE}"
if pull_image_with_retry "$ASSURE_IMAGE"; then
  echo "==> Start assure-app (pull-only, no build)"
  "${COMPOSE_GHCR[@]}" up -d --no-build --pull never --force-recreate assure-app
else
  echo "WARN: GHCR pull failed after ${GHCR_PULL_RETRIES} attempts (add GHCR_TOKEN with read:packages to .env.production)."
  if [[ "${ASSURE_DEPLOY_PULL_ONLY:-}" == "1" ]]; then
    echo "ASSURE_DEPLOY_PULL_ONLY=1 — aborting." >&2
    exit 1
  fi
  free_gb="$(disk_free_gb || echo 0)"
  if [[ "${free_gb:-0}" -lt "$MIN_DISK_GB_FOR_BUILD" ]]; then
    echo "ERROR: only ${free_gb}GB free on / — need >= ${MIN_DISK_GB_FOR_BUILD}GB for EC2 fallback build." >&2
    exit 1
  fi
  echo "==> Fallback: build on EC2 (slow, ARM64; ${free_gb}GB free)"
  export DOCKER_DEFAULT_PLATFORM=linux/arm64
  remove_stale_app_container
  DOCKER_BUILDKIT=1 "${COMPOSE[@]}" build --build-arg "ASSURE_BUILD_SHA=${FULL_SHA}" assure-app
  "${COMPOSE[@]}" up -d --force-recreate assure-app
fi

echo "==> Wait for health"
for i in $(seq 1 45); do
  if curl -sf "http://127.0.0.1:8765/health" >/tmp/assure-health.json 2>/dev/null \
    && [[ -s /tmp/assure-health.json ]]; then
    break
  fi
  sleep 2
done

echo "==> Health / UI manifest"
if [[ ! -s /tmp/assure-health.json ]]; then
  echo "ERROR: health check failed — container did not respond on :8765/health" >&2
  exit 1
fi
python3 -c "
import json, sys
data = json.load(open('/tmp/assure-health.json'))
ui = data.get('ui') or {}
print('status:', data.get('status'))
print('build_sha:', data.get('build_sha', '(not set)'))
print('css_version:', ui.get('css_version'))
print('js_version:', ui.get('js_version'))
print('jdf_workbench:', ui.get('jdf_workbench'))
if not data.get('ok'):
    print('health ok=false', file=sys.stderr)
    sys.exit(1)
" || {
  echo "ERROR: health JSON invalid or ok=false" >&2
  exit 1
}

echo "==> Template check (workspace-shell in container)"
if "${COMPOSE_GHCR[@]}" exec -T assure-app grep -q 'workspace-shell' /app/prompt_matrix/templates/index.html 2>/dev/null \
  || "${COMPOSE[@]}" exec -T assure-app grep -q 'workspace-shell' /app/prompt_matrix/templates/index.html 2>/dev/null; then
  echo "    OK: JDF workspace-shell present in index.html"
else
  echo "    FAIL: workspace-shell missing — wrong image or old checkout"
  exit 1
fi

echo ""
echo "Done. Image: ${ASSURE_IMAGE}"
