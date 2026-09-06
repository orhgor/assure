#!/usr/bin/env bash
set -euo pipefail

BASE="${ASSURE_CANARY_URL:-https://getassureai.com}"

curl -sf "${BASE}/health" \
  -H "User-Agent: AssureSyntheticCanary/1.0" \
  | jq -e '.ok == true' >/dev/null

curl -sf -X POST "${BASE}/api/sandbox/verify" \
  -H "Content-Type: application/json" \
  -H "User-Agent: AssureSyntheticCanary/1.0" \
  -d '{"text":"Revenue reached $12M in Q3."}' \
  | jq -e '.ok == true' >/dev/null

echo "canary ok"
