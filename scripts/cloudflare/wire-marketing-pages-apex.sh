#!/usr/bin/env bash
# Point getassureai.com + www at Cloudflare Pages (assure-marketing).
# Workbench stays on app.getassureai.com via Tunnel → EC2.
set -euo pipefail

ACCOUNT_ID="${CLOUDFLARE_ACCOUNT_ID:-381b292d419f2efdc1c85a3636268e91}"
PROJECT="${CF_PAGES_PROJECT:-assure-marketing}"
ZONE_NAME="${CLOUDFLARE_ZONE:-getassureai.com}"

python3 <<PY
import json, re, sys, urllib.error, urllib.request
from pathlib import Path

cfg = Path.home() / "Library/Preferences/.wrangler/config/default.toml"
text = cfg.read_text() if cfg.exists() else ""
m = re.search(r'oauth_token\s*=\s*"([^"]+)"', text) or re.search(r'api_token\s*=\s*"([^"]+)"', text)
if not m:
    sys.exit("Run: npx wrangler login")
token = m.group(1)
acct = "${ACCOUNT_ID}"
project = "${PROJECT}"
zone_name = "${ZONE_NAME}"

def api(method, url, data=None):
    body = None if data is None else json.dumps(data).encode()
    req = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        err = e.read().decode("utf-8", errors="replace")
        print(f"HTTP {e.code} {method} {url}\n{err}", file=sys.stderr)
        raise

zones = api("GET", f"https://api.cloudflare.com/client/v4/zones?name={zone_name}")
zone_id = (zones.get("result") or [{}])[0].get("id")
if not zone_id:
    sys.exit(f"Zone not found: {zone_name}")
print(f"Zone: {zone_name} ({zone_id})")

for host in (zone_name, f"www.{zone_name}"):
    try:
        resp = api(
            "POST",
            f"https://api.cloudflare.com/client/v4/accounts/{acct}/pages/projects/{project}/domains",
            {"name": host},
        )
        if resp.get("success"):
            print(f"✅ Pages domain added: {host}")
        else:
            print(f"⚠️  Pages domain {host}: {resp.get('errors')}")
    except urllib.error.HTTPError as e:
        if e.code == 409:
            print(f"ℹ️  Pages domain already exists: {host}")
        else:
            raise

domains = api(
    "GET",
    f"https://api.cloudflare.com/client/v4/accounts/{acct}/pages/projects/{project}/domains",
)
for d in domains.get("result") or []:
    print(f"   {d.get('name')}: status={d.get('status')} validation={d.get('validation_data')}")

PY

echo "Pages custom domains requested for ${PROJECT}."
