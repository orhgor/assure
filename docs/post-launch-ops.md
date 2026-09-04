# Post-launch ops — tester monitoring & feedback

**Date:** 2026-09-04  
**Branch:** `p4-account-wallet`  
**Production health:** `https://getassureai.com/health` (also `https://app.getassureai.com/health` if that host is routed to the same app)

---

## Summary checklist

| Task | Status | Notes |
| :--- | :--- | :--- |
| System health monitoring | ✅ Live | Cron every 5 min on EC2 (`scripts/aws/install-auto-heal-cron.sh`) |
| Audit log queries | ✅ Live | Schema matches queries below |
| Plausible Analytics | ✅ Live on Flask `/`, `/architecture`, workbench (custom embed, prod-gated) |
| Tester feedback button | ✅ Live — `POST /api/tester-feedback` → `audit_log` |
| Discord/Slack channel | ⬜ Manual | No code change |
| Sentry (optional) | ✅ Wired — bundled `@sentry/browser` (prod-gated) + server SDK when `SENTRY_DSN` set |

---

## 1. Monitor system health (already live)

**URL:** `https://getassureai.com/health`

**Check:** top-level `status: "healthy"`, `build_sha`, and under `checks`: `disk_free_gb`, `sqlite`, `memory`, `backup`.

```bash
curl -s https://getassureai.com/health | jq '{status, build_sha, disk: .checks.disk_free_gb, sqlite: .checks.sqlite, memory: .checks.memory}'
```

**On EC2 (inside the host):**

```bash
curl -s http://127.0.0.1:8765/health | jq .
```

Auto-heal cron (restarts container if health fails):

```bash
*/5 * * * * curl -sf http://127.0.0.1:8765/health >/dev/null || (cd /home/ubuntu/assure && docker compose -f docker-compose.yml -f docker-compose.prod.yml rm -f -s assure-app 2>/dev/null; docker compose -f docker-compose.yml -f docker-compose.prod.yml up -d assure-app)
```

---

## 2. Query audit logs for errors (corrected)

SQLite path on EC2: `/home/ubuntu/assure/data/history.sqlite`  
(Container env: `DATABASE_PATH=/app/data/history.sqlite`)

Use `&&` so `cd` and `sqlite3` are separate commands:

```bash
cd /home/ubuntu/assure && sqlite3 data/history.sqlite "SELECT created_at, action, error_type, error_message FROM audit_log WHERE success=0 ORDER BY created_at DESC LIMIT 20;"
```

**Action counts:**

```bash
cd /home/ubuntu/assure && sqlite3 data/history.sqlite "SELECT action, COUNT(*) FROM audit_log GROUP BY action ORDER BY COUNT(*) DESC;"
```

**Schema reference** (`prompt_matrix/db/connection.py`): `audit_log` columns include `action`, `success` (0/1), `error_type`, `error_message`, `created_at`.  
`system_metrics` columns: `cpu_percent`, `memory_used_mb`, `memory_total_mb`, `disk_free_gb`, `created_at`.

---

## 3. One-liner quick view (corrected)

```bash
cd /home/ubuntu/assure && \
  echo "=== Recent Errors ===" && \
  sqlite3 data/history.sqlite "SELECT created_at, action, error_type FROM audit_log WHERE success=0 ORDER BY created_at DESC LIMIT 10;" && \
  echo "=== System Metrics ===" && \
  sqlite3 data/history.sqlite "SELECT created_at, memory_used_mb FROM system_metrics ORDER BY created_at DESC LIMIT 5;" && \
  echo "=== Health ===" && \
  curl -s http://127.0.0.1:8765/health | jq '{status, build_sha, disk: .checks.disk_free_gb}'
```

---

## 4. Plausible Analytics

**Context:** Static HTML under `landing/` already ships **Google Analytics 4** (`G-54F5NE9Y0P`) with privacy copy on `/privacy`. Flask-served pages (`prompt_matrix/templates/landing.html`, workbench) use Plausible via `includes/plausible.html` — **not** GA4.

**Embed:** Custom Plausible script (domain baked into `pa-we0rKAtBU-r8df6whoZbn.js`). Included from `base.html`, `landing.html`, and `architecture.html`.

**Gating (local dev stays clean):**

| Condition | Plausible loads? |
| :--- | :--- |
| `ENVIRONMENT=production` | Yes (default on EC2) |
| `PLAUSIBLE_ENABLED=1` | Yes (explicit override) |
| Local / dev (neither above) | No |

To test locally:

```bash
PLAUSIBLE_ENABLED=1 python -m prompt_matrix.web
```

**Deploy:** push → GitHub Actions App Docker → `bash scripts/aws/redeploy-app.sh`.

Update `/privacy` if Plausible replaces or supplements GA4 on static pages.

### Browser script vs API key

| Mechanism | What | Where |
| :--- | :--- | :--- |
| **Browser embed** | `pa-we0rKAtBU-r8df6whoZbn.js` in `includes/plausible.html` | Loaded on page — **no API key in templates** |
| **Stats API** | `PLAUSIBLE_API_KEY` | Server-side / CI only — query Plausible Stats API |

The browser script handles pageview tracking automatically when prod-gated. The API key is **not** used in frontend templates; it is for server-side stats queries (dashboards, scripts, GitHub Actions).

**Store manually** (never commit):

