#!/usr/bin/env bash
# Sync OpenUser UX test history into OMP semantic memory.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OMP_SERVER="${OMP_SERVER:-http://localhost:3456}"
API_KEY=""
if [[ -f "$HOME/.omp/api_key" ]]; then
  API_KEY="$(tr -d '[:space:]' < "$HOME/.omp/api_key")"
elif [[ -n "${OMP_API_KEY:-}" ]]; then
  API_KEY="$OMP_API_KEY"
fi

if [[ -z "$API_KEY" ]]; then
  echo "OMP sync skipped: no API key at ~/.omp/api_key or OMP_API_KEY" >&2
  exit 0
fi

HISTORY="$ROOT/openuser/results/history.jsonl"
if [[ ! -f "$HISTORY" ]]; then
  echo "OMP sync skipped: no history at $HISTORY" >&2
  exit 0
fi

tail -n 10 "$HISTORY" | while IFS= read -r test; do
  name="$(echo "$test" | jq -r '.name // "unknown"')"
  status="$(echo "$test" | jq -r '.status // "unknown"')"
  ts="$(echo "$test" | jq -r '.timestamp // ""')"
  duration="$(echo "$test" | jq -r '.duration_ms // 0')"
  error="$(echo "$test" | jq -r '.error // ""')"
  key="ux-test:${name}:latest"
  content="Status: ${status}, Time: ${ts}, DurationMs: ${duration}"
  if [[ -n "$error" && "$error" != "null" ]]; then
    content="${content}, Error: ${error}"
  fi
  tag_name="$(echo "$name" | tr '[:upper:]' '[:lower:]' | tr -cs 'a-z0-9_:-' '-' | sed 's/^-//;s/-$//')"
  payload="$(jq -n \
    --arg content "$content" \
    --arg key "$key" \
    --arg tag "$tag_name" \
    '{content: $content, type: "semantic", namespace: "project:prompt-matrix", tags: ["ux-test", $tag, $key], source: {tool: "openuser", timestamp: (now | todate)}}')"
  curl -sfS -X POST "${OMP_SERVER}/v1/memories" \
    -H "Authorization: Bearer ${API_KEY}" \
    -H "Content-Type: application/json" \
    -d "$payload" \
    || echo "OMP sync failed for ${name}" >&2
done

echo "OMP sync complete."
