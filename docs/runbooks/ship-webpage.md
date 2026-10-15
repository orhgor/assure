# Ship the webpage — runbook for content editors
#
# The normal loop is ONE step:
#     edit files -> git push (to main) -> live in ~2 minutes
# The GitHub Action `.github/workflows/webpage-deploy.yml` builds and deploys
# automatically. You never run the deploy script by hand in the normal case.

## Where the webpage lives
- Serving: Cloudflare Worker `assure-marketing-proxy` reads bucket
  `assure-marketing-prod` (R2) on getassureai.com.
- Source of truth: `origin/main` in `orhgor/assure` — the files that matter:
  - `prompt_matrix/templates/landing.html`     structure & markup
  - `prompt_matrix/i18n.py`                    all visible text (EN dict = source)
  - `prompt_matrix/static/landing.css`         styles
  - `prompt_matrix/ui_cache.py`                `LANDING_CSS` version — bump when CSS changes
- Docs: `docs/webpage-all-content.md`.

## Editing loop
1. Branch from `main` (never build/deploy from another branch's tree).
2. Edit the files above. Text lives in `i18n.py`; if you add a new key, also
   reference it in the template with `{{ strings.get('key', 'English fallback') }}`.
3. If CSS changed: bump `LANDING_CSS` in `prompt_matrix/ui_cache.py`.
4. Push your branch and merge to `main` (PR or direct push if you're solo).
5. Done. The Action builds, uploads to R2, smoke-checks the live page.

## Local preview (optional, before pushing)
```bash
colima start   # if Docker isn't running (build imports the app, needs Postgres)
docker compose -f docker-compose.dev.yml up -d
git worktree add /tmp/web origin/main --detach && cd /tmp/web
# edit here, then:
DATABASE_URL='postgresql://assure:assure@localhost:5432/assure' \
PYTHON=/path/to/repo/.venv/bin/python \
bash scripts/build-marketing-static.sh
# dist/ is the built site — open dist/index.html
```

## Manual deploy (only when CI is down)
```bash
MARKETING_DEPLOY_FORCE=1 \
DATABASE_URL='postgresql://assure:assure@localhost:5432/assure' \
PYTHON=/path/to/repo/.venv/bin/python \
bash scripts/deploy-marketing-r2.sh
```
Requires wrangler auth on your machine (`npx wrangler login`).

## Rules that prevent drift (learned the hard way)
- Only build/deploy from a tree checked out at `origin/main`. Never from a
  feature branch or a stale /tmp tree.
- Commit in the same session as the deploy, from the same tree you built.
- `wrangler r2 object put` without `--remote` writes to a LOCAL simulation —
  always pass `--remote` for manual object puts.
- After any deploy, verify: `curl -s https://www.getassureai.com/ | grep -c '<your change>'`.

## Known gotchas
- The Worker's `contentType()` maps extensions; new asset types need a line there
  (`scripts/cloudflare/marketing-r2-worker.js`).
- Cloudflare edge may serve stale HTML despite `max-age=300` if a zone cache rule
  ignores query strings — re-asserting the Worker route or a cache purge fixes it.