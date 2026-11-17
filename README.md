# assureproof.com — marketing site

This repository builds and deploys the public marketing site for **Assure**:
https://assureproof.com (getassureai.com canonicalizes to it).

Everything the site needs lives in [`marketing/`](marketing/README.md) — a
self-contained pipeline (Flask render → static `dist/` → Cloudflare Worker
static assets). No database, no workbench imports.

## Deploy

Push to `main` (paths `marketing/**`). `.github/workflows/webpage-deploy.yml`
runs the website checks, builds `dist/`, and deploys the Worker with `dist/`
as static assets — gated on the checks passing. Only the owner merges.

## Local preview

```bash
bash marketing/build.sh
# open dist/index.html
```

## Tests

```bash
bash marketing/build.sh && pytest tests/ -v
```

The deploy job runs the same suite as its gate (`website-checks`).

## Rules

See `AGENTS.md` and `marketing/README.md`. Short version: never run
`marketing/deploy.sh` by hand (CI deploys; `--dry-run` is fine), never commit
secrets, new static assets must be added to the `build.sh` include list.