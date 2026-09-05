#!/usr/bin/env bash
# Run free-disk-cleanup on EC2 via SSM (from your Mac).
set -euo pipefail

INSTANCE_ID="${1:-${ASSURE_INSTANCE_ID:-i-09d0ad0b561113abe}}"
REGION="${AWS_REGION:-us-east-1}"

REMOTE='cd /home/ubuntu/assure && git pull origin p4-account-wallet 2>/dev/null || true && bash scripts/aws/free-disk-cleanup.sh && bash scripts/aws/install-disk-maintenance-cron.sh 2>/dev/null || true'

echo "Running free disk cleanup on $INSTANCE_ID ($REGION)..."
CMD_ID="$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "Assure free disk cleanup" \
  --timeout-seconds 600 \
  --parameters "commands=[$(printf '%s' "$REMOTE" | python3 -c 'import json,sys; print(json.dumps(sys.stdin.read()))')]" \
  --query 'Command.CommandId' \
  --output text)"

echo "Command ID: $CMD_ID"
for i in $(seq 1 30); do
  STATUS="$(aws ssm get-command-invocation \
    --region "$REGION" \
    --command-id "$CMD_ID" \
    --instance-id "$INSTANCE_ID" \
    --query Status \
    --output text 2>/dev/null || echo Pending)"
  echo "  [$i] $STATUS"
  if [[ "$STATUS" == "Success" || "$STATUS" == "Failed" ]]; then
    break
  fi
  sleep 5
done

aws ssm get-command-invocation \
  --region "$REGION" \
  --command-id "$CMD_ID" \
  --instance-id "$INSTANCE_ID" \
  --query '{Status:Status,Stdout:StandardOutputContent,Stderr:StandardErrorContent}' \
  --output json
