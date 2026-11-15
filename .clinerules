# Rules for AI agents (Cursor, Cline, Oh My Pi, Claude, any bot)

## Scope
- This repository is the public marketing website only. The live site is https://assureproof.com.
- The website lives ONLY in marketing/. All website work (copy, i18n, templates, CSS, JS, build, Worker) happens in marketing/ only.
- The workbench/application lives in a separate repository. Never link the website to the workbench or to app.getassureai.com.

## Build & deploy
- `marketing/build.sh` renders `dist/` (templates, locales, static assets, sitemap, robots.txt).
- `marketing/deploy.sh` syncs changed `dist/` files to R2 (bucket `assure-marketing-prod`) and deploys the `assure-marketing-proxy` Worker.
- Merging to `main` (a push touching `marketing/**`) deploys production; only the owner merges.

## Safety
- Never run `marketing/deploy.sh`, `marketing/sync_r2.py` or `wrangler deploy` (wrangler `--dry-run` only). Merging to main deploys production; only the owner merges.
- Never delete files without explicit owner approval.
- Never commit secrets, tokens or passwords.

## Domains
- Primary: https://assureproof.com and https://www.assureproof.com — served by the `assure-marketing-proxy` Worker from R2 (bucket `assure-marketing-prod`).
- Legacy: getassureai.com and www.getassureai.com 301 to assureproof.com (path + query preserved) via a Cloudflare Redirect Rule. Never add new getassureai.com references.
