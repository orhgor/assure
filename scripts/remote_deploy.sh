#!/usr/bin/env bash
# Immutable deploy: pull an exact image from GHCR, recreate the container, then
# verify the build identity the container reports matches what was deployed.
#
# Runs on the EC2 box (invoked over SSM). No build happens here — the image must
# already exist in GHCR. Fails closed on any identity mismatch.
set -euo pipefail

APP_DIR="${APP_DIR:-/home/ubuntu/assure}"
APP_PORT="${APP_PORT:-8765}"
APP_ENV="${APP_ENV:-staging}"
IMAGE_REPO="${ASSURE_IMAGE_REPO:-ghcr.io/orhgor/assure-app}"
HEALTH_URL="${ASSURE_HEALTH_URL:-http://127.0.0.1:${APP_PORT}/api/health}"
HEALTH_TIMEOUT_SEC="${ASSURE_HEALTH_TIMEOUT_SEC:-120}"

: "${EXPECTED_SHA:?missing EXPECTED_SHA}"
: "${EXPECTED_BRANCH:?missing EXPECTED_BRANCH}"
: "${GHCR_READ_USER:?missing GHCR_READ_USER}"
: "${GHCR_READ_TOKEN:?missing GHCR_READ_TOKEN}"

EXPECTED_TIME="${EXPECTED_TIME:-}"
APP_IMAGE="${APP_IMAGE:-${IMAGE_REPO}:${EXPECTED_SHA}}"
ASSURE_SERVICE_API_TOKEN="${ASSURE_SERVICE_API_TOKEN:-}"

# Compose overlay set must match how the box is already running (see
# scripts/aws/redeploy-app.sh): base + environment + GHCR pull-only overlay.
if [[ "$APP_ENV" == "staging" ]]; then
  COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.staging.yml -f docker-compose.ghcr.yml)
else
  COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml -f docker-compose.ghcr.yml)
fi

cd "$APP_DIR"

echo "[1/7] Authenticate with GHCR"
printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io -u "$GHCR_READ_USER" --password-stdin

echo "[2/7] Write persistent .env for reboot survival"
# The service token is a secret the deploy is not always given: it is not in
# the image, not in git, and only a workflow that has the repository secret can
# supply it. Writing a blank over an existing token would break the service API
# until someone re-set it by hand — and writing a placeholder would be worse,
# since the endpoint compares against it with hmac.compare_digest, so any caller
# presenting the placeholder is authorised. When this script is not given one,
# the value already on the box is kept.
if [ -z "$ASSURE_SERVICE_API_TOKEN" ] && [ -f .env ]; then
  ASSURE_SERVICE_API_TOKEN="$(sed -n 's/^ASSURE_SERVICE_API_TOKEN=//p' .env | head -1)"
  [ -n "$ASSURE_SERVICE_API_TOKEN" ] && echo "    service token: kept from existing .env"
fi
if [ -z "$ASSURE_SERVICE_API_TOKEN" ]; then
  echo "    service token: not set (service API endpoints will refuse every caller)"
fi

# The ghcr overlay reads ASSURE_IMAGE_TAG; the app reads ASSURE_BUILD_* and the
# service token. Write both names so compose and the container agree.
ASSURE_IMAGE_TAG="$EXPECTED_SHA"
cat > .env <<EOF
APP_IMAGE=$APP_IMAGE
ASSURE_IMAGE_TAG=$ASSURE_IMAGE_TAG
BUILD_SHA=$EXPECTED_SHA
BUILD_BRANCH=$EXPECTED_BRANCH
BUILD_TIME=$EXPECTED_TIME
ASSURE_BUILD_SHA=$EXPECTED_SHA
ASSURE_BUILD_BRANCH=$EXPECTED_BRANCH
ASSURE_BUILD_TIME=$EXPECTED_TIME
ASSURE_SERVICE_API_TOKEN=$ASSURE_SERVICE_API_TOKEN
EOF
export ASSURE_IMAGE_TAG
export ASSURE_BUILD_SHA="$EXPECTED_SHA"
export ASSURE_BUILD_BRANCH="$EXPECTED_BRANCH"
export ASSURE_BUILD_TIME="$EXPECTED_TIME"
export APP_IMAGE
export ASSURE_SERVICE_API_TOKEN

# This script is invoked as root over SSM, and it writes .env into a worktree
# owned by ubuntu. Unreconciled, every deploy leaves root-owned files behind;
# after enough of them the git user can no longer write to .git/objects and the
# next deploy dies with "insufficient permission for adding an object to
# repository database". That is how the staging box accumulated 90 root-owned
# files and most of its deploys failed. Reconcile where the writes happen.
chown -R ubuntu:ubuntu "$APP_DIR" 2>/dev/null || true

echo "[3/7] Pull exact image: $APP_IMAGE"
docker pull "$APP_IMAGE"

echo "[4/7] Recreate container from exact image"
"${COMPOSE[@]}" up -d --no-build --pull never --force-recreate --no-deps assure-app

echo "[5/7] Wait for health"
HEALTHY=0
for _ in $(seq 1 $((HEALTH_TIMEOUT_SEC / 2))); do
  if curl -sf --max-time 2 "$HEALTH_URL" >/dev/null 2>&1; then
    HEALTHY=1
    break
  fi
  sleep 2
done
if [ "$HEALTHY" != "1" ]; then
  echo "Health endpoint never became ready after ${HEALTH_TIMEOUT_SEC}s: $HEALTH_URL"
  "${COMPOSE[@]}" logs --tail 50 assure-app || true
  exit 1
fi

echo "[6/7] Verify deployed build identity"
ACTUAL_JSON="$(curl -sf --max-time 5 "$HEALTH_URL")"

json_field() {
  printf '%s' "$ACTUAL_JSON" | python3 -c "
import json, sys
try:
    print(json.load(sys.stdin).get('$1') or '')
except Exception:
    print('')
"
}

ACTUAL_SHA="$(json_field build_sha)"
ACTUAL_BRANCH="$(json_field build_branch)"
ACTUAL_IMAGE="$(json_field image_ref)"
ACTUAL_TIME="$(json_field build_time)"

fail=0
if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
  echo "SHA mismatch: expected=$EXPECTED_SHA actual=$ACTUAL_SHA"
  fail=1
fi
if [ -n "$EXPECTED_BRANCH" ] && [ "$ACTUAL_BRANCH" != "$EXPECTED_BRANCH" ]; then
  echo "Branch mismatch: expected=$EXPECTED_BRANCH actual=$ACTUAL_BRANCH"
  fail=1
fi
if [ "$ACTUAL_IMAGE" != "$APP_IMAGE" ]; then
  echo "Image mismatch: expected=$APP_IMAGE actual=$ACTUAL_IMAGE"
  fail=1
fi
# build_time is only checked when the caller knows it (an immutable image carries
# its own; a caller that can't read it passes empty and the check is skipped).
if [ -n "$EXPECTED_TIME" ] && [ "$ACTUAL_TIME" != "$EXPECTED_TIME" ]; then
  echo "Build time mismatch: expected=$EXPECTED_TIME actual=$ACTUAL_TIME"
  fail=1
fi
if [ "$fail" != "0" ]; then
  echo "Identity verification failed — deployment is NOT confirmed."
  exit 1
fi

echo "[7/7] Deployment verified: sha=$ACTUAL_SHA branch=$ACTUAL_BRANCH image=$ACTUAL_IMAGE"
docker image prune -f --filter "until=168h" >/dev/null 2>&1 || true
echo "Deployment verified and complete."
