#!/usr/bin/env bash
# Point Cloudflare Workers Builds for Worker "assure" at the webpage branch (Option B).
#
# Uses the Workers Builds API (triggers), not wrangler.jsonc:
#   GET  /accounts/{account_id}/workers/scripts
#   GET  /accounts/{account_id}/builds/workers/{worker_tag}/triggers
#   PATCH /accounts/{account_id}/builds/triggers/{trigger_uuid}
#
# Token: user-scoped API token with "Workers Builds Configuration" (Edit) and
#        "Workers Scripts" (Read). Create at https://dash.cloudflare.com/profile/api-tokens
#
# Usage:
#   export CLOUDFLARE_API_TOKEN=...
#   export CLOUDFLARE_ACCOUNT_ID=381b292d419f2efdc1c85a3636268e91   # optional default
#   bash scripts/cloudflare/set_workers_branch.sh
#
# Optional env:
#   ASSURE_WORKER_NAME=assure
#   ASSURE_WORKER_TAG=52b3678b8ecc46bfb00ee1ce846d9c3f   # skip name lookup
#   ASSURE_PRODUCTION_BRANCH=webpage
#   ASSURE_DISABLE_PREVIEW_BUILDS=1   # stop PR checks on app branches (default 1)
#   ASSURE_APP_BRANCH_EXCLUDES=p4-account-wallet,main
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
MANUAL_GUIDE="$ROOT/docs/cloudflare-fix.md"
API_BASE="https://api.cloudflare.com/client/v4"

WORKER_NAME="${ASSURE_WORKER_NAME:-assure}"
WORKER_TAG="${ASSURE_WORKER_TAG:-}"
PRODUCTION_BRANCH="${ASSURE_PRODUCTION_BRANCH:-webpage}"
DEPLOY_COMMAND="${ASSURE_DEPLOY_COMMAND:-npx wrangler deploy}"
ROOT_DIRECTORY="${ASSURE_ROOT_DIRECTORY:-/}"
DISABLE_PREVIEW="${ASSURE_DISABLE_PREVIEW_BUILDS:-1}"
APP_BRANCH_EXCLUDES="${ASSURE_APP_BRANCH_EXCLUDES:-p4-account-wallet,main}"

prompt_secret() {
  local var_name="$1"
  local prompt_text="$2"
  if [[ -z "${!var_name:-}" ]]; then
    read -r -s -p "$prompt_text" input
    echo
    printf -v "$var_name" '%s' "$input"
  fi
}

prompt_plain() {
  local var_name="$1"
  local prompt_text="$2"
  if [[ -z "${!var_name:-}" ]]; then
    read -r -p "$prompt_text" input
    printf -v "$var_name" '%s' "$input"
  fi
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

print_manual_fallback() {
  echo
  echo "Manual fallback: $MANUAL_GUIDE"
  echo "Dashboard: https://dash.cloudflare.com/381b292d419f2efdc1c85a3636268e91/workers/services/view/assure/production"
  echo
  echo "Quick steps:"
  echo "  1. Settings → Build → Branch control"
  echo "  2. Production branch → webpage"
  echo "  3. Uncheck “Builds for non-production branches” (stops PR checks on app branches)"
  echo "  4. Deploy command → npx wrangler deploy"
  echo "  5. Root directory → . (empty)"
}

cf_api() {
  local method="$1"
  local path="$2"
  local data="${3:-}"
  local tmp
  tmp="$(mktemp)"
  local http_code
  if [[ -n "$data" ]]; then
    http_code="$(curl -sS -o "$tmp" -w '%{http_code}' \
      -X "$method" \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      -H "Content-Type: application/json" \
      --data "$data" \
      "${API_BASE}${path}")"
  else
    http_code="$(curl -sS -o "$tmp" -w '%{http_code}' \
      -X "$method" \
      -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
      "${API_BASE}${path}")"
  fi
  CF_LAST_HTTP_CODE="$http_code"
  CF_LAST_BODY="$(cat "$tmp")"
  rm -f "$tmp"
}

