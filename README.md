# Assure — marketing site (assureproof.com)

The public marketing website for **Assure** — verified document compilation for claims and regulated teams.

**Live:** https://assureproof.com (https://www.assureproof.com)

This repository contains only the marketing website. The Assure workbench/application lives in a separate repository.

## How the site works

- `marketing/` — the complete, self-contained pipeline: templates, i18n + locale catalogs (`en es zh fr de ja tr ar`), static assets, privacy content per locale, `llms.txt`, and the Cloudflare Worker + R2 deploy scripts. See `marketing/README.md`.
- `marketing/build.sh` renders `dist/` (static HTML per locale, `sitemap.xml`, `robots.txt`).
- `marketing/deploy.sh` syncs changed files to R2 (bucket `assure-marketing-prod`) and deploys the `assure-marketing-proxy` Worker.
- `.github/workflows/webpage-deploy.yml` builds + deploys on every push to `main` touching `marketing/**`.

## Domains

- **https://assureproof.com** + **https://www.assureproof.com** — served by the `assure-marketing-proxy` Worker from R2.
- `getassureai.com` / `www.getassureai.com` — permanent 301 to assureproof.com (path + query preserved) via a Cloudflare Redirect Rule. Never add new getassureai.com references.

## Development

```bash
pip install -r marketing/requirements.txt
bash marketing/build.sh          # renders dist/
pytest tests/test_r2_sync_marketing.py tests/test_marketing_revision_copy.py
```

Deploy: merge to `main` (CI builds + deploys), or run `bash marketing/deploy.sh` manually.
