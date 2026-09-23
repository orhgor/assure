#!/usr/bin/env bash
# C2: run a Red-Hat audit on an anchored paragraph of a scratch project and show
# that the persisted finding now carries node_id.
set -uo pipefail
BASE=https://app.getassureai.com
KEY="${SHELL_KEY:?}"
FIXTURE=docs/demo/insurance-boston-real-estate/assets/naic-underwriting-policy-redacted.md
INTENT="Summarize the coverage limits."

RAND="$(openssl rand -hex 3)"
PID="$(curl -s -X POST "$BASE/api/projects" -H "Authorization: Bearer $KEY" \
        -H 'Content-Type: application/json' -d "{\"title\":\"phase-c-nodeid-$RAND\"}" | jq -r '.id')"
echo "PROJECT=$PID"

SID="$(curl -s -X POST "$BASE/api/projects/$PID/substrate/upload" -H "Authorization: Bearer $KEY" \
        -F "file=@$FIXTURE;type=text/markdown" | jq -r '.id')"
echo "SOURCE=$SID"

curl -s -N -X POST "$BASE/api/projects/$PID/draft/stream" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  -d "{\"intent\":\"$INTENT\",\"substrate_file_ids\":[\"$SID\"]}" > /tmp/c_compile.sse
echo "compiled frame present: $(grep -c '^event: compiled' /tmp/c_compile.sse)"

# The compiled frame carries the tree and the draft text the audit continues from.
grep '^event: compiled' -A1 /tmp/c_compile.sse | tail -1 | sed 's/^data: //' > /tmp/c_compiled.json
jq -r '.draft_text' /tmp/c_compiled.json > /tmp/c_draft.txt
jq -c '{document}' /tmp/c_compiled.json > /tmp/c_doc.json

echo "--- paragraphs and their anchor state ---"
jq -r '[.document.body[]? | .. | objects | select(.type? == "paragraph")]
       | .[] | "\(.id)\tanchored=\((.provenance // []) | map(select((.extracted_quote // "") != "")) | length)"' \
   /tmp/c_doc.json | head -12

TARGET="$(jq -r '[.document.body[]? | .. | objects | select(.type? == "paragraph")
        | select(((.provenance // []) | map(select((.extracted_quote // "") != "")) | length) > 0)]
        | .[0].id' /tmp/c_doc.json)"
echo "TARGET_NODE=$TARGET"

jq -n --rawfile dt /tmp/c_draft.txt --slurpfile doc /tmp/c_doc.json \
      --arg t "$TARGET" \
      '{draft_text: $dt, document: $doc[0].document, target_node_id: $t}' > /tmp/c_redhat_req.json

echo "--- redhat stream ---"
curl -s -N -X POST "$BASE/api/projects/$PID/draft/redhat/stream" \
  -H "Authorization: Bearer $KEY" -H 'Content-Type: application/json' \
  -H 'Accept: text/event-stream' \
  --data-binary @/tmp/c_redhat_req.json > /tmp/c_redhat.sse
grep '^event:' /tmp/c_redhat.sse | sort | uniq -c

echo "--- finding as persisted in the JDF (the locator's input) ---"
curl -s "$BASE/api/projects/$PID/jdf" -H "Authorization: Bearer $KEY" \
  | jq -c --arg t "$TARGET" '
      [.document.body[]? | .. | objects | select(.id? == $t)]
      | .[0].annotations.redhat
      | .[] | {keys: (keys), id, node_id, status, text: (.text[0:80])}'

echo "--- cleanup ---"
curl -s -X DELETE "$BASE/api/projects/$PID" -H "Authorization: Bearer $KEY"; echo
echo "PROJECT_DELETED=$PID"
