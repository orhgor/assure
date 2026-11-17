# Ship the webpage — runbook for content editors
#
# The normal loop is ONE step:
#     edit files -> git push (to main) -> live in ~2 minutes
# The GitHub Action `.github/workflows/webpage-deploy.yml` builds and deploys
# automatically. You never run the deploy script by hand in the normal case.

## Where the webpage lives
- Serving: Cloudflare Worker `assure-marketing-proxy` ships `dist/` as Worker
  static assets (no R2) on assureproof.com; getassureai.com canonicalizes to it.
- Build: `marketing/` — the isolated webpage tree (see `marketing/README.md`).
  No database, no workbench imports.
- Source of truth: `origin/main` in `orhgor/assure` — the files that matter:
  - `marketing/templates/landing.html`         structure & markup
  - `marketing/i18n.py` + `marketing/locales/` all visible text (en = source)
  - `marketing/static/landing.css`             styles
  - `marketing/cache_versions.py`              `LANDING_CSS` version — bump when CSS changes
- Docs: `marketing/README.md`.

## Editing loop
1. Branch from `main` (never build/deploy from another branch's tree).
2. Edit the files above. Text lives in `marketing/i18n.py` + `marketing/locales/`;
   templates reference keys with `{{ strings.get('key', 'English fallback') }}`.
3. If CSS changed: bump `LANDING_CSS` in `marketing/cache_versions.py`.
4. Push your branch and merge to `main` (PR or direct push if you're solo).
5. Done. The Action builds, deploys the Worker with `dist/` as static assets,
   and smoke-checks the live page.

## Local preview (optional, before pushing)
```bash
git worktree add /tmp/web origin/main --detach && cd /tmp/web
# edit here, then:
bash marketing/build.sh
# dist/ is the built site — open dist/index.html
```
No Postgres, no Docker — the marketing build is standalone
(`marketing/app.py` never imports the workbench app).

## Manual deploy (only when CI is down)
```bash
bash marketing/deploy.sh --skip-build
```
Requires wrangler auth on your machine (`npx wrangler login`).

## Rules that prevent drift (learned the hard way)
- Only build/deploy from a tree checked out at `origin/main`. Never from a
  feature branch or a stale /tmp tree.
- Commit in the same session as the deploy, from the same tree you built.
- New static assets must be added to the rsync include list in
  `marketing/build.sh` — `icons.js` was missed once and 404'd in production.
- `marketing/` is self-contained: it never imports the workbench app or any
  database. See `marketing/README.md`.
- After any deploy, verify: `curl -s https://www.assureproof.com/ | grep -c '<your change>'`.

## Known gotchas
- The Worker serves `dist/` through the `ASSETS` binding (content types are
  automatic); legacy paths `/contact`, `/government`, `/app`, `/api` are
  redirected in `marketing/cloudflare/marketing-worker.js`.
- `landing-i18n.js` flattens `data-i18n` elements at runtime; structured markup
  in such elements is protected by the `childElementCount` guard — keep it.
- Cloudflare edge may serve stale HTML despite `max-age=300` if a zone cache rule
  ignores query strings — re-asserting the Worker route or a cache purge fixes it.

## Contact form → Resend

- The contact page posts JSON to `POST /contact-submit`, handled by the marketing
  Worker (legacy `/api/*` paths other than `/api/z3-demo` 301 to `/contact-us/`).
- The Worker emails `sales@getassureai.com` via the Resend API using the
  `RESEND_API_KEY` Worker secret, with `reply_to` set to the submitter.
- Set/rotate the secret once per account (persists across deploys):

  ```shell
  cd marketing/cloudflare && npx wrangler secret put RESEND_API_KEY
  ```

- `from` is `website@getassureai.com` — that address (or a catch-all) must be
  verified as a sending domain in the Resend dashboard or Resend rejects the send.
- If the secret is missing (503) or Resend fails (502), the page shows an inline
  error so submissions are never silently lost.
- A hidden `company_website` honeypot field is silently accepted and dropped.
- `POST /api/z3-demo` serves the interactive hero proof card — deterministic,
  no external calls (see `marketing-worker.js`).
