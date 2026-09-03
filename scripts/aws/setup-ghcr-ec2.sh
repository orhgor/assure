#!/usr/bin/env bash
# Write GHCR_TOKEN to EC2 .env.production for fast docker pull (no on-box build).
#
# Prerequisites: gh auth with read:packages, OR export GHCR_TOKEN=ghp_...
#
# Usage:
#   gh auth refresh -h github.com -s read:packages   # one-time browser step
#   bash scripts/aws/setup-ghcr-ec2.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$ROOT/.env.production"
INSTANCE_ID="${ASSURE_INSTANCE_ID:-i-09d0ad0b561113abe}"
REGION="${AWS_REGION:-us-east-1}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
fi

TOKEN="${GHCR_TOKEN:-}"
if [[ -z "$TOKEN" ]]; then
  TOKEN="$(gh auth token 2>/dev/null || true)"
fi
if [[ -z "$TOKEN" ]]; then
  echo "Set GHCR_TOKEN or run: gh auth refresh -h github.com -s read:packages" >&2
  exit 1
fi

USER_NAME="${GHCR_USER:-orhgor}"

# Also store in GitHub Actions for deploy job (optional, not printed).
if command -v gh >/dev/null 2>&1; then
  gh secret set GHCR_DEPLOY_TOKEN --body "$TOKEN" 2>/dev/null || true
fi

B64_TOKEN="$(printf '%s' "$TOKEN" | base64 | tr -d '\n')"
B64_USER="$(printf '%s' "$USER_NAME" | base64 | tr -d '\n')"

echo "Updating GHCR credentials on EC2 ($INSTANCE_ID)..."

CMD_ID="$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "Assure setup GHCR token on EC2" \
  --parameters "commands=[\"TOKEN=\\\$(echo ${B64_TOKEN} | base64 -d); USER=\\\$(echo ${B64_USER} | base64 -d); ENV=/home/ubuntu/assure/.env.production; touch \\\$ENV; grep -q '^GHCR_TOKEN=' \\\$ENV && sed -i 's|^GHCR_TOKEN=.*|GHCR_TOKEN='\\\"\\\$TOKEN\\\"'|' \\\$ENV || echo GHCR_TOKEN=\\\"\\\$TOKEN\\\" >> \\\$ENV; grep -q '^GHCR_USER=' \\\$ENV && sed -i 's|^GHCR_USER=.*|GHCR_USER='\\\"\\\$USER\\\"'|' \\\$ENV || echo GHCR_USER=\\\"\\\$USER\\\" >> \\\$ENV; chown ubuntu:ubuntu \\\$ENV; chmod 600 \\\$ENV; echo OK: GHCR lines updated\"]" \
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
echo "Done. Test fast deploy: bash scripts/aws/redeploy-via-ssm.sh"
