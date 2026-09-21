# Marketing on R2 — stay within Cloudflare free tier

Marketing deploy scripts are tuned to avoid unnecessary Class A/B operations, Worker redeploys, and zone API calls.

## Free tier budgets (typical Cloudflare account)

| Resource | Free allowance | Assure marketing usage |
|----------|----------------|------------------------|
| **R2 storage** | 10 GB-month | ~4 MB static site (`dist/`) |
| **R2 Class A** (PUT, LIST) | 1 M / month | ~50 PUTs per full upload; **0** when nothing changed |
| **R2 Class B** (GET, HEAD) | 10 M / month | One GET per page/asset request via Worker |
| **Workers requests** | 100 k / day | Apex marketing traffic only (`/app`/`/api` are 302 redirects) |
| **Workers CPU** | 10 ms / invocation (free) | Trivial static serve + redirect |

Egress from R2 through the marketing Worker is **not billed** separately on standard R2 pricing.

## What the deploy scripts do

| Guard | Script | Effect |
|-------|--------|--------|
| **Changed-files-only upload** | `scripts/r2_sync_marketing.py` | SHA-256 manifest in `.cache/marketing-r2-manifest.json`; skips unchanged objects |
| **Worker deploy only when needed** | `scripts/marketing_worker_changed.py` | Redeploys only if `marketing-r2-worker.js` or wrangler config changed |
| **Cooldown** | `scripts/deploy-marketing-r2.sh` | Default 120 s between uploads (`MARKETING_DEPLOY_MIN_INTERVAL`); bypass with `MARKETING_DEPLOY_FORCE=1` |
| **No zone cache purge on build** | `scripts/build-marketing-static.sh` | Removed `purge_everything` (unnecessary for R2 path) |
| **No workers.dev** | `wrangler-marketing-proxy.jsonc` | `workers_dev: false` — avoids stray traffic on `*.workers.dev` |

## Commands

```bash
# Local build only (no Cloudflare API calls)
bash scripts/build-marketing-static.sh

# Preview what would upload
bash scripts/deploy-marketing-r2.sh --dry-run

# Production upload (changed files + Worker if needed)
bash scripts/deploy-marketing-r2.sh

# Force Worker redeploy
bash scripts/deploy-marketing-r2.sh --worker

# Build + plan, no remote writes
bash scripts/deploy-marketing-r2.sh --no-apply
```

## When you might leave free tier

- **Workers Paid** — only if you exceed 100 k requests/day on marketing routes or need longer CPU time (unlikely at current traffic).
- **R2 storage** — only if you store large assets in `assure-marketing-prod` (keep marketing static only).
- **PDF Worker + R2** (`worker/`) — separate buckets for edge PDF; use lifecycle expiry (see `worker/README.md`).
- **SQLite backups to R2** (`scripts/aws/backup-sqlite.sh`) — capped at 8 GB by default (`R2_STORAGE_CAP_GB`).

## Do not

- Run `deploy-marketing-r2.sh` in a loop or CI on every commit without `--dry-run` / `--no-apply`.
- Upload large binaries or user content to the marketing bucket.
- Enable `workers_dev: true` on production marketing Worker.
- Call `purge_everything` on the zone after every copy change (Worker sets `Cache-Control: max-age=300`).

## Verify usage

Cloudflare dashboard → **R2** → `assure-marketing-prod` (storage + operations)
Cloudflare dashboard → **Workers & Pages** → `assure-marketing-proxy` (requests)
