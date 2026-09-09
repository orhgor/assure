#!/usr/bin/env bash
# Apply app-only tunnel config (marketing on Pages, not EC2).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INSTANCE_ID="${ASSURE_INSTANCE_ID:-i-09d0ad0b561113abe}"
REGION="${AWS_REGION:-us-east-1}"
CONFIG="$ROOT/scripts/aws/cloudflared-config-app-only.yml"
B64="$(base64 < "$CONFIG" | tr -d '\n')"

echo "Applying app-only tunnel config on EC2 ($INSTANCE_ID)..."

CMD_ID="$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "Assure app-only cloudflared (Pages serves apex)" \
  --parameters "commands=[\"echo ${B64} | base64 -d > /etc/cloudflared/config.yml\", \"systemctl restart cloudflared\", \"sleep 2\", \"systemctl is-active cloudflared\", \"echo OK: app-only cloudflared\"]" \
  --query 'Command.CommandId' \
  --output text)"

sleep 6
aws ssm get-command-invocation \
  --region "$REGION" \
  --command-id "$CMD_ID" \
  --instance-id "$INSTANCE_ID" \
  --query '[Status,StandardOutputContent,StandardErrorContent]' \
  --output text
