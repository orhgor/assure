#!/usr/bin/env bash
# Send EC2 pull-only redeploy over SSM and wait for completion.
# Mac: needs gh/AWS creds. GitHub Actions: AWS_* + GITHUB_TOKEN secrets.
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
  INSTANCE_ID="${1:-${ASSURE_INSTANCE_ID:-$INSTANCE_ID}}"
  REGION="${AWS_REGION:-$REGION}"
fi

resolve_github_token() {
  if [[ -n "${DEPLOY_GITHUB_TOKEN:-}" ]]; then printf '%s' "$DEPLOY_GITHUB_TOKEN"; return 0; fi
  if [[ -n "${GITHUB_TOKEN:-}" ]]; then printf '%s' "$GITHUB_TOKEN"; return 0; fi
  if [[ -n "${GH_TOKEN:-}" ]]; then printf '%s' "$GH_TOKEN"; return 0; fi
  if command -v gh >/dev/null 2>&1; then gh auth token 2>/dev/null; return 0; fi
  return 1
}

TOKEN="$(resolve_github_token || true)"
if [[ -z "$TOKEN" ]]; then
  echo "Need DEPLOY_GITHUB_TOKEN, GITHUB_TOKEN, GH_TOKEN, or \`gh auth login\`." >&2
  exit 1
fi

GHCR_TOKEN="${GHCR_DEPLOY_TOKEN:-${GHCR_TOKEN:-$TOKEN}}"
GHCR_USER="${GHCR_USER:-orhgor}"
ASSURE_IMAGE_TAG="${ASSURE_IMAGE_TAG:-}"
ASSURE_ENVIRONMENT="${ASSURE_ENVIRONMENT:-}"
REDEPLOY_ARGS=""
if [[ -n "$ASSURE_IMAGE_TAG" ]]; then
  REDEPLOY_ARGS="--tag ${ASSURE_IMAGE_TAG}"
fi
if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  source "$ENV_FILE"
  GHCR_TOKEN="${GHCR_DEPLOY_TOKEN:-${GHCR_TOKEN:-$TOKEN}}"
  GHCR_USER="${GHCR_USER:-orhgor}"
fi

BODY="$(mktemp)"
trap 'rm -f "$BODY"' EXIT

cat > "$BODY" <<SCRIPT
#!/usr/bin/env bash
set -euo pipefail
export GIT_TERMINAL_PROMPT=0
export GHCR_TOKEN='${GHCR_TOKEN}'
export GHCR_USER='${GHCR_USER}'
sudo -u ubuntu git config --global --add safe.directory /home/ubuntu/assure 2>/dev/null || true
cd /home/ubuntu/assure
sudo -u ubuntu git remote set-url origin "https://x-access-token:${TOKEN}@github.com/orhgor/assure.git"
sudo -u ubuntu git config remote.origin.fetch '+refs/heads/*:refs/remotes/origin/*'
sudo -u ubuntu git fetch origin ${GIT_BRANCH}
sudo -u ubuntu git checkout -B ${GIT_BRANCH} origin/${GIT_BRANCH}
sudo -u ubuntu git log -1 --oneline
sudo -u ubuntu env GHCR_TOKEN='${GHCR_TOKEN}' GHCR_USER='${GHCR_USER}' ASSURE_IMAGE_TAG='${ASSURE_IMAGE_TAG}' ASSURE_ENVIRONMENT='${ASSURE_ENVIRONMENT}' ASSURE_DEPLOY_BRANCH='${GIT_BRANCH}' ASSURE_SSM_BACKGROUND=1 ASSURE_DEPLOY_PULL_ONLY=1 ASSURE_HEALTH_URL='${ASSURE_HEALTH_URL:-}' bash scripts/aws/redeploy-app.sh ${REDEPLOY_ARGS}
echo ---HEALTH---
# Postflight gate. The workflow treats this script's SSM status as "deploy
# done", so success must mean the app on this box is serving. A stale
# container left over from an earlier compose project answers on the same
# port, so a bare 200 is not enough: when the payload carries a build_sha,
# require it to be the revision just checked out. ABSENT build_sha (staging
# today) can only be gated on 200 — reported, not hidden.
HEALTH_URL="${ASSURE_HEALTH_URL:-http://127.0.0.1:8765/health}"
DEPLOY_LOG="${ASSURE_DEPLOY_LOG:-/var/log/assure-deploy.log}"
DEPLOYED_SHA="\$(sudo -u ubuntu git -C /home/ubuntu/assure rev-parse --short HEAD 2>/dev/null || true)"
health_ok=0
served_sha=""
for i in \$(seq 1 20); do
  if curl -sf "\$HEALTH_URL" >/tmp/assure-ssm-health.json 2>/dev/null && [[ -s /tmp/assure-ssm-health.json ]]; then
    served_sha="\$(sed -n 's/.*"build_sha"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' /tmp/assure-ssm-health.json | head -1)"
    if [[ -z "\$served_sha" || -z "\${DEPLOYED_SHA}" || "\$served_sha" == "\${DEPLOYED_SHA}"* ]]; then
      health_ok=1
      break
    fi
  fi
  sleep 3
