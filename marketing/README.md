# marketing/ — the isolated webpage tree

This directory is the complete, self-contained pipeline for the public
marketing site (getassureai.com). It is fully independent of the workbench
(EC2, Docker): the build never reads the workbench tree and never needs a
database. Everything the site needs lives here.

## Layout

| Path | Role |
|---|---|
| `app.py` | Standalone Flask app for static rendering — string catalog, brand, gettext, auth/edition shims. No DB, no workbench reads. |
| `i18n.py` | Own string catalog (all locales, text copied from the original catalog). |
| `templates/` | Own page templates and includes. |
| `static/` | Own assets (landing.css, landing JS, icons, favicon, sentry bundle). |
| `content/privacy/` | Own privacy policy HTML per locale. |
| `cache_versions.py` | Own cache-bust values (LANDING_CSS / LANDING_JS). |
| `render.py` | Renders the marketing templates to static HTML, one locale per run. |
| `build.sh` | Builds `dist/` (assets + the published locales). |
| `deploy.sh` | Uploads changed `dist/` files to Cloudflare R2 and deploys the marketing Worker when its source changes. |
| `sync_r2.py` | Content-hash sync (uploads only changed files — free-tier friendly). |
| `worker_changed.py` | Skips redundant Worker deploys. |
| `cloudflare/` | Worker source + wrangler config + deploy wrapper. |
| `requirements.txt` | Build deps: `flask`. That's all. |

## Isolation contract

The site owns its copies of markup, assets, and strings under `marketing/`.
Editing the website can never change the workbench, and the other way round.
A CI check fails any PR that touches both `marketing/` and the workbench tree.

## Deploy flow

- **Automatic**: push to `main` touching `marketing/**` or `landing/**` →
  `.github/workflows/webpage-deploy.yml` builds + deploys.
- **Manual** (CI down): `bash marketing/deploy.sh` (needs `npx wrangler login`
  on the machine).

## Rules

- Never read anything from the workbench tree from here, and never import the workbench app.
- Never put page HTML in `landing/` — that folder is hosting metadata + assets only.
- Bump `LANDING_CSS` in `marketing/cache_versions.py` when `landing.css` changes.
- Arabic (`ar`) is built for preview only. To turn it on later: `PUBLISH_LOCALES="en es zh fr de ja tr ar" bash marketing/build.sh` (or set that variable in the deploy workflow); the switcher and the language count follow it automatically.
