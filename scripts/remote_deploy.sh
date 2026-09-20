#!/usr/bin/env bash
set -euo pipefail

: "${APP_DIR:=/srv/assure}"
: "${APP_PORT:=8765}"
: "${APP_IMAGE:?missing APP_IMAGE}"
: "${EXPECTED_SHA:?missing EXPECTED_SHA}"
: "${EXPECTED_BRANCH:?missing EXPECTED_BRANCH}"
: "${EXPECTED_TIME:?missing EXPECTED_TIME}"
: "${ASSURE_SERVICE_API_TOKEN:?missing ASSURE_SERVICE_API_TOKEN}"
: "${GHCR_READ_USER:?missing GHCR_READ_USER}"
: "${GHCR_READ_TOKEN:?missing GHCR_READ_TOKEN}"

cd "$APP_DIR"

echo "[1/7] Authenticate with GHCR"
printf '%s' "$GHCR_READ_TOKEN" | docker login ghcr.io -u "$GHCR_READ_USER" --password-stdin

echo "[2/7] Write persistent .env for reboot survival"
cat > .env <<EOF
APP_IMAGE=$APP_IMAGE
BUILD_SHA=$EXPECTED_SHA
BUILD_BRANCH=$EXPECTED_BRANCH
BUILD_TIME=$EXPECTED_TIME
ASSURE_BUILD_SHA=$EXPECTED_SHA
ASSURE_BUILD_BRANCH=$EXPECTED_BRANCH
ASSURE_BUILD_TIME=$EXPECTED_TIME
ASSURE_SERVICE_API_TOKEN=$ASSURE_SERVICE_API_TOKEN
EOF

echo "[3/7] Pull exact image: $APP_IMAGE"
docker pull "$APP_IMAGE"

echo "[4/7] Recreate container from exact image"
docker compose up -d --force-recreate --no-deps

echo "[5/7] Wait for health"
for i in $(seq 1 60); do
  if python3 - <<'PY'
import json, os, sys, urllib.request
port = os.environ.get("APP_PORT", "8765")
url = f"http://127.0.0.1:{port}/api/health"
try:
    data = json.load(urllib.request.urlopen(url, timeout=2))
    print(json.dumps(data))
    sys.exit(0)
except Exception:
    sys.exit(1)
PY
  then
    break
  fi
  sleep 2
done

echo "[6/7] Verify deployed build identity"
ACTUAL_JSON=$(python3 - <<'PY'
import json, os, urllib.request
port = os.environ.get("APP_PORT", "8765")
url = f"http://127.0.0.1:{port}/api/health"
data = json.load(urllib.request.urlopen(url, timeout=5))
print(json.dumps(data))
PY
)

ACTUAL_SHA=$(echo "$ACTUAL_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("build_sha", ""))')

ACTUAL_BRANCH=$(echo "$ACTUAL_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("build_branch", ""))')

ACTUAL_IMAGE=$(echo "$ACTUAL_JSON" | python3 -c 'import sys, json; print(json.load(sys.stdin).get("image_ref", ""))')

if [ "$ACTUAL_SHA" != "$EXPECTED_SHA" ]; then
  echo "SHA mismatch: expected=$EXPECTED_SHA actual=$ACTUAL_SHA"
  exit 1
fi

if [ "$ACTUAL_BRANCH" != "$EXPECTED_BRANCH" ]; then
  echo "Branch mismatch: expected=$EXPECTED_BRANCH actual=$ACTUAL_BRANCH"
  exit 1
fi

if [ "$ACTUAL_IMAGE" != "$APP_IMAGE" ]; then
  echo "Image mismatch: expected=$APP_IMAGE actual=$ACTUAL_IMAGE"
  exit 1
fi

echo "[7/7] Deployment verified"

echo "[8/8] Pruning unused images older than 7 days"
docker image prune -af --filter "until=168h" || true

echo "Deployment verified and complete."