done
cat /tmp/assure-ssm-health.json 2>/dev/null || true
if [[ "\$health_ok" != "1" ]]; then
  echo "HEALTH_TIMEOUT: \${HEALTH_URL} served no 200 for this deploy within 60s"
  if [[ -n "\$served_sha" ]]; then
    echo "  served build_sha=\$served_sha expected=\${DEPLOYED_SHA}"
  fi
  tail -n 20 "\$DEPLOY_LOG" 2>/dev/null || true
  exit 1
fi
if [[ -n "\$served_sha" ]]; then
  echo "health: ok build_sha=\$served_sha"
else
  echo "health: ok (payload has no build_sha — served revision unverified)"
fi
SCRIPT

B64="$(base64 < "$BODY" | tr -d '\n')"

echo "Sending GHCR pull redeploy to $INSTANCE_ID ($REGION), branch $GIT_BRANCH..."
CMD_ID="$(aws ssm send-command \
  --region "$REGION" \
  --instance-ids "$INSTANCE_ID" \
  --document-name AWS-RunShellScript \
  --comment "Assure GHCR redeploy (${GIT_BRANCH})" \
  --timeout-seconds 900 \
  --parameters "commands=[\"echo ${B64} | base64 -d | bash\"]" \
  --query 'Command.CommandId' \
  --output text)"

echo "Command ID: $CMD_ID"
echo "Polling..."

POLL_SEC="${ASSURE_SSM_POLL_SEC:-10}"
POLL_MAX="${ASSURE_SSM_POLL_MAX:-24}"
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
    Success|Failed|Cancelled|TimedOut) break ;;
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
import sys
p = json.load(open("/tmp/assure-ssm-redeploy.json"))
print("=== SSM status ===", p.get("Status"))
out = p.get("StandardOutputContent") or ""
err = p.get("StandardErrorContent") or ""
for line in out.splitlines():
    if any(k in line for k in ("HEAD:", "Pull", "Pulling", "Started", "OK:", "FAIL:", "Done.", "build_sha", "css_version")):
        print(line)
health_block = ""
if "---HEALTH---" in out:
    health_block = out.split("---HEALTH---", 1)[1].strip()
    print("=== Loopback health ===")
    print(health_block[:2000])
if err.strip():
    print("=== stderr (tail) ===")
    print("\n".join(err.splitlines()[-15:]))
if p.get("Status") != "Success":
    raise SystemExit(1)
# Terminal postflight assertion: SSM Success only means the remote script
# exited 0, which now requires the on-box gate to have printed its pass line.
# Declare success here only when that line is actually present.
if "health: ok" not in health_block:
    print("=== Postflight ===")
    print("no 'health: ok' from the box — deploy unverified", file=sys.stderr)
    raise SystemExit(1)
PY

if [[ "${SSM_STRICT_HEALTH:-}" == "1" ]]; then
  curl -sf "https://getassureai.com/health" >/dev/null || exit 1
fi

PUBLIC_HEALTH_URL="${ASSURE_PUBLIC_HEALTH_URL:-https://staging.getassureai.com/health}"
if [[ "$GIT_BRANCH" == "main" ]]; then
  PUBLIC_HEALTH_URL="${ASSURE_PUBLIC_HEALTH_URL:-https://getassureai.com/health}"
fi
if curl -sf "$PUBLIC_HEALTH_URL" | python3 -c "
import json, sys
d = json.load(sys.stdin)
ui = d.get('ui') or {}
print('=== Public health ===')
print('url:', sys.argv[1] if len(sys.argv) > 1 else '')
print('build_sha:', d.get('build_sha'))
print('css_version:', ui.get('css_version'))
print('js_version:', ui.get('js_version'))
print('status:', d.get('status'))
" "$PUBLIC_HEALTH_URL" 2>/dev/null; then
  echo "Hard refresh ${PUBLIC_HEALTH_URL%/health} (Cmd+Shift+R)."
else
  echo "Public /health not reachable yet (tunnel may be warming up): ${PUBLIC_HEALTH_URL}" >&2
fi
