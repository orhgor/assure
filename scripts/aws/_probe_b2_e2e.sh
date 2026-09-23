#!/usr/bin/env bash
# B2b end-to-end: three compiles of the same intent over the same source, with
# the compile cache cleared between each, must persist identical documents.
set -uo pipefail
BASE=https://app.getassureai.com
KEY="${SHELL_KEY:?}"
FIXTURE=docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.md
INTENT="Summarize the coverage limits."
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

RAND="$(openssl rand -hex 3)"
NAME="phase-b-determinism-$RAND"
PID="$(curl -s -X POST "$BASE/api/projects" -H "Authorization: Bearer $KEY" \
        -H 'Content-Type: application/json' -d "{\"title\":\"$NAME\"}" | jq -r '.id')"
echo "PROJECT=$PID"

UP="$(curl -s -X POST "$BASE/api/projects/$PID/substrate/upload" -H "Authorization: Bearer $KEY" \
        -F "file=@$FIXTURE;type=text/markdown")"
SID="$(printf '%s' "$UP" | jq -r '.id')"
echo "SOURCE=$SID  instruction_like=$(printf '%s' "$UP" | jq -r '.instruction_like')"
echo "text_chars=$(printf '%s' "$UP" | jq -r '.text | length')"

hash_doc() {
  curl -s "$BASE/api/projects/$PID/jdf" -H "Authorization: Bearer $KEY" \
    | jq -c '[.document.body[]? | .. | objects | select(has("content")) | .content]' \
    | shasum -a 256 | cut -c1-24
}

for i in 1 2 3; do
  echo "--- run $i ---"
  bash "$ROOT/scripts/aws/_boxpy.sh" scripts/aws/_probe_cache_admin.py "$PID" clear 2>&1 \
    | grep -v "Provider List" | tail -3
  curl -s -N -X POST "$BASE/api/projects/$PID/draft/stream" \
    -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
    -H 'Accept: text/event-stream' \
    -d "{\"intent\":\"$INTENT\",\"substrate_file_ids\":[\"$SID\"]}" \
    > "/tmp/b2run$i.sse"
  echo "  frames: $(grep -c '^event:' /tmp/b2run$i.sse)  error=$(grep -c '^event: error' /tmp/b2run$i.sse)  compiled=$(grep -c '^event: compiled' /tmp/b2run$i.sse)"
  echo "  draft_text sha=$(grep '^event: compiled' -A1 /tmp/b2run$i.sse | tail -1 | sed 's/^data: //' | jq -r '.draft_text' | shasum -a 256 | cut -c1-24)"
  echo "  persisted doc sha=$(hash_doc)"
  echo "  revisions=$(curl -s "$BASE/api/projects/$PID/history" -H "Authorization: Bearer $KEY" | jq -r '.count')"
done

echo "--- cleanup ---"
curl -s -X DELETE "$BASE/api/projects/$PID" -H "Authorization: Bearer $KEY"
echo
echo "PROJECT_DELETED=$PID"