```bash
# EC2 .env.production or GitHub Actions secret
PLAUSIBLE_API_KEY=YOUR_PLAUSIBLE_API_KEY
```

Generate the key in Plausible → Site settings → API keys. Rotate if shared in chat.

---

## 5. Tester feedback button (not the thumbs API)

**Existing:** Workbench thumbs call `POST /api/feedback` with `{ run_hash, rating }` where `rating` is `0` or `1`. That feeds the bandit / `prompt_performance` table — not free text.

**For tester notes** (what’s working / confusing), add a **separate** endpoint, e.g. `POST /api/tester-feedback`:

```json
{ "text": "Export button was unclear", "page": "/app" }
```

Log to `audit_log` with `action='TESTER_FEEDBACK'`, `success=1`, `details` JSON. Optional floating button on workbench only; use i18n keys (`tester.feedback.*`) in all seven locales.

Do **not** post `{ text, user: 'tester' }` to `/api/feedback` — it will return `400 Missing run.`

---

## 6. Discord / Slack (manual)

| Platform | Setup | Share |
| :--- | :--- | :--- |
| Discord | Server → private `#testers` channel → invite link | Invite URL |
| Slack | Workspace → private channel → invite testers | Invite URL |

**Pinned message:**

> When reporting an issue, include: 1) What you were doing, 2) What happened, 3) Any error messages (and `build_sha` from `/health` if possible).

---

## 7. Sentry (optional)

Two layers — both prod-gated for local dev:

| Layer | What | When it loads |
| :--- | :--- | :--- |
| Browser | `includes/sentry.html` — bundled `@sentry/browser` (`sentry.bundle.js`) | `ENVIRONMENT=production` or `SENTRY_ENABLED=1` |
| Server | `sentry-sdk[flask]` via `_init_sentry()` in `web.py` | When `SENTRY_DSN` is set (independent of browser gating) |

**Browser embed:** Included from `base.html`, `landing.html`, and `architecture.html` (same gating as Plausible). DSN is injected as `window.__SENTRY_DSN` before the bundled init runs (single init only).

**Browser DSN resolution** (in order):

| Source | Notes |
| :--- | :--- |
| `SENTRY_BROWSER_DSN` | Browser-only override |
| `SENTRY_DSN` | Shared with server SDK when set |
| Production default | Built-in project DSN when `ENVIRONMENT=production` and neither env var is set |

Rebuild the browser bundle after changing `prompt_matrix/static/src/sentry-init.js`:

```bash
npm install
npm run bundle:sentry
```

Docker builds run `npm run bundle:sentry` automatically (multi-stage `Dockerfile`).

**Gating (browser loader only):**

| Condition | Sentry browser loader loads? |
| :--- | :--- |
| `ENVIRONMENT=production` | Yes (default on EC2) |
| `SENTRY_ENABLED=1` | Yes (explicit override) |
| Local / dev (neither above) | No |

To test the browser loader locally:

```bash
SENTRY_ENABLED=1 SENTRY_BROWSER_DSN="https://YOUR_KEY@oYOUR_ORG.ingest.us.sentry.io/YOUR_PROJECT" python -m prompt_matrix.web
```

Open the app, then in the browser devtools console run:

```javascript
myUndefinedFunction();
```

A new issue should appear in the Sentry dashboard within a minute (Issues → filter by project). Confirm only one Sentry init: no `js.sentry-cdn.com` script tag in page source.

### Env vars (EC2 `.env.production` or GitHub Actions secrets)

| Variable | Used for | Notes |
| :--- | :--- | :--- |
| `SENTRY_DSN` | **Server SDK** (`sentry-sdk[flask]`) and browser fallback | DSN URL from Sentry project settings — **not** an auth token |
| `SENTRY_BROWSER_DSN` | Browser bundle only (optional override) | Defaults to `SENTRY_DSN` when set |
| `SENTRY_AUTH_TOKEN` | **CLI / CI only** (releases, source maps) | Token prefix `sntrys_` — **never** used in browser or Flask SDK init |

**Do not confuse token types:**

- **DSN** (`https://…@…ingest…sentry.io/…`) — public by design; goes in SDK init (browser via `window.__SENTRY_DSN`, server via `SENTRY_DSN`).
- **Auth token** (`sntrys_…`) — private; for Sentry CLI, GitHub Actions upload, or API. Store in GitHub Secrets or paste manually into EC2 `.env.production`. Never commit to git.

**EC2 example** (paste real values manually after deploy):

```bash
SENTRY_DSN=https://YOUR_KEY@oYOUR_ORG.ingest.us.sentry.io/YOUR_PROJECT
# optional — same DSN unless you split browser/server projects:
# SENTRY_BROWSER_DSN=
# optional — CI/source maps only:
# SENTRY_AUTH_TOKEN=sntrys_YOUR_TOKEN
```

**Server-side backend errors:** set `SENTRY_DSN` in production env (`.env.production` / compose secrets). Optional `SENTRY_BROWSER_DSN` overrides the browser DSN without changing the server SDK. Redeploy after changing env.

---

## Deploy reminder

```bash
git push origin p4-account-wallet
bash scripts/aws/redeploy-app.sh
# or: bash scripts/aws/ssm-redeploy-and-wait.sh
```

Volumes are preserved on redeploy (`data/history.sqlite` stays intact).
