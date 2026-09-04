# Fix Cloudflare Workers Builds on app branches (Option B)

PR #1 shows a red **Workers Builds: assure** check while GitHub Actions **CI** is green. The app branch (`p4-account-wallet`) is not the marketing deploy branch; Cloudflare was building from repo root where there is no `wrangler.jsonc` (it lives under `landing/` in this tree, and at repo root on `webpage` only).

## Branch roles

| Branch | Purpose | Public URL |
|--------|---------|------------|
| `p4-account-wallet` | EC2 JDF Workstation (Docker on AWS) | [getassureai.com](https://getassureai.com) **(canonical public URL)** |
| `webpage` | Cloudflare Worker `assure` — workers.dev redirect only | [assure.orhangorenn.workers.dev](https://assure.orhangorenn.workers.dev) → apex |

Workers Builds for Worker **`assure`** must watch **`webpage` only**. Failures on `p4-account-wallet` do not affect the live app.

## Option A — API script (recommended)

Requires a **user-scoped** Cloudflare API token with:

- **Workers Builds Configuration** → Edit
- **Workers Scripts** → Read

Create at [Cloudflare API tokens](https://dash.cloudflare.com/profile/api-tokens).

```bash
chmod +x scripts/cloudflare/set_workers_branch.sh

export CLOUDFLARE_API_TOKEN="your-token"
export CLOUDFLARE_ACCOUNT_ID="381b292d419f2efdc1c85a3636268e91"

# Dry run is not supported; script PATCHes triggers directly.
bash scripts/cloudflare/set_workers_branch.sh
```

Expected success output:

```text
✅ Production branch set to webpage
✅ Preview builds disabled for app branches (PR checks on p4-account-wallet should stop)
```

If the script fails, it prints errors and points here.

### Verify after API run

```bash
# List triggers (optional; requires jq)
curl -s "https://api.cloudflare.com/client/v4/accounts/${CLOUDFLARE_ACCOUNT_ID}/builds/workers/52b3678b8ecc46bfb00ee1ce846d9c3f/triggers" \
  -H "Authorization: Bearer ${CLOUDFLARE_API_TOKEN}" \
  | python3 -m json.tool
```

Production trigger should show `"branch_includes": ["webpage"]` and `"deploy_command": "npx wrangler deploy"`.

Re-check PR checks:

```bash
gh pr checks 1
```

**Workers Builds: assure** should disappear on the next push to `p4-account-wallet`, or after re-running checks once Cloudflare stops listening to that branch.

## Option B — Manual dashboard (fallback)

Open the Worker production settings:

**https://dash.cloudflare.com/381b292d419f2efdc1c85a3636268e91/workers/services/view/assure/production**

1. Go to **Settings** → **Build** → **Branch control** (or **Settings** → **Git**, depending on UI version).
2. Change **Production branch** from `p4-account-wallet` to **`webpage`**.
3. **Uncheck** “Builds for non-production branches” (stops preview builds on PR branches like `p4-account-wallet`).
4. Confirm **Root directory** is empty (`.` — on `webpage`, `wrangler.jsonc` is at repo root).
5. Confirm **Deploy command** is `npx wrangler deploy` (not `npx wrangler versions upload` alone from repo root).
6. Click **Save**.

### Marketing deploy flow (unchanged)

```bash
./scripts/sync-webpage.sh /path/to/webpage-checkout
# commit + push webpage branch only
```

Cloudflare Workers Builds deploys automatically on push to `webpage`.

## What not to change

- Do **not** delete or move `landing/` or `landing/wrangler.jsonc` in the app branch.
- Do **not** point Workers Builds at `landing/` on `p4-account-wallet` unless you intentionally want monorepo deploys from the app branch (Option A from the earlier discussion — not recommended).

## Related

- Worker tag for `assure`: `52b3678b8ecc46bfb00ee1ce846d9c3f`
- Builds API: [Workers Builds API reference](https://developers.cloudflare.com/workers/ci-cd/builds/api-reference/)
- EC2 app deploy: `bash scripts/aws/redeploy-via-ssm.sh`