api_success() {
  python3 - <<'PY' "$CF_LAST_BODY"
import json, sys
body = json.loads(sys.argv[1])
sys.exit(0 if body.get("success") else 1)
PY
}

api_error_messages() {
  python3 - <<'PY' "$CF_LAST_BODY"
import json, sys
body = json.loads(sys.argv[1])
for err in body.get("errors") or []:
    print(f"  - [{err.get('code')}] {err.get('message')}")
PY
}

resolve_worker_tag() {
  if [[ -n "$WORKER_TAG" ]]; then
    echo "$WORKER_TAG"
    return 0
  fi
  cf_api GET "/accounts/${CLOUDFLARE_ACCOUNT_ID}/workers/scripts"
  if ! api_success; then
    echo "Failed to list Workers (HTTP ${CF_LAST_HTTP_CODE}):" >&2
    api_error_messages >&2
    return 1
  fi
  CF_LAST_BODY="$CF_LAST_BODY" WORKER_NAME="$WORKER_NAME" python3 <<'PY'
import json, os
name = os.environ["WORKER_NAME"]
data = json.loads(os.environ["CF_LAST_BODY"])
for item in data.get("result") or []:
    if item.get("id") == name:
        print(item.get("tag") or "")
        break
PY
}

list_triggers_json() {
  cf_api GET "/accounts/${CLOUDFLARE_ACCOUNT_ID}/builds/workers/${WORKER_TAG}/triggers"
  if ! api_success; then
    echo "Failed to list triggers (HTTP ${CF_LAST_HTTP_CODE}):" >&2
    api_error_messages >&2
    return 1
  fi
  printf '%s' "$CF_LAST_BODY"
}

patch_trigger() {
  local trigger_uuid="$1"
  local payload="$2"
  cf_api PATCH "/accounts/${CLOUDFLARE_ACCOUNT_ID}/builds/triggers/${trigger_uuid}" "$payload"
  if ! api_success; then
    echo "Failed to PATCH trigger ${trigger_uuid} (HTTP ${CF_LAST_HTTP_CODE}):" >&2
    api_error_messages >&2
    return 1
  fi
}

main() {
  require_cmd curl
  require_cmd python3

  prompt_secret CLOUDFLARE_API_TOKEN "CLOUDFLARE_API_TOKEN: "
  CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-381b292d419f2efdc1c85a3636268e91}"
  prompt_plain CLOUDFLARE_ACCOUNT_ID "CLOUDFLARE_ACCOUNT_ID [${CLOUDFLARE_ACCOUNT_ID}]: "
  CLOUDFLARE_ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-381b292d419f2efdc1c85a3636268e91}"

  echo "Resolving Worker tag for '${WORKER_NAME}'..."
  WORKER_TAG="$(resolve_worker_tag || true)"
  if [[ -z "$WORKER_TAG" ]]; then
    echo "Could not resolve Worker tag for '${WORKER_NAME}'." >&2
    print_manual_fallback
    exit 1
  fi
  echo "Worker tag: ${WORKER_TAG}"

  echo "Fetching build triggers..."
  triggers_json="$(list_triggers_json)" || {
    print_manual_fallback
    exit 1
  }

  trigger_tmp="$(mktemp)"
  TRIGGERS_JSON="$triggers_json" PRODUCTION_BRANCH="$PRODUCTION_BRANCH" python3 <<'PY' >"$trigger_tmp"
import json, os
data = json.loads(os.environ["TRIGGERS_JSON"])
triggers = data.get("result") or []
prod = os.environ["PRODUCTION_BRANCH"]

def kind(t):
    includes = t.get("branch_includes") or []
    name = (t.get("trigger_name") or "").lower()
    if "*" in includes:
        return "preview"
    if "production" in name or "prod" in name:
        return "production"
    if len(includes) == 1 and includes[0] not in ("*", prod):
        return "production"
    if prod in includes:
        return "production"
    return "unknown"

for t in triggers:
    uuid = t.get("trigger_uuid") or ""
    includes = ",".join(t.get("branch_includes") or [])
    excludes = ",".join(t.get("branch_excludes") or [])
    print(f"{uuid}\t{kind(t)}\t{includes}\t{excludes}\t{t.get('trigger_name') or ''}")
PY

  if [[ ! -s "$trigger_tmp" ]]; then
    rm -f "$trigger_tmp"
    echo "No build triggers found for Worker '${WORKER_NAME}'." >&2
    print_manual_fallback
    exit 1
  fi

  production_uuid=""
  preview_uuid=""
  first_uuid=""
  while IFS=$'\t' read -r uuid kind _includes _excludes _name; do
    [[ -z "$first_uuid" ]] && first_uuid="$uuid"
    if [[ "$kind" == "production" && -z "$production_uuid" ]]; then
      production_uuid="$uuid"
    elif [[ "$kind" == "preview" && -z "$preview_uuid" ]]; then
      preview_uuid="$uuid"
    elif [[ "$kind" != "preview" && -z "$production_uuid" ]]; then
      production_uuid="$uuid"
    fi
  done <"$trigger_tmp"
  rm -f "$trigger_tmp"

  production_uuid="${production_uuid:-$first_uuid}"

  echo "Updating production trigger ${production_uuid} → branch '${PRODUCTION_BRANCH}'..."
  prod_payload="$(PRODUCTION_BRANCH="$PRODUCTION_BRANCH" DEPLOY_COMMAND="$DEPLOY_COMMAND" ROOT_DIRECTORY="$ROOT_DIRECTORY" python3 <<'PY'
import json, os
print(json.dumps({
    "branch_includes": [os.environ["PRODUCTION_BRANCH"]],
    "branch_excludes": [],
    "deploy_command": os.environ["DEPLOY_COMMAND"],
    "root_directory": os.environ["ROOT_DIRECTORY"],
    "build_command": "",
}))
PY
)"
  if ! patch_trigger "$production_uuid" "$prod_payload"; then
    print_manual_fallback
    exit 1
  fi

  if [[ "$DISABLE_PREVIEW" == "1" && -n "$preview_uuid" ]]; then
    echo "Disabling preview / non-production builds (trigger ${preview_uuid})..."
    preview_payload="$(APP_BRANCH_EXCLUDES="$APP_BRANCH_EXCLUDES" PRODUCTION_BRANCH="$PRODUCTION_BRANCH" python3 <<'PY'
import json, os
excludes = [b.strip() for b in os.environ["APP_BRANCH_EXCLUDES"].split(",") if b.strip()]
excludes.append(os.environ["PRODUCTION_BRANCH"])
# Exclude every branch so app PRs stop triggering Workers Builds checks.
excludes.append("*")
print(json.dumps({
    "branch_includes": ["*"],
    "branch_excludes": sorted(set(excludes)),
}))
PY
)"
    if ! patch_trigger "$preview_uuid" "$preview_payload"; then
      echo "Warning: production branch updated, but preview trigger could not be disabled." >&2
      echo "Uncheck “Builds for non-production branches” in the dashboard." >&2
    fi
  elif [[ "$DISABLE_PREVIEW" == "1" && -z "$preview_uuid" ]]; then
    echo "No preview trigger found (non-production builds may already be off)."
  fi

  echo
  echo "✅ Production branch set to ${PRODUCTION_BRANCH}"
  echo "   Deploy command: ${DEPLOY_COMMAND}"
  echo "   Root directory: ${ROOT_DIRECTORY}"
  if [[ "$DISABLE_PREVIEW" == "1" ]]; then
    echo "✅ Preview builds disabled for app branches (PR checks on p4-account-wallet should stop)"
  fi
  echo
  echo "Verify: push to p4-account-wallet should no longer fail Workers Builds: assure."
  echo "Marketing deploys: push to webpage (or ./scripts/sync-webpage.sh then push webpage)."
}

main "$@"
