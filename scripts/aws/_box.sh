#!/usr/bin/env bash
# Run a shell snippet on the staging box over SSM and print its output.
# usage: scripts/aws/_box.sh '<snippet>'   (snippet runs as root in HOME=/root, cwd /home/ubuntu/assure-prototype)
set -euo pipefail
INSTANCE_ID="${ASSURE_INSTANCE_ID:-i-03e39eccc57572191}"
REGION="${AWS_REGION:-us-east-1}"
SNIPPET="${1:?snippet required}"

FULL="export HOME=/root
git config --global --add safe.directory '*' 2>/dev/null || true
cd /home/ubuntu/assure-prototype
${SNIPPET}"

PARAMS="$(jq -n --arg c "$FULL" '{commands: [$c]}')"
CMD_ID="$(aws ssm send-command --instance-ids "$INSTANCE_ID" --document-name AWS-RunShellScript \
  --parameters "$PARAMS" --region "$REGION" --output text --query 'Command.CommandId')"

for _ in $(seq 1 ${ASSURE_SSM_POLLS:-400}); do
  OUT="$(aws ssm get-command-invocation --command-id "$CMD_ID" --instance-id "$INSTANCE_ID" \
    --region "$REGION" --output json)"
  STATUS="$(printf '%s' "$OUT" | jq -r '.Status')"
  case "$STATUS" in
    Success|Failed|Cancelled|TimedOut)
      printf '%s' "$OUT" | jq -r '.StandardOutputContent'
      ERR="$(printf '%s' "$OUT" | jq -r '.StandardErrorContent')"
      if [[ -n "$ERR" ]]; then printf 'STDERR>>>\n%s\n' "$ERR" >&2; fi
      [[ "$STATUS" == "Success" ]] || { printf 'SSM status: %s\n' "$STATUS" >&2; exit 1; }
      exit 0 ;;
  esac
  sleep 3
done
echo "timed out waiting for $CMD_ID" >&2
exit 1
