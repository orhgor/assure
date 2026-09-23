#!/usr/bin/env bash
# Print the output of an already-sent SSM command.
# usage: scripts/aws/_boxout.sh <command-id>
set -euo pipefail
ID="${1:?command id required}"
aws ssm get-command-invocation --command-id "$ID" \
  --instance-id "${ASSURE_INSTANCE_ID:-i-03e39eccc57572191}" \
  --region "${AWS_REGION:-us-east-1}" --output json | jq -r '.Status, .StandardOutputContent'
