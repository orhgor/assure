#!/usr/bin/env bash
# Poll staging /health until ok. Optionally wait until build_sha matches
# STAGING_EXPECT_SHA so Playwright or a deploy check does not race a simultaneous GHCR/SSM deploy.
set -euo pipefail

BASE="${ASSURE_BASE_URL:-https://staging.getassureai.com}"
BASE="${BASE%/}"
URL="${BASE}/health"
TIMEOUT="${STAGING_READY_TIMEOUT_SEC:-480}"
EXPECT_SHA="${STAGING_EXPECT_SHA:-}"
SLEEP="${STAGING_READY_POLL_SEC:-5}"

echo "Waiting for ${URL} (timeout ${TIMEOUT}s, expect_sha=${EXPECT_SHA:-none})"
start=$(date +%s)

while true; do
  now=$(date +%s)
  elapsed=$((now - start))
  if (( elapsed >= TIMEOUT )); then
    echo "Staging not ready after ${TIMEOUT}s" >&2
    curl -sS -m 10 "$URL" >&2 || true
    exit 1
  fi
  body="$(curl -fsS -m 15 "$URL" || true)"
  if [[ -n "$body" ]] && echo "$body" | grep -q '"ok": *true'; then
    sha="$(printf '%s' "$body" | python3 -c "import sys,json; print(json.load(sys.stdin).get('build_sha') or '')" 2>/dev/null || true)"
    if [[ -z "$EXPECT_SHA" ]]; then
      echo "Staging healthy build_sha=${sha:-unknown} after ${elapsed}s"
      exit 0
    fi
    if [[ "$sha" == "$EXPECT_SHA"* || "$EXPECT_SHA" == "$sha"* ]]; then
      echo "Staging healthy matching build_sha=${sha} after ${elapsed}s"
      exit 0
    fi
    echo "healthy but build_sha ${sha:-empty} != ${EXPECT_SHA} (${elapsed}s)"
  else
    echo "not healthy yet (${elapsed}s)"
  fi
  sleep "$SLEEP"
done
