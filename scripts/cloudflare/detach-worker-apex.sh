#!/usr/bin/env bash
# Remove Worker custom domains so apex/www can route to Cloudflare Tunnel (EC2).
# Requires wrangler OAuth login on this machine.
set -euo pipefail

ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-381b292d419f2efdc1c85a3636268e91}"
WORKER="${WORKER_NAME:-assure}"

python3 <<PY
import json, re, sys, urllib.request
from pathlib import Path

cfg = Path.home() / "Library/Preferences/.wrangler/config/default.toml"
text = cfg.read_text() if cfg.exists() else ""
m = re.search(r'oauth_token\\s*=\\s*"([^"]+)"', text) or re.search(r'api_token\\s*=\\s*"([^"]+)"', text)
if not m:
    sys.exit("Run: npx wrangler login")
token = m.group(1)
acct = "${ACCOUNT_ID}"
worker = "${WORKER}"

req = urllib.request.Request(
    f"https://api.cloudflare.com/client/v4/accounts/{acct}/workers/domains",
    headers={"Authorization": f"Bearer {token}"},
)
with urllib.request.urlopen(req) as r:
    domains = json.load(r).get("result") or []

for d in domains:
    if (d.get("service") or "") != worker:
        continue
    host = d.get("hostname") or ""
    if host not in ("getassureai.com", "www.getassureai.com"):
        continue
    did = d["id"]
    del_req = urllib.request.Request(
        f"https://api.cloudflare.com/client/v4/accounts/{acct}/workers/domains/{did}",
        method="DELETE",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(del_req) as r:
        r.read()
    print(f"Removed Worker domain: {host}")
PY

echo "Next: bash scripts/aws/route-apex-dns.sh"
