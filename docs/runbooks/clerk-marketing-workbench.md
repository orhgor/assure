# Clerk — marketing (R2) + workbench (EC2) design

**Status:** **Wired, not enabled.** The server-side path exists and is complete — `prompt_matrix/cloud_auth.py:70-71`
`clerk_configured()`, `:92-95` `require_clerk_login()`, `:104-107` `auth_required()` (which returns
`clerk_configured()`), and `prompt_matrix/middleware.py:61` already reads `session["clerk_user_id"]`;
`templates/auth.html` carries the Clerk mount and `.env.example` / `.env.production.example` carry
`CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY`. What is missing is the **configuration**, not the code:
`.env.staging` contains no Clerk keys, so `auth_required()` is False and the only live gate is the
shared bearer `SHELL_ACCESS_KEY` on `prototype/dev-server.py`. Enabling per-user auth is therefore a
config change (publishable key, secret key, allowed domain) — it does **not** need a build.
**Constraint:** Marketing stays static on R2. PEM, providers, SQLite, and all `/api/*` stay on EC2. Clerk **secret** never ships to R2.

---

## Goals

1. Marketing pages show correct auth chrome: **Sign in**, **Sign up**, **Account**, **Launch workbench** (signed-in vs anonymous).
2. One user identity across `getassureai.com` (marketing) and `app.getassureai.com` (workbench).
3. After auth, user lands in `/app` with Flask session + credit wallet already bound (existing `remember_user` / `ensure_wallet` path).
4. No Clerk logic on Workers beyond existing 302 redirects for `/app*` and `/api*`.

---

## Split of responsibility

| Layer | Host | Clerk role |
| --- | --- | --- |
| **Marketing shell** | R2 static + Worker | Client-only: `@clerk/clerk-js`, publishable key baked at build time. Read session from Clerk; no server session on apex. |
| **Auth UI (full)** | EC2 `/signin`, `/signup` | Existing `auth.html` + Clerk mount (source of truth for sign-in/up forms). |
| **Auth API** | EC2 `/api/auth/*` | Verify Clerk session token → Flask `session` cookie (`clerk_user_id`, `clerk_email`). Billing/wallet hooks unchanged. |
| **Workbench** | EC2 `/app`, protected `/api/*` | Existing `protect_request()` / `login_required`. Requires Flask session (synced from Clerk). |

**Rule:** R2 never calls Clerk Backend API. EC2 holds `CLERK_SECRET_KEY` only.

---

## Clerk Dashboard (one application)

Single Clerk app for Assure production:

| Setting | Value |
| --- | --- |
| **Primary domain** | `app.getassureai.com` |
| **Satellite / additional origins** | `https://getassureai.com`, `https://www.getassureai.com` |
| **Allowed redirect URLs** | `https://app.getassureai.com/app`, `https://app.getassureai.com/signin`, `https://getassureai.com/` (post-auth return to apex if needed) |
| **Sign-in / sign-up URLs** | Hosted on EC2: `https://app.getassureai.com/signin`, `/signup` |

Use Clerk **multi-domain** (satellite) so a session created on either host is recognized by `clerk-js` on both.

---

## Session model (two cookies, one user)

```
Browser
  ├─ Clerk client session (__session / Clerk cookies on .getassureai.com)
  └─ Flask session (assure-app cookie on app.getassureai.com only)
```

### Flow A — sign in from marketing

1. User on `getassureai.com/pricing` clicks **Sign in**.
2. Navigate to `https://app.getassureai.com/signin?next=/app&lang=tr` (always EC2 for auth forms).
3. Clerk mount completes → `POST /api/auth/session` with Bearer Clerk token → Flask session set on **app** subdomain.
4. Redirect to `next` (`/app`).

### Flow B — return visit on marketing (signed in)

1. Static page loads `static/landing-auth.js` (new, small).
2. Script loads Clerk JS with baked publishable key; `clerk.load()` → session present.
3. Header swaps **Sign in** → **Account** + **Launch workbench** (link `https://app.getassureai.com/app`).
4. Optional: on first marketing page load with Clerk session but no Flask session, fire `POST https://app.getassureai.com/api/auth/session` (`credentials: include` after Clerk token exchange) so `/app` does not ask again.

