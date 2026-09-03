#!/usr/bin/env bash
# Redeploy assure-app on EC2 via SSM (run from your Mac / CI with AWS CLI).
#
# Private repo: sets a temporary authenticated origin URL using GITHUB_TOKEN,
# GH_TOKEN, or `gh auth token`, then runs scripts/aws/redeploy-app.sh on the
# instance. Does NOT remove Docker volumes (history.sqlite stays intact).
#
# Usage:
#   bash scripts/aws/redeploy-via-ssm.sh [instance-id]
#
# Env:
#   AWS_REGION          default us-east-1
#   ASSURE_GIT_REF      branch (default p4-account-wallet; also read from .env.production)
#   GITHUB_TOKEN        preferred for private repo fetch
#   GH_TOKEN            alternate token env
#   ASSURE_INSTANCE_ID  default EC2 instance if arg omitted
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
ENV_FILE="$ROOT/.env.production"
INSTANCE_ID="${1:-${ASSURE_INSTANCE_ID:-i-09d0ad0b561113abe}}"
REGION="${AWS_REGION:-us-east-1}"
GIT_BRANCH="${ASSURE_GIT_REF:-p4-account-wallet}"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  GIT_BRANCH="${ASSURE_GIT_REF:-$GIT_BRANCH}"
fi

resolve_github_token() {
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then
    printf '%s' "$GITHUB_TOKEN"
    return 0
  fi
  if [[ -n "${GH_TOKEN:-}" ]]; then
    printf '%s' "$GH_TOKEN"
    return 0
  fi
  if command -v gh >/dev/null 2>&1; then
    gh auth token 2>/dev/null
    return 0
  fi
  return 1
}

TOKEN="$(resolve_github_token || true)"
if [[ -z "$TOKEN" ]]; then
  echo "Need GITHUB_TOKEN, GH_TOKEN, or \`gh auth login\` for private repo fetch." >&2
  exit 1
fi

INNER="$(mktemp)"
trap 'rm -f "$INNER"' EXIT

cat > "$INNER" <<SCRIPT
#!/usr/bin/env bash
set -euo pipefail
export GIT_TERMINAL_PROMPT=0
cd /home/ubuntu/assure
sudo -u ubuntu git remote set-url origin "https://x-access-token:${TOKEN}@github.com/orhgor/assure.git"
sudo -u ubuntu git fetch origin ${GIT_BRANCH}
sudo -u ubuntu git checkout ${GIT_BRANCH}
sudo -u ubuntu git reset --hard origin/${GIT_BRANCH}
sudo -u ubuntu git log -1 --oneline
sudo -u ubuntu bash scripts/aws/redeploy-app.sh
echo ---HEALTH---
curl -sf http://127.0.0.1:8765/health || true
SCRIPT

B64="$(base64 < "$INNER" | tr -d '\n')"

echo "Sending redeploy to $INSTANCE_ID ($REGION), branch $GIT_BRANCH..."
CMD_ID="$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "Assure redeploy via SSM (${GIT_BRANCH})" \
  --timeout-seconds 3600 \
  --parameters "commands=[\"echo ${B64} | base64 -d | bash\"]" \
  --query 'Command.CommandId' \
  --output text)"

echo "Command ID: $CMD_ID"
echo "Polling (Docker --no-cache build may take several minutes)..."

POLL_SEC="${ASSURE_SSM_POLL_SEC:-30}"
POLL_MAX="${ASSURE_SSM_POLL_MAX:-90}"
STATUS="Pending"

for i in $(seq 1 "$POLL_MAX"); do
  STATUS="$(aws ssm get-command-invocation \
    --region "$REGION" \
    --command-id "$CMD_ID" \
    --instance-id "$INSTANCE_ID" \
    --query 'Status' \
    --output text 2>/dev/null || echo Pending)"
  echo "  [$i/$POLL_MAX] status=$STATUS"
  case "$STATUS" in
    Success|Failed|Cancelled|TimedOut)
      break
      ;;
  esac
  sleep "$POLL_SEC"
done

INVOCATION="$(aws ssm get-command-invocation \
  --region "$REGION" \
  --command-id "$CMD_ID" \
  --instance-id "$INSTANCE_ID" \
  --output json)"

echo "$INVOCATION" > /tmp/assure-ssm-redeploy.json

python3 - <<'PY'
import json
p = json.load(open("/tmp/assure-ssm-redeploy.json"))
print("=== SSM status ===", p.get("Status"))
out = p.get("StandardOutputContent") or ""
err = p.get("StandardErrorContent") or ""
for line in out.splitlines():
    if any(k in line for k in (
        "HEAD:", "build_sha", "css_version", "js_version", "jdf_workbench",
        "OK:", "FAIL:", "Done.", "WARNING:", "status:",
    )):
        print(line)
if "---HEALTH---" in out:
    block = out.split("---HEALTH---", 1)[1].strip()
    print("=== Loopback health ===")
    print(block[:2000])
if err.strip():
    print("=== stderr (tail) ===")
    print("\n".join(err.splitlines()[-20:]))
if p.get("Status") != "Success":
    raise SystemExit(1)
PY

echo ""
echo "=== Public health (app.getassureai.com) ==="
if curl -sf "https://app.getassureai.com/health" | python3 -c "
import json, sys
d = json.load(sys.stdin)
ui = d.get('ui') or {}
print('build_sha:', d.get('build_sha'))
print('css_version:', ui.get('css_version'))
print('js_version:', ui.get('js_version'))
print('jdf_workbench:', ui.get('jdf_workbench'))
print('status:', d.get('status'))
"; then
  echo ""
  echo "Hard refresh https://app.getassureai.com (Cmd+Shift+R / Ctrl+Shift+R)."
else
  echo "Could not reach public /health (tunnel may still be warming up)." >&2
fi
