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

if command -v gh >/dev/null 2>&1; then
  gh secret set GHCR_DEPLOY_TOKEN --body "$TOKEN" 2>/dev/null || true
fi

B64_TOKEN="$(printf '%s' "$TOKEN" | base64 | tr -d '\n')"
B64_USER="$(printf '%s' "$USER_NAME" | base64 | tr -d '\n')"

echo "Updating GHCR credentials on EC2 ($INSTANCE_ID)..."

REMOTE_PY="$(cat <<PY
import base64
import pathlib
import re

token = base64.b64decode("${B64_TOKEN}").decode("utf-8")
user = base64.b64decode("${B64_USER}").decode("utf-8")
env_path = pathlib.Path("/home/ubuntu/assure/.env.production")
lines = env_path.read_text(encoding="utf-8").splitlines() if env_path.exists() else []

def upsert(key, value):
    global lines
    pattern = re.compile(rf"^{re.escape(key)}=")
    out = [ln for ln in lines if not pattern.match(ln)]
    out.append(f'{key}="{value}"')
    lines = out

upsert("GHCR_TOKEN", token)
upsert("GHCR_USER", user)
env_path.parent.mkdir(parents=True, exist_ok=True)
env_path.write_text("\\n".join(lines) + "\\n", encoding="utf-8")
env_path.chmod(0o600)
print("OK: GHCR lines updated")
PY
)"

REMOTE_B64="$(printf '%s' "$REMOTE_PY" | base64 | tr -d '\n')"

CMD_ID="$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "Assure setup GHCR token on EC2" \
  --parameters "commands=[\"echo ${REMOTE_B64} | base64 -d | python3 -\"]" \
  --query 'Command.CommandId' \
  --output text)"

sleep 5
OUT="$(aws ssm get-command-invocation \
  --region "$REGION" \
  --command-id "$CMD_ID" \
  --instance-id "$INSTANCE_ID" \
  --query '[Status,StandardOutputContent,StandardErrorContent]' \
  --output text)"
echo "$OUT"

if ! echo "$OUT" | grep -q "^Success"; then
  echo "SSM setup failed." >&2
  exit 1
fi

echo ""
echo "Done. Test fast deploy: bash scripts/aws/redeploy-via-ssm.sh"