### Flow C — launch workbench (anonymous)

1. **Launch App** → `https://app.getassureai.com/app?lang=…`
2. EC2 `protect_request()` → redirect `/signin?next=/app` if Clerk required and Flask session empty.

### Flow D — sign out

1. Marketing or workbench **Sign out** → Clerk `signOut()` + `POST /api/auth/logout` on app host.
2. Redirect to apex `/` or `/signin`.

---

## Marketing header states (i18n keys)

| State | Left nav | Right cluster |
| --- | --- | --- |
| Anonymous | Product links | **Sign in** · **Sign up** · **Launch App** |
| Clerk session | Product links | **Account** · **Launch workbench** · avatar/menu |
| Self-hosted / Clerk off (build flag) | Product links | **Launch App** only (current behavior) |

Build-time flag: `CLERK_PUBLISHABLE_KEY` empty → omit auth script; no sign-in links (matches local/static export today).

---

## Worker routing (no change to split)

Keep `marketing-r2-worker.js` behavior:

- Marketing paths → R2 object.
- `/app*`, `/api*` on apex → **302** to `app.getassureai.com` (already implemented).

**Do not** proxy `/signin` through Worker to R2. Sign-in URLs in marketing HTML always point to **app** host.

Optional later: apex `/signin` → 302 to `app.getassureai.com/signin` (convenience only).

---

## Static build changes (future PR)

1. `scripts/render_static.py` — pass `auth.publishable_key` when key set at build time (env or `.env.production` on CI builder only; key is public).
2. `landing_header.html` — auth cluster with `data-auth-slot` placeholders; hidden when `auth.configured` false.
3. New `static/landing-auth.js` — Clerk load, header state, no provider keys.
4. `build-marketing-static.sh` — require publishable key in prod builds when Clerk enabled.

Secrets stay in EC2 `.env.production` only.

---

## EC2 changes (future PR)

1. **CORS** on `/api/auth/session` and `/api/auth/me`: allow `Origin: https://getassureai.com` with `credentials` for cross-subdomain sync (Flow B).
2. **Cookie** — ensure Flask session cookie `Domain=.getassureai.com; Secure; SameSite=Lax` when `ENVIRONMENT=production` so marketing-initiated sync works (evaluate security vs host-only cookie).
3. **`/api/auth/config`** — already public; marketing JS may poll for `configured` / `required`.
4. Keep `PUBLIC_HTML` including `/signin`, `/signup`; keep `/app` protected.

No move of auth routes to Worker or R2.

---

## Security notes

- Publishable key in static HTML is expected; rotate in Clerk if leaked.
- Never embed `CLERK_SECRET_KEY` in R2, Worker, or marketing JS.
- `POST /api/auth/session` must verify token server-side (existing `verify_session_token`).
- Rate-limit auth endpoints on EC2 if marketing sync adds traffic.
- Stripe webhooks and `/api/webhook/*` remain EC2-only, unchanged.

---

## Out of scope (this design)

- Clerk Organizations / SSO for enterprise.
- Marketing-gated content (paywalled pages) — all marketing stays public.
- Replacing Flask session with JWT-only API auth (future simplification, not required for v1).

---

## Implementation phases

| Phase | Deliverable |
| --- | --- |
| **P0** | Clerk Dashboard: satellite domains + redirect URLs |
| **P1** | EC2: cookie domain + CORS for `/api/auth/session` |
| **P2** | Marketing build: header auth cluster + `landing-auth.js` |
| **P3** | i18n keys for auth chrome in all 7 locales |
| **P4** | Playwright: anonymous marketing → sign-in → `/app`; signed-in header on apex |

**Not in scope now:** demo, Clerk Components on R2 beyond header chrome, account billing UI on marketing ( stays `/account` on EC2).

---

## Related docs

- Stack split: `.cursor/rules/deploy-flow.mdc`
- Marketing deploy: `docs/runbooks/marketing-r2-free-tier.md`
- PEM / workbench auth today: `prompt_matrix/cloud_auth.py`, `templates/auth.html`
