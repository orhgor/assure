# Assure — Webpage & marketing (master reference)

**Last updated:** 2026-09-09 (evening — UI polish live on R2)
**Live marketing:** https://getassureai.com (R2 + Cloudflare Worker `assure-marketing-proxy`)
**Live workbench:** https://app.getassureai.com/app (EC2 via Tunnel)
**Production `/health` (app, not R2):** `f4e2d20`, UI workbench `assure-127`
**Live marketing cache:** `landing.css?v=60` / `landing-pilot.js?v=45` (verified curl 2026-09-09)

This document inventories **every public webpage surface** — live routes, templates, static export, copy sources, APIs, analytics, and deploy paths. It is the webpage counterpart to [assure-ai-all-functions.md](./assure-ai-all-functions.md) (workbench).

---

## Final status — 2026-09-09

**Verdict:** Marketing homepage and locale homes are **live on production R2**. Workbench is unchanged by this pass.

| Item | Status |
|------|--------|
| Apex marketing | **Live** — `GET /` returns HTML with `landing.css?v=60`, two-line hero, new subtitle, 2×2 trust cards |
| Locales | **Live** — `/es/` `/zh/` `/fr/` `/de/` `/ja/` `/tr/` (200) |
| Inner pages | **Live** — `/privacy` `/architecture` `/pricing` `/terms` `/about` (200); 52 R2 objects uploaded |
| Worker | **Deployed** — `assure-marketing-proxy` on `getassureai.com` routes |
| Source git | `c834afb` on `feat/marketing-ui-polish` — [PR #33](https://github.com/orhgor/assure/pull/33) into `main` |
| Workbench | **Not this ship** — EC2 `/health` `f4e2d20` / `assure-127` |
| Staging EC2 | **502** (`https://staging.getassureai.com/`) |

**UI polish that shipped (Stack A → R2):**

- Trust cards: `grid-template-columns: repeat(2, 1fr)` (2×2; 1-col under 800px), `.trust-card` 32px padding, 8px radius, hover shadow
- Hero H1: `.hero-title` two `.text-line` rows, `line-height: 1.1`, desktop `white-space: nowrap` on each line
- Hero sub: `max-width: 640px`, `line-height: 1.6`; English *Plausible AI is a liability. Before you export, Assure verifies every clause, every citation, and every financial figure against your source documents.* (all 7 locales)
- Why Now body: left-aligned, `max-width: 680px`
- Hero CTAs: matched `280px` width
- Pricing: both cards `48px` price type, `40px 32px` padding, `min-height: 420px`
- Footer: `#f8fafc`, `border-top`, `padding: 64px 0`, `margin-top: 64px`

**CDN:** Worker `cache-control: public, max-age=300` — hard-refresh if an old subtitle is still visible.

---

## Table of contents

1. [Final status — 2026-09-09](#final-status--2026-09-09)
2. [Production architecture — read this first](#1-production-architecture--read-this-first)
3. [Marketing pages (source + live)](#2-marketing-pages-source--live)
4. [Landing copy & i18n keys](#3-landing-copy--i18n-keys)
5. [Enterprise privacy policy](#4-enterprise-privacy-policy)
6. [Static site draft (`landing/`)](#5-static-site-draft-landing)
7. [Interactive features & APIs](#6-interactive-features--apis)
8. [Assets, fonts & cache busting](#7-assets-fonts--cache-busting)
9. [Analytics & error tracking](#8-analytics--error-tracking)
10. [Deploy, DNS & routing](#9-deploy-dns--routing)
11. [Copy rules & companion docs](#10-copy-rules--companion-docs)
12. [Change checklist](#11-change-checklist)
13. [Related documents](#12-related-documents)

---

## 1. Production architecture — read this first

Marketing and workbench are **split at the edge**. Marketing is pre-rendered static HTML in **R2**; the interactive app stays on **EC2**.

```
Marketing (/, /privacy, /architecture, /static/*, locale paths)
  → Cloudflare Worker  assure-marketing-proxy
  → R2 bucket          assure-marketing-prod

/app, /api/*  on apex
  → Worker 302         → app.getassureai.com

Workbench + API
  → Cloudflare Tunnel  → EC2 Flask :8765 (Docker)
```

| Surface | Where it runs | URL |
|---------|---------------|-----|
| **Marketing (static)** | R2 + Worker routes | `https://getassureai.com/` |
| **Workbench (Flask)** | AWS EC2 production (`i-09d0ad0b561113abe`) | `https://app.getassureai.com/app` |
| **Staging (full stack)** | AWS EC2 staging (`i-03e39eccc57572191`) | `https://staging.getassureai.com/` — **502** (disk full; SSM RunShellScript fails) |

### Three code paths (do not confuse)

| Path | Location | Role |
|------|----------|------|
| **A — Marketing source (LIVE after export)** | `prompt_matrix/templates/landing*.html`, `i18n.py`, `static/landing*` | Jinja source → `scripts/render_static.py` → R2 |
| **B — Static HTML draft (legacy)** | `landing/*.html`, `landing/assets/*` | Not on apex; `/guide` etc. **404** on production |
| **C — Workbench** | `prompt_matrix/templates/index.html`, `web.py` | EC2 only; not in R2 |

**Rule:** Copy that must appear on https://getassureai.com/ → edit **Stack A**, rebuild with `scripts/deploy-marketing-r2.sh`, verify live.

**Rule:** Workbench changes → EC2/GHCR redeploy only; they do **not** go to R2.

---

## 2. Marketing pages (source + live)

### 2.1 Source templates & static export

| Source template | Static output (R2 key) | Live URL |
|-----------------|------------------------|----------|
| `landing.html` | `index.html`, `{locale}/index.html` | `/`, `/es/`, … |
| `landing_privacy.html` | `privacy/index.html`, `{locale}/privacy/index.html` | `/privacy` |
| `architecture.html` | `architecture/index.html`, … | `/architecture` |
| `pricing.html`, `terms.html`, `about.html` | matching paths under `dist/` | `/pricing`, `/terms`, `/about` |

**Export pipeline:**

| Script | Role |
|--------|------|
| `scripts/render_static.py` | Pre-renders 6 pages × 7 locales from Flask templates |
| `scripts/build-marketing-static.sh` | Builds `dist/` (or `DIST_DIR`) |
| `scripts/deploy-marketing-r2.sh` | Upload to R2 + deploy Worker (`--dry-run`, changed-files-only) |
| `scripts/r2_sync_marketing.py` | SHA-256 manifest; skips unchanged R2 objects |
| `scripts/marketing_worker_changed.py` | Skip Worker redeploy when JS/config unchanged |
| `scripts/cloudflare/wrangler-marketing-proxy.jsonc` | Worker config (R2 binding + routes; `workers_dev: false`) |
| `scripts/cloudflare/marketing-r2-worker.js` | Serves R2; redirects `/app` and `/api` to `app.getassureai.com` |

Flask still registers the same routes for local/staging render; production apex marketing is **served from R2**, not Flask on EC2.

### 2.2 Route inventory (user-facing)

| URL | Live backend | Notes |
|-----|--------------|-------|
| `GET /` | R2 | Enterprise home |
| `GET /privacy` | R2 | Enterprise privacy policy (12 sections) |
| `GET /architecture` | R2 | How it works |
| `GET /pricing`, `/terms`, `/about` | R2 | Legal/pricing pages |
| `GET /es/`, `/zh/`, … | R2 | Locale prefixes (7 locales: `en es zh fr de ja tr`) |
| `GET /app` on apex | Worker **302** → `app.getassureai.com/app` | Not R2 |
| `GET /api/*` on apex | Worker **302** → `app.getassureai.com/api/*` | Not R2 |
| `GET /app` on `app.` host | EC2 Flask | Workbench |

**Anchors on `/`:** `#proof`, `#why-now`, `#trust`, `#pricing`
**Header nav:** Proof · Why now · Trust · Privacy · Language · Launch App (`includes/landing_header.html`)

### 2.3 Home page sections (`landing.html`) — 2026-09-09 evening

| Section ID | Content |
|------------|---------|
| Hero | H1 two lines (*Draft at the speed of AI.* / *Verify with mathematical certainty.*) · sub *Plausible AI is a liability. Before you export, Assure verifies every clause, every citation, and every financial figure against your source documents.* · CTAs (Request Enterprise Pilot · Run a 60-Second Red-Hat Audit), matched 280px. **No kicker or badge in hero.** |
| `#proof` | Role tabs: Coverage Counsel & Litigators · Public Adjusters & Risk Managers · Compliance Officers |
| `#why-now` | *Plausibility is a liability. Certainty is a competitive advantage.* + left-aligned body (max 680px) |
| `#trust` | Trust badge *Enterprise ready · Bring your own keys · 7 languages* + **2×2** cards (Orchestration Engine, Mathematical Logic Engine, Adversarial Audit, Audit-Ready Export) |
| `#pricing` | Free ($0) vs Enterprise Pilot — equal 48px price type, shared padding / min-height |
| Modal `#download-modal` | Pilot form → `POST https://app.getassureai.com/api/waitlist` |

### 2.4 Brand defaults (English source)

From `prompt_matrix/i18n.py` → `brand.*`:

| Key | English value |
|-----|----------------|
| `brand.page_title` | Assure AI — The Deterministic Truth Engine for High-Stakes Professionals |
| `brand.hero_title` | Draft at the speed of AI. Verify with mathematical certainty. |
| `brand.tagline` | The enterprise standard for verified AI drafting. |
| `brand.meta_description` | Deterministic truth engine for insurance, legal, and compliance professionals… |
| `brand.category` | The Deterministic Truth Engine |

Architecture page uses `brand.architecture_title` and `arch.*` keys.

### 2.5 Shared includes

| File | Role |
|------|------|
| `templates/includes/landing_header.html` | Logo, nav, locale `<select>`, Launch App → `app.getassureai.com` |
| `templates/includes/landing_footer.html` | © line, server-processing disclosure, Privacy · Terms · Architecture |
| `templates/includes/plausible.html` | Plausible analytics embed |
| `templates/includes/sentry.html` | Browser Sentry DSN when configured |

**Footer disclosure (`landing.footer.engineer_tagline`):** *Secure by design. Processed on our servers, never used to train our models.*

---

## 3. Landing copy & i18n keys

**Source of truth for live copy:** `prompt_matrix/i18n.py` (English) + locale overrides for `es`, `zh`, `fr`, `de`, `ja`, `tr`.

**Key prefixes (~100+ keys):**

| Prefix | Covers |
|--------|--------|
| `brand.*` | Page titles, meta, tagline, hero |
| `landing.nav.*` | Header links, menu, launch |
| `landing.hero.*` | Hero sub, badge (trust section), CTAs |
| `landing.proof.*` | Role tabs, step flows, captions |
| `landing.why.*` | Why now title + long copy |
| `landing.trust.*` | Trust section title + architecture cards |
| `landing.plans.lead` | BYOK pricing lead under `#pricing` |
| `landing.plans.*` | Pricing grid on home |
| `landing.pilot.*` | Enterprise pilot modal form + success state |
| `landing.features.*`, `landing.compare.*`, `landing.demo.*` | Extended/demo templates and legacy blocks |
| `arch.*` | Architecture page body |
| `landing.footer.*` | Footer strings |
| `privacy.enterprise.*` | Privacy page shell (title, TOC labels, summary) |

**Runtime (Flask/staging):** `_landing_page()` injects `strings=string_catalog(lang)` and sets `assure_lang` cookie. Client-side switcher: `static/landing-i18n.js`.

**Static export:** `render_static.py` bakes locale strings into HTML at build time; `landing-i18n.js` still handles in-page locale switch (reload to locale path).

**First paint:** Jinja `{{ strings.get('key', 'fallback') }}` and `data-i18n` / `data-i18n-html` on visible nodes.

**Copy rules (Sep 9 final pass):**

- Do **not** claim *court-defensible*, *zero retention*, or *data never leaves your machine* on marketing.
- Hero certainty framing: *mathematical certainty*, *deterministically grounded*.
- Risk framing: verify-before-export (clause / citation / figure) — not absolute guarantees. Do not restore *reduces the risk* as the hero sub.
- Closed-mode tooltip (`privacy.chip.tip.closed`): server-processing disclosure when workbench is redeployed.

---

## 4. Enterprise privacy policy

**Live:** https://getassureai.com/privacy (R2)

| Item | Detail |
|------|--------|
| Template | `prompt_matrix/templates/landing_privacy.html` |
| Body content | `prompt_matrix/content/privacy/{en,es,zh,fr,de,ja,tr}.html` |
| Loader | `prompt_matrix/privacy_enterprise.py` → merged in `i18n.catalog()` as `privacy.enterprise.body_html` |
| Route (Flask) | `/privacy` → `_landing_page("landing_privacy.html")` in `web.py` |
| Styles | `.privacy-landing`, TOC, summary box, tables in `landing.css` |
| Tests | `tests/test_privacy_enterprise.py` |

**Page structure (12 sections):**

1. What data we collect (`#collect`)
2. How we use your data (`#use`)
3. Sharing your data (`#sharing`)
4. Your rights (`#rights`)
5. Data security (`#security`)
6. Data retention (`#retention`)
7. Children's privacy (`#children`)
8. Disclaimer (`#disclaimer`)
9. Data processing agreement (`#dpa`)
10. International transfers (`#transfers`)
11. Changes to this policy (`#changes`)
12. Contact us (`#contact`)

**Last updated line:** September 8, 2026 (`privacy.enterprise.updated`)

Replaces legacy `templates/privacy.html` for marketing; old template may still exist for workbench reference.

---

## 5. Static site draft (`landing/`)

Not served on production apex for most paths (verified 2026-09-08: `/guide` → 404). Kept for webpage-branch sync, future deploy, and copy experiments. **Not synced** with Sep 8–9 Truth Engine / Jobs-style copy on Stack A.

### 5.1 HTML pages

| File | Canonical intent | Notes |
|------|------------------|-------|
| `index.html` | Home (BYOK auditor) | Hero: *Never Send an Unverified AI Draft to a Client, Board, or Court.* Interactive audit simulator, compiler demo |
| `guide.html` | `/guide` — 60-second quickstart | API key setup, NLI trust signals |
| `pricing.html` | Standalone pricing | Links from static nav |
| `about.html` | About | |
| `privacy.html` | Privacy | Superseded on live site by enterprise privacy |
| `terms.html` | Terms | |
| `install.html` | Source install | |
| `hallucination-detection.html` | Check outputs explainer | |
| `audit.html` | Public audit / revision table | Counts must match `docs/audits/` |
| `404.html` | Not found | |
| `use-cases/analyst.html` | Analyst persona | |
| `use-cases/consultant.html` | Consultant persona | |
| `use-cases/researcher.html` | Researcher persona | |

### 5.2 Static assets

| File | Role |
|------|------|
| `assets/site.css` | Shared styles (`?v=30` in HTML) |
| `assets/site.js` | Reveal animations, nav |
| `assets/compiler.js` | Landing dialect translation demo (browser-only) |
| `favicon.svg` | Favicon |
| `sitemap.xml` | SEO URLs (mix of static paths and Flask paths — reconcile before publish) |
| `robots.txt`, `_headers`, `_redirects` | Cloudflare Pages / Workers metadata |

### 5.3 Static-only markdown (copy bank)

| File | Purpose |
|------|---------|
| `landing/ICP.md` | Personas, hero rules, job titles, discover paths |
| `landing/objections.md` | FAQ / sales objections (ChatGPT, wrapper, PromptLayer, privacy) |
| `landing/PRODUCT_HUNT.md` | Product Hunt launch copy |
| `landing/launch/*.md` | Twitter, LinkedIn, newsletter drafts |
| `landing/README.md` | Tunnel / DNS setup notes |

---

## 6. Interactive features & APIs

### 6.1 Enterprise pilot waitlist (live)

| Item | Detail |
|------|--------|
| UI | `#waitlist-form` in `landing.html` modal; opened via `[data-waitlist-open]` |
| JS | `prompt_matrix/static/landing-pilot.js` |
| API | `POST https://app.getassureai.com/api/waitlist` — fields: `email`, `company`, `workflow` |
| Backend | `prompt_matrix/waitlist.py` → Supabase when configured |
| Public | No auth; CORS limited to landing origins + localhost |

### 6.2 Zero-Risk Paste Test (sandbox)

| Item | Detail |
|------|--------|
| API | `POST /api/sandbox/verify` |
| Auth | Public — no login, no persistence |
| Pipeline | Same lock inference + Z3 + Red-Hat as workbench, zero storage |
| Static demo | Stack B `index.html` simulator is **visual only**; live sandbox is API-driven from app/landing integrations |

### 6.3 Handoff to workbench

| CTA | Target |
|-----|--------|
| Launch App / Open live sandbox | `https://app.getassureai.com/app` |
| Connect / BYOK | `GET /connect` inside app |

---

## 7. Assets, fonts & cache busting

### 7.1 Live marketing (Stack A → R2)

| Asset | Path | Version (`ui_cache.py`) |
|-------|------|-------------------------|
| Landing CSS | `prompt_matrix/static/landing.css` | `LANDING_CSS = "60"` |
| Pilot modal JS | `prompt_matrix/static/landing-pilot.js` | `LANDING_JS = "45"` |
| i18n switcher | `prompt_matrix/static/landing-i18n.js` | same bump as `LANDING_JS` |
| Demo (if embedded) | `prompt_matrix/static/landing-demo.js` | bump with landing JS |

**Fonts (live home):** Inter (Google Fonts). Architecture page adds IBM Plex Sans / Mono.

**After CSS/JS edit:** bump `ui_cache.py`, rebuild static (`build-marketing-static.sh`), redeploy R2.

### 7.2 Workbench (Stack C — not R2)

| Asset | Version |
|-------|---------|
| `style.css` / `script.js` | `assure-127` (from production `/health`, not R2) |

### 7.3 Static draft (Stack B)

| Asset | Path | Version |
|-------|------|---------|
| Site CSS | `landing/assets/site.css` | `?v=30` in HTML (manual bump) |
| Site JS | `landing/assets/site.js` | manual |
| Compiler demo | `landing/assets/compiler.js` | manual |

---

## 8. Analytics & error tracking

| Service | Where | ID / notes |
|---------|-------|------------|
| **Plausible** | Live marketing pages (`includes/plausible.html`) | Custom script `pa-we0rKAtBU-r8df6whoZbn.js` |
| **Google Analytics 4** | Static `landing/*.html` only | `G-54F5NE9Y0P`; consent default **denied** until updated |
| **Sentry (browser)** | Live marketing when `SENTRY_BROWSER_DSN` set | Injected via `includes/sentry.html` |

Workbench uses separate UI cache (`assure-127` on production `/health`) — not covered here.

---

## 9. Deploy, DNS & routing

```
Browser → getassureai.com
       → Cloudflare Worker (assure-marketing-proxy) → R2 (marketing)
       → /app, /api/* on apex → 302 → app.getassureai.com
       → Cloudflare Tunnel → EC2 :8765 (workbench + API)
```

| Host | Backend |
|------|---------|
| `https://getassureai.com/` | R2 static (Worker) |
| `https://getassureai.com/privacy` | R2 static |
| `https://getassureai.com/app` | 302 → `app.getassureai.com/app` |
| `https://app.getassureai.com/app` | EC2 Flask workbench |
| `https://staging.getassureai.com/` | Staging EC2 — **502** (disk full) |

**Marketing deploy (production):**

```bash
# Build locally only (no Cloudflare billing)
bash scripts/build-marketing-static.sh

# Upload changed files + Worker if needed (free-tier guards)
bash scripts/deploy-marketing-r2.sh
```

Requires Cloudflare auth + `assure-marketing-prod` R2 bucket. Uploads **skip unchanged files**; Worker redeploys only when JS/config change. See [runbooks/marketing-r2-free-tier.md](./runbooks/marketing-r2-free-tier.md).

**App deploy:** EC2 SSM + `scripts/aws/redeploy-app.sh` (GitHub push to staging/main paused per deploy-flow rule as of 2026-09-07).

**Legacy / alternate paths:**

| Mechanism | Branch / target |
|-----------|-----------------|
| `scripts/sync-webpage.sh` | Copies `landing/` → separate `webpage` checkout |
| `landing/wrangler.jsonc` | Worker `assure` on **workers.dev only** — do not attach apex |
| `docs/cloudflare-fix.md` | Workers Builds branch control (`webpage` vs app branch) |

---

## 10. Copy rules & companion docs

### 10.1 Hard rules (all public copy)

- **Closed / open wording:** *closed to the internet* / *open to the internet* — not local, cloud, or offline.
- **BYOK:** Keys stay on the user's machine / browser; Assure charges for the **workbench**, not model tokens.
- **Server processing:** Marketing footer and closed-mode tooltip disclose processing on Assure servers; do not claim zero server contact.
- **Do not claim** *court-defensible*, *zero retention*, or absolute *zero hallucination* on marketing.
- **Turkish:** **soru** for question; **sorun** only for problems.

### 10.2 Personas (copy bank)

See `landing/ICP.md` — hero trio plus six job-title archetypes. Live Stack A uses Coverage Counsel, Public Adjusters, Compliance Officers (Sep 8 reposition).

### 10.3 Objections / FAQ source

See `landing/objections.md` for approved answers (ChatGPT comparison, wrapper, PromptLayer, privacy).

### 10.4 Recent audits

| Audit | Scope |
|-------|-------|
| `docs/audits/2026-09-08-landing-truth-engine-copy.md` | Truth Engine reposition |
| `docs/audits/2026-09-09-landing-final-jobs-copy.md` | Final Jobs-style copy pass |
| `docs/audits/2026-09-09-landing-orphan-audit.md` | Orphan word fixes (390 viewport partial) |
| `docs/audits/2026-09-09-marketing-i18n-mixed-wording.md` | Locale trust title + plans.lead EN fallbacks fixed |
| This file — Final status 2026-09-09 evening | UI polish live: 2×2 trust grid, hero type, CTA/pricing/footer; R2 + Worker |
| `docs/runbooks/staging-launch-execution.md` | Phase 1–3 when staging EC2 is back |

Full viewport audit (390 · 768 · 1080 · 1440) per [WEBPAGE_AUDIT_STANDARD.md](./WEBPAGE_AUDIT_STANDARD.md) — **deferred**.

---

## 11. Change checklist

Before claiming webpage work done:

1. **Confirm stack** — Stack A (live marketing) vs `landing/` (draft)?
2. **Edit copy in `i18n.py`** for all 7 locales if changing live strings.
3. **Privacy body** — edit `prompt_matrix/content/privacy/*.html` if policy text changes.
4. **Bump** `LANDING_CSS` / `LANDING_JS` in `prompt_matrix/ui_cache.py`.
5. **Build + deploy R2** — `bash scripts/deploy-marketing-r2.sh` (or `--dry-run` first). See [marketing-r2-free-tier.md](./runbooks/marketing-r2-free-tier.md).
6. **Run** [WEBPAGE_AUDIT_STANDARD.md](./WEBPAGE_AUDIT_STANDARD.md) viewports: 390 · 768 · 1080 · 1440.
7. **Verify live** — `curl -sI https://getassureai.com/` and spot-check `/privacy`, `/architecture`.
8. **Log audit** — add row to `docs/audits/` if material visual/copy change.

---

## 12. Related documents

| Document | Role |
|----------|------|
| **[assure-ai-all-functions.md](./assure-ai-all-functions.md)** | Workbench master (§1.1 summarizes public routes) |
| **[product-status.md](./product-status.md)** | Deploy snapshot, what's live vs git (may lag marketing-on-R2) |
| **[WEBPAGE_AUDIT_STANDARD.md](./WEBPAGE_AUDIT_STANDARD.md)** | Visual/copy QA checklist |
| **[launch-checklist.md](./launch-checklist.md)** | Business positioning & launch gates |
| **[cloudflare-fix.md](./cloudflare-fix.md)** | Workers Builds / branch routing |
| **`landing/ICP.md`** | Personas & jobs |
| **`landing/objections.md`** | Sales / FAQ answers |
| **`docs/audits/`** | Per-change landing audit reports |

---

## Drift matrix (2026-09-09)

| Item | Stack A (live, R2) | Stack B (static `landing/`) |
|------|--------------------|----------------------------|
| Hero headline | Draft at the speed of AI. Verify with mathematical certainty. | Never Send an Unverified AI Draft… |
| Hero sub | Plausible AI is a liability. Before you export, Assure verifies every clause, every citation, and every financial figure against your source documents. | BYOK / zero retention badges in hero |
| Trust badge | Enterprise ready · BYOK · 7 languages (in `#trust`) | In hero |
| Trust cards | 2×2 grid (`.trust-arch-grid-four`) | Persona / simulator layout |
| Primary CTA | Request Enterprise Pilot | Launch Free Workbench |
| `/privacy` | Enterprise 12-section policy (`landing_privacy.html`) | Legacy static `privacy.html` |
| `/guide` | **404** | `guide.html` exists |
| Hosting | R2 + Worker | Not on apex |
| i18n | 7 locales via export + `landing-i18n.js` | English HTML (+ partial static patterns) |
| Analytics | Plausible | GA4 + consent denied default |

*When promoting static pages to production, port copy into `i18n.py` + Flask templates, export to R2 — do not only edit `landing/index.html`.*

---

*Generated as the webpage master inventory. For route handlers see `prompt_matrix/web.py`; for workbench see [assure-ai-all-functions.md](./assure-ai-all-functions.md).*
