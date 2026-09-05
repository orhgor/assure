#!/usr/bin/env bash
# Run on EC2 via SSM — checks Plausible/Sentry keys and API reachability (no secret output).
set -euo pipefail
cd /home/ubuntu/assure
set -a
# shellcheck disable=SC1091
. /home/ubuntu/assure/.env.production
set +a

echo KEY_STATUS
[[ -n "${PLAUSIBLE_API_KEY:-}" ]] && echo PLAUSIBLE=set || echo PLAUSIBLE=missing
[[ -n "${SENTRY_DSN:-}" ]] && echo SENTRY_DSN=set || echo SENTRY_DSN=missing
[[ -n "${SENTRY_BROWSER_DSN:-}" ]] && echo SENTRY_BROWSER=set || echo SENTRY_BROWSER=missing
[[ -n "${SENTRY_AUTH_TOKEN:-}" ]] && echo SENTRY_AUTH=set || echo SENTRY_AUTH=missing

echo PLAUSIBLE_HTTP
code=$(curl -s -o /tmp/pl.json -w '%{http_code}' \
  -H "Authorization: Bearer ${PLAUSIBLE_API_KEY}" \
  -H 'Content-Type: application/json' \
  -d '{"site_id":"app.getassureai.com","metrics":["visitors"],"date_range":"7d"}' \
  https://plausible.io/api/v2/query)
echo "HTTP=${code}"
python3 - <<'PY' 2>/dev/null || head -c 150 /tmp/pl.json
import json
d = json.load(open("/tmp/pl.json"))
r = d.get("results") or []
print("visitors_7d", r[0]["metrics"][0] if r else d.get("error", d))
PY

echo SENTRY_HTTP
if [[ -n "${SENTRY_AUTH_TOKEN:-}" ]]; then
  code=$(curl -s -o /tmp/se.json -w '%{http_code}' \
    -H "Authorization: Bearer ${SENTRY_AUTH_TOKEN}" \
    'https://sentry.io/api/0/projects/assure-ai/javascript/issues/?query=is:unresolved&limit=3')
  echo "HTTP=${code}"
  python3 - <<'PY' 2>/dev/null || head -c 150 /tmp/se.json
import json
xs = json.load(open("/tmp/se.json"))
print("unresolved", len(xs) if isinstance(xs, list) else xs.get("detail", "err"))
PY
else
  echo SKIP_AUTH_TOKEN
fi

echo CONTAINER_ENV
docker exec assure-assure-app-1 sh -c 'for k in ENVIRONMENT SENTRY_DSN PLAUSIBLE_API_KEY; do v=$(printenv "$k" 2>/dev/null || true); if [ -n "$v" ]; then echo "${k}=set"; else echo "${k}=missing"; fi; done'

echo LIVE_HTML
curl -s http://127.0.0.1:8765/ | tr '\n' ' ' | grep -q pa-we0rKAtBU && echo PLAUSIBLE_HTML=yes || echo PLAUSIBLE_HTML=no
curl -s http://127.0.0.1:8765/ | tr '\n' ' ' | grep -q sentry.bundle.js && echo SENTRY_HTML=yes || echo SENTRY_HTML=no
