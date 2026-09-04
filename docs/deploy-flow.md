# Deploy flow — app (EC2 + GHCR)

Fast, reliable deploys for **getassureai.com** (`p4-account-wallet`).

## Architecture

```
Cursor/Mac → git push → GitHub
                          ├─ CI (tests)
                          └─ App Docker workflow
                               ├─ build image → ghcr.io/orhgor/assure-app:<sha>
                               └─ SSM → EC2 pull + restart
```

| Layer | Role |
|-------|------|
| **GitHub** | Source of truth, CI, **Docker build** |
| **GHCR** | Pre-built images (`ghcr.io/orhgor/assure-app`) |
| **EC2** | Pull image, run container, persist `data/` |
| **Cloudflare** | Public URL → tunnel → EC2; **PDF edge Worker + R2** for uploads |

Marketing site stays on branch **`webpage`** → Cloudflare Worker (not this flow).

### Edge PDF processing

```
Browser → Cloudflare Worker (signed R2 URL)
       → R2 (temporary PDF)
       → Worker: unpdf (+ Textract fallback)
       → POST /api/substrate on EC2 (text only)
       → R2 delete PDF
```

| Resource | Staging | Production |
|----------|---------|------------|
| Branch | `staging` | `p4-account-wallet` |
| Worker | `assure-worker-staging` | `assure-worker-prod` |
| R2 bucket | `assure-pdf-uploads-staging` | `assure-pdf-uploads-prod` |
| EC2 | `STAGING_INSTANCE_ID` secret | `ASSURE_INSTANCE_ID` secret |
| GitHub Environment | `staging` | `production` |

See `worker/README.md` for deploy and secrets.

---

## Day-to-day (developer)

1. Edit on any machine; work on branch **`p4-account-wallet`**
2. `git commit && git push`
3. GitHub Actions **App Docker** builds and pushes the image
4. If auto-deploy is configured, EC2 updates automatically
5. Otherwise (or to redeploy manually):

   ```bash
   bash scripts/aws/redeploy-via-ssm.sh
   ```

6. Verify:

   ```bash
   curl -s https://getassureai.com/health | python3 -m json.tool
   ```

   `build_sha` must match your commit (short prefix of full SHA).

---

## GitHub secrets (for auto-deploy)

In **Settings → Secrets and variables → Actions**, add:

| Secret | Purpose |
|--------|---------|
| `AWS_ACCESS_KEY_ID` | IAM user with SSM send-command (see `scripts/aws/iam-policy-assure-deploy.json`) |
| `AWS_SECRET_ACCESS_KEY` | Pair for above |
| `ASSURE_INSTANCE_ID` | Optional; defaults to `i-09d0ad0b561113abe` |
| `GHCR_DEPLOY_TOKEN` | Optional; PAT with `read:packages` for fast EC2 pull (see `setup-ghcr-ec2.sh`) |

`GITHUB_TOKEN` is provided automatically (git fetch on EC2 during SSM).

Until these secrets exist, **build still runs on every push**; deploy the image manually with `redeploy-via-ssm.sh` from your Mac.

**GHCR pull on EC2** needs a token with **`read:packages`**. Options:

1. Add `GHCR_TOKEN` to your Mac `.env.production` (used by SSM redeploy), or
2. Add the same to EC2 `/home/ubuntu/assure/.env.production`, or
3. Add `GHCR_DEPLOY_TOKEN` as a GitHub Actions secret for auto-deploy.

Create a classic PAT at GitHub → Settings → Developer settings → PAT with **`read:packages`** (and **`repo`** for private git fetch if needed).

Or run the helper (after `gh auth refresh -h github.com -s read:packages`):

```bash
bash scripts/aws/setup-ghcr-ec2.sh
```

That writes `GHCR_TOKEN` to EC2 `.env.production` and sets the `GHCR_DEPLOY_TOKEN` Actions secret.

---

## EC2 one-time setup

On the instance, `.env.production` should include (optional if SSM passes token from deploy script):

```bash
GHCR_USER=orhgor
GHCR_TOKEN=ghp_...   # fine-grained PAT: read:packages (and repo if needed)
```

If omitted, redeploy via SSM passes the same GitHub token used for `git fetch`.

---

## Manual redeploy (Mac)

Requires `gh auth login` (or `GITHUB_TOKEN`) and AWS CLI with SSM permissions:

```bash
bash scripts/aws/redeploy-via-ssm.sh
```

This syncs git on EC2, logs into GHCR, pulls `ghcr.io/orhgor/assure-app:<commit-sha>`, and restarts — **no Docker build on EC2**.

---

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `manifest unknown` on pull | Wait for **App Docker** workflow to finish building the image for that commit |
| GHCR auth failed on EC2 | Set `GHCR_TOKEN` in `/home/ubuntu/assure/.env.production` |
| SSM deploy job failed in Actions | Add AWS secrets; or deploy from Mac with `redeploy-via-ssm.sh` |
| Stale UI | Hard refresh; check `/health` → `css_version` / `js_version` |

---

## What not to do

- Do not `docker build` on EC2 for routine deploys
- Do not edit production code only on EC2
- Do not commit `.env.production` or API keys
