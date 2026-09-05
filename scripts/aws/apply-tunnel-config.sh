#!/usr/bin/env bash
# Push cloudflared ingress for apex + www (+ legacy app host) and restart on EC2.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
INSTANCE_ID="${ASSURE_INSTANCE_ID:-i-09d0ad0b561113abe}"
REGION="${AWS_REGION:-us-east-1}"
CONFIG="$ROOT/scripts/aws/cloudflared-config.yml"
B64="$(base64 < "$CONFIG" | tr -d '\n')"

echo "Applying tunnel config on EC2 ($INSTANCE_ID)..."

CMD_ID="$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "Assure apply apex cloudflared config" \
  --parameters "commands=[\"echo ${B64} | base64 -d > /etc/cloudflared/config.yml\", \"systemctl restart cloudflared\", \"sleep 2\", \"systemctl is-active cloudflared\", \"echo OK: cloudflared restarted\"]" \
  --query 'Command.CommandId' \
  --output text)"

sleep 5
aws ssm get-command-invocation \
  --region "$REGION" \
  --command-id "$CMD_ID" \
  --instance-id "$INSTANCE_ID" \
  --query '[Status,StandardOutputContent,StandardErrorContent]' \
  --output text

echo ""
echo "Ensure Cloudflare DNS routes apex/www to this tunnel (run locally if needed):"
echo "  cloudflared tunnel route dns assure-prod getassureai.com"
echo "  cloudflared tunnel route dns assure-prod www.getassureai.com"
