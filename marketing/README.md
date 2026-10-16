# marketing/ — the isolated webpage tree

This directory is the complete, self-contained pipeline for the public
marketing site (getassureai.com). It is deliberately isolated from the
workbench app (`prompt_matrix/web.py`, EC2, Docker): the build never
imports the app and never needs a database.

## Layout

| Path | Role |
|---|---|
| `app.py` | Standalone Flask app for static rendering — string catalog, brand, gettext, auth/edition shims. No DB, no workbench imports. |
| `render.py` | Renders the marketing templates to static HTML, one locale per run. |
| `build.sh` | Builds `dist/` (assets + all 7 locales). |
| `deploy.sh` | Uploads changed `dist/` files to Cloudflare R2 and deploys the marketing Worker when its source changes. |
| `sync_r2.py` | Content-hash sync (uploads only changed files — free-tier friendly). |
| `worker_changed.py` | Skips redundant Worker deploys. |
| `cloudflare/` | Worker source + wrangler config + deploy wrapper. |
| `requirements.txt` | Build deps: `flask`. That's all. |

## What is shared with the app (files only, never code)

The single sources of truth for markup, assets, and strings stay in
`prompt_matrix/templates`, `prompt_matrix/static`, `prompt_matrix/i18n.py`,
`prompt_matrix/ui_cache.py`. The workbench serves the same files live on EC2;
this tree renders them to static HTML for Cloudflare. Neither side imports
the other's code.

## Deploy flow

- **Automatic**: push to `main` touching webpage files →
  `.github/workflows/webpage-deploy.yml` builds + deploys.
- **Manual** (CI down): `bash marketing/deploy.sh` (needs `npx wrangler login`
  on the machine).

## Rules

- Never import `prompt_matrix.web` (or anything that pulls the app/DB) here.
- Never put page HTML in `landing/` — that folder is hosting metadata + assets only.
- Bump `LANDING_CSS` in `prompt_matrix/ui_cache.py` when `landing.css` changes.