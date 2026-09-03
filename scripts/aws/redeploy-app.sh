#!/usr/bin/env bash
# Safe production redeploy: pull latest code, rebuild assure-app without cache.
# Does NOT remove volumes (data/history.sqlite stays intact).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$ROOT"

COMPOSE=(docker compose -f docker-compose.yml -f docker-compose.prod.yml)
BRANCH="${ASSURE_DEPLOY_BRANCH:-p4-account-wallet}"

echo "==> Git pull (${BRANCH})"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull origin "$BRANCH"
HEAD_SHA="$(git rev-parse --short HEAD)"
echo "    HEAD: ${HEAD_SHA} $(git log -1 --oneline)"

echo "==> Stop assure-app (keep volumes)"
"${COMPOSE[@]}" stop assure-app || true

echo "==> Rebuild assure-app (--no-cache)"
export ASSURE_BUILD_SHA="$HEAD_SHA"
DOCKER_BUILDKIT=1 "${COMPOSE[@]}" build --no-cache --build-arg "ASSURE_BUILD_SHA=${HEAD_SHA}" assure-app

echo "==> Start assure-app"
"${COMPOSE[@]}" up -d assure-app

echo "==> Wait for health"
for i in $(seq 1 30); do
  if curl -sf "http://127.0.0.1:8765/health" >/tmp/assure-health.json 2>/dev/null; then
    break
  fi
  sleep 2
done

echo "==> Health / UI manifest"
if [[ -f /tmp/assure-health.json ]]; then
  python3 -c "
import json
data = json.load(open('/tmp/assure-health.json'))
ui = data.get('ui') or {}
print('status:', data.get('status'))
print('build_sha:', data.get('build_sha', '(not set)'))
print('css_version:', ui.get('css_version'))
print('js_version:', ui.get('js_version'))
print('jdf_workbench:', ui.get('jdf_workbench'))
if ui.get('css_version') not in ('assure-39', 'assure-40', 'assure-41'):
    print('WARNING: css_version may be stale (expected assure-39+)')
"
else
  echo "health check failed — container may still be starting"
fi

echo "==> Template check (workspace-shell in container)"
if "${COMPOSE[@]}" exec -T assure-app grep -q 'workspace-shell' /app/prompt_matrix/templates/index.html; then
  echo "    OK: JDF workspace-shell present in index.html"
else
  echo "    FAIL: workspace-shell missing — wrong image or old checkout"
  exit 1
fi

echo "==> pyperclip check"
if "${COMPOSE[@]}" exec -T assure-app grep -rq pyperclip /app/prompt_matrix --include='*.py' 2>/dev/null; then
  echo "    FAIL: pyperclip still referenced in Python"
  exit 1
else
  echo "    OK: no pyperclip in prompt_matrix Python"
fi

echo ""
echo "Done. Hard refresh https://app.getassureai.com (Cmd+Shift+R / Ctrl+Shift+R)."
echo "Verify: curl -s http://127.0.0.1:8765/health | python3 -m json.tool"
