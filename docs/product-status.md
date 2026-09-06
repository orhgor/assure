# Assure — full product status

**Date:** 2026-09-07
**GitHub Actions:** included cap is **3,000 minutes** (the prior 2,000 allotment was used up). Policy: [github-actions-minutes.md](./github-actions-minutes.md). Monitor tester notes at `/backstage` (SQLite inbox; Resend email is opt-in).

**Date:** 2026-09-06
**Git:** `staging` and `main` include merge `36e532a` (PR #6 + Trust & Clarity).
**Live EC2:** Staging health still `f04ec7f`; production still `bac3d40`. **GitHub Actions deploy failed** (account billing / spending limit) — code is on GitHub, images were not rebuilt.

## Current snapshot (2026-09-06)

| Environment | Git (origin) | Live `/health` |
| :--- | :--- | :--- |
| **Production** | `main` includes `36e532a` after this promote | `bac3d40` — Wow only until Actions billing is fixed and deploy succeeds |
| **Staging** | `36e532a` Merge PR #6 | `f04ec7f` — Trust & Clarity grid, not yet the stepper |

**Live URLs:** https://getassureai.com · https://staging.getassureai.com · workbench `/app`
**CI:** PR #6 `test` + `playwright-tests` **SUCCESS** (2026-09-06).

### Workbench (after this sprint)

| Item | Status |
| :--- | :--- |
| Sidebar Write / Draft / Polish / Sources / Analytics / Settings | ✅ Analytics is in-shell (`#view-analytics`), not a full navigation away |
| Document Lifecycle stepper (Write → Verify → Audit → Ship) | ✅ `workbench_stepper.js` |
| Single Accept & Dock | ✅ `#generate-accept-dock-phase` only |
| Provenance ⓘ drawer | ✅ `#provenance-panel-drawer.is-open` |
| Role switcher | ✅ Admin / Compliance / Developer / Executive |
| Wow effects | ✅ Optional layer — **not unified** with gutters/overlay/ⓘ |
| Active Works visual hierarchy | ❌ Not in this sprint |
| Full Audit as verify-only (no re-compile) | ❌ Still re-runs compile stream |

**Detail UI Q&A:** [workbench-ui-current-state.md](./workbench-ui-current-state.md) · **Function catalog:** [assure-ai-all-functions.md](./assure-ai-all-functions.md)

---

## Historical snapshot — 2026-09-04

**Date:** 2026-09-04
**Code check & test report:** [2026-09-04-edge-restructure-codecheck.md](./audits/2026-09-04-edge-restructure-codecheck.md)
**Decision:** **GO (production)** — app on `p4-account-wallet`; marketing on `webpage`
**Production (that day):** `https://getassureai.com/health` → `status: healthy`, `build_sha: 1b21f8b`
**Tests (that day):** **291 pytest passed, 1 failed** locally (`tests/test_adoption.py::test_resolve_lock_inference_model`).
**Git (that day):** **`1b21f8b`** on `p4-account-wallet`.

The sections below are the 2026-09-04 launch log. Prefer the **Current snapshot** above for what to demo now.

---

## One-line summary

Assure is **The Intellectual Compiler** — *Compile intent. Verify logic. Ship truth.* Upload a source document, compile a structured JDF draft with provenance and Z3 checks, refine surgically, and export a build artifact (DOCX with optional References). Marketing at **getassureai.com** (`/` landing, `/app` workspace, `/architecture` deep-dive). Zero-Risk Paste Test runs the real pipeline via `POST /api/sandbox/verify` with no persistence.

---

## Phase 4 — Workbench discovery (2026-09-04 evening)

Production **`1b21f8b`**. Draft model **`anthropic/claude-sonnet-4-5`**; Gemini ids **`gemini/gemini-3.6-flash`**. Compiler is BYOK via `litellm_kwargs_for`.

| Item | Status |
| :--- | :--- |
| Projects CRUD (`GET/POST /api/projects`, PATCH/DELETE, lock_count; cannot delete `default`) | ✅ Live (`4906d91`) |
| Sidebar Projects + `#view-projects` | ✅ Live |
| Unsaved-change confirm on project switch and leaving workspace | ✅ Live (`1b21f8b`) |
| Command-deck **Audit Manifest** — tooltip, modal, JSON download, success toast | ✅ Live (`1b21f8b`) |
| Settings Audit Manifest preview + same modal | ✅ Live |
| Compile/Refine layout (full-width CTAs, stream wrap hidden until compile) | ✅ Live |
| Canvas **right-click menu**: Edit / Revise / Re-prompt / Send for Revision | ⏳ Local only — wires existing `/inquire/stream` + `target_node_id` + canvas diff; not in `1b21f8b` |
| Staging `.env.staging` / `cloud_init.staging.sh` | ⏳ Untracked local files; not in git |

### Compiler models (do not document as Claude 3.5)

| Role | LiteLLM id |
| :--- | :--- |
| Draft / synthesis | `anthropic/claude-sonnet-4-5` |
| Gemini path | `gemini/gemini-3.6-flash` |
| DeepSeek | `deepseek/deepseek-chat` (and reasoner for Red-Hat where configured) |

---

## Phase 3 — Document Compiler cycle (2026-09-04)

Earlier launch-day commits on **`p4-account-wallet`**. GitHub Actions App Docker (ARM64) + EC2 SSM redeploy confirmed. Ancestry includes mobile layout + landing i18n (`fe247c8`), deploy hardening (`955ef45`), launch bundle (`d2f0019`…`6efd85e`). Current prod SHA is **`1b21f8b`**, not `7a1cbef`.

### Landing & marketing

| Item | Status |
| :--- | :--- |
| Intellectual Compiler manifesto landing (hero, tagline, 3-Act Engine, competitor section) | ✅ Live |
| Persona strip — 3 roles + “and more” link to `#personas` | ✅ Live (`f69b8cb`) |
| Static hero before/after (no auto-play animation); AST badges flex-wrap on mobile | ✅ Live (`fe247c8`) |
| `/architecture` subpage — shared header/footer with landing (logo, lang, mobile menu) | ✅ Live (`fe247c8`) |
| Landing i18n — `landing-i18n.js`, locale select, `data-i18n` on hero/nav/footer | ✅ Live (`fe247c8`) |
| Mobile nav — Menu toggle, full-width Launch CTA; value table card stack (≤768px, no h-scroll) | ✅ Live (`fe247c8`) |
| Zero-Risk Paste Test — `POST /api/sandbox/verify` + inline errors (no `alert`) | ✅ Live |
| Nav: Docs, Architecture, Sandbox, **Launch Workspace** → `/app` | ✅ Live |
| Turkish brand — **Zihinsel Derleyici**; tagline *Bilgiyi derleyin. Mantığı doğrulayın. Gerçeği teslim edin.* | ✅ Live (`7a1cbef`) |

### Workbench (Document Compiler UI)

| Item | Status |
| :--- | :--- |
| Sidebar **Compile** / **Refine** (i18n; view IDs: `view-generate`, `view-surgical`) | ✅ |
| Single `#compiler-status` pill: Idle / Processing / Verified / Issues Found | ✅ |
| Example intent chips (Executive Summary, Market Analysis, Risk Assessment) | ✅ 7 locales |
| Auto-resize textareas on compile/refine inputs | ✅ |
| Build Artifact export + **Compile Document** button | ✅ |
| Click-to-Refine with interactive element exclusion | ✅ |
| Onboarding 5-step tour incl. Red-Hat toggle + Refine nav (`onboarding.js`) | ✅ |
| Demo Red-Hat chip + toast flow (`#demo-redhat-btn`) | ✅ |
| Tooltips with mobile overflow fix; context-locking Refine copy | ✅ |
| Canvas skeleton (first load only), 30 ms node pop-in, Z3 lock animation (first verify) | ✅ |
| Mobile: hamburger, icon sidebar (tablet), floating command deck | ✅ |
| Mobile header grid — logo left, locale right; trust strip hidden <1024px | ✅ Live (`fe247c8`) |
| Default view **Compile**; stream wrap hidden until compile | ✅ Live |
| **Projects** sidebar + CRUD | ✅ Live (`4906d91`) |
| Unsaved guards (project switch / leave workspace / compiling) | ✅ Live (`1b21f8b`) |
| Command deck **Audit Manifest** (tooltip + modal + toast) | ✅ Live (`1b21f8b`) |
| Node context menu (Edit / Revise / Re-prompt / Send for Revision) | ⏳ In working tree (`assure-65` / `assure-56`), not deployed |
| Dark mode toggle | ❌ Removed from MVP |

### JDF engine

| Item | Status |
| :--- | :--- |
| Pydantic JDF models; `annotations.redhat` / `annotations.z3` on nodes | ✅ `prompt_matrix/models/jdf.py` |
| Progressive SSE draft pipeline (`compiled` → `audit_complete`) | ✅ `draft.py`, `inquire_stream.py` |
| SSE reconnect (12 s idle, 3 retries, exponential backoff) | ✅ `inquire_client.js`, `generate.js` |
| Provenance list schema, cite parsing, citation badges, `workspace_settings` | ✅ |
| DOCX References section (`include_citations` param) | ✅ `docx_ast.py` |
| Version history + comments API | ✅ |
| Language guards on LLM calls | ✅ `language_guard.py` + tests |
| Z3 solver pool (max 5, reset on reuse) | ✅ `truth_engine.py` |
| LiteLLM 429 exponential backoff | ✅ `litellm_runner.py`, `cost_governance.py` |

### Backend / infra

| Item | Status |
| :--- | :--- |
| **Edge PDF processing** — Cloudflare Worker + R2 (`worker/`), unpdf fast path, Textract fallback, auto-delete | ✅ Implemented (deploy + secrets manual) |
| **POST /api/substrate** — edge ingest into `substrates` + `substrate_vault` | ✅ |
| **Staging / production separation** — GitHub Environments, `cd-staging.yml`, `docker-compose.staging.yml` | ⚠️ Workflow exists; local staging env files untracked |
| **Rate limiting** — Worker KV (20/IP/min), Flask-Limiter, `daily_compile_limits` (100/project/day) | ✅ Deploy pending |
| AWS Textract Substrate Vault (single-page guard, image vs PDF routing) | ✅ (legacy direct EC2 upload) |
| Textract throttling retry; reject extracted text ≤ 10 chars | ✅ |
| `poppler-utils` in Dockerfile | ✅ |
| SQLite WAL + `busy_timeout` + locked retry (max 3) | ✅ `connection.py` |
| IAM Textract permissions | ✅ `iam-policy-assure-deploy.json` |
| ARM64 GHCR build (`ubuntu-24.04-arm`) | ✅ `.github/workflows/app-docker.yml` |
| EC2 fallback build via `DOCKER_DEFAULT_PLATFORM=linux/arm64` | ✅ `redeploy-app.sh` |

### Deploy hardening (`955ef45`)

| Item | Status |
| :--- | :--- |
| Deploy mutex (`flock` on `/tmp/assure-redeploy.lock`) — no overlapping SSM redeploys | ✅ |
| Stale container cleanup before recreate (`compose rm -f`, orphan name purge) | ✅ |
| GHCR pull retries (5 attempts, exponential backoff) | ✅ Verified on deploy |
| Disk guard — refuse EC2 fallback build if `/` < 3 GB free | ✅ |
| Strict health exit — redeploy fails if `/health` `ok` is false | ✅ |
| Health-based auto-heal cron (every 5 min, loopback `:8765/health`) | ✅ Live on EC2 |
| `scripts/aws/install-auto-heal-cron.sh` | ✅ |
| Redeploy script tests | ✅ `tests/test_redeploy_hardening.py` (3 tests) |
| Audit log + system_metrics SQL runbook | ✅ [post-launch-ops.md](./post-launch-ops.md) |
| Plausible / tester free-text feedback / Sentry | ✅ Live — Plausible custom embed on prod Flask pages (dashboard domain `app.getassureai.com`; embed unchanged); feedback modal + `POST /api/tester-feedback`; Sentry when `SENTRY_DSN` set |
| Friendly error handlers (404/429/500) | ✅ JSON for `/api/*`, HTML `error.html` elsewhere |
| Tester Help link (`ASSURE_HELP_URL`) | ✅ Wired — defaults to `mailto:feedback@getassureai.com` |

### Tests

| Item | Result |
| :--- | :--- |
| Full suite (no e2e) | ⚠️ **291 passed, 1 failed** (`uv run pytest tests/ --ignore=tests/e2e -q`, 2026-09-04) — `test_resolve_lock_inference_model` |
| Coverage areas | sandbox, draft, textract, jdf annotations, provenance export, language guard, connection retry, redeploy hardening |

---

## Product identity

| Layer | Name | Role |
| :--- | :--- | :--- |
| **Product** | Assure | Document Compiler workbench (`assure --web`). Compile → refine → export build artifacts. |
| **Engine** | PEM (Prompt Engineering Matrix) | JDF AST, progressive SSE draft, Z3/redhat annotations, provenance, DOCX export. |
| **CLI** | `pem` / `assure` | Same entry point. `pem --ci`, `pem eval`, `pem monitor`, MCP server. |
| **Public site** | getassureai.com | Marketing, compiler preview, waitlist. Workspace at `/app`. |
| **GitHub `orhgor/assure`** | App + webpage | Branch `p4-account-wallet` for app deploy; `webpage` for static sync. |

---

## Launch decision

All hard gates for a soft launch are satisfied:

| Gate | Status |
| :--- | :--- |
| `getassureai.com` + `www` → Cloudflare | ✅ 200 |
| Landing, `/app`, `/architecture` | ✅ 200 |
| Production health | ✅ `healthy` @ `1b21f8b` |
| Zero-Risk Paste Test (sandbox API) | ✅ Wired |
| 7 languages on workbench + landing marketing strings | ✅ i18n in all locales |
| Landing page + GA4 + privacy disclosure | ✅ Live |

**Not ready for:** promising a download button, implying Assure pays API costs, or wide promotion before P1 fixes below.

---

## Business model (BYOK)

Assure is **bring your own key (BYOK)**. Key facts:

| Who pays | What |
| :--- | :--- |
| **User → provider** | Token usage (Gemini, DeepSeek, Claude, Kimi, Ollama) |
| **User → Assure** | Workbench subscription (Free $0, Pro $19/mo) — limits and features, not model calls |

| Tier | Price | Checks/day | Models | History | Export | Diff panel |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Free** | $0 | 5 | Gemini Flash grounding | 7 days | Blocked | Hidden |
| **Pro** | $19/mo | 100 | Claude 3.5 + Gemini 1.5 Pro consensus | Full SQLite | Markdown, HTML, PDF, Prompty | Visible |
| **Team** | Install flag | Unlimited on this machine | All (max 8) | Full | All | Visible |

**Not on PyPI yet.** Install: `./scripts/install.sh` then `assure --web`.

---

## What is live today

### Public website

- **URL:** https://getassureai.com/ (EC2 on `p4-account-wallet`, Cloudflare tunnel)
- **Pages:** IC manifesto landing, `/architecture`, Zero-Risk Paste Test, pricing, install, privacy, terms, about
- **Sandbox:** `POST /api/sandbox/verify` — real compile pipeline, no persistence; inline error banner on mobile
- **Nav:** Shared header (SVG logo, lang select, mobile Menu); Docs, Architecture, Sandbox, Launch Workspace → `/app`
- **Locales:** Landing + workbench in en, es, zh, fr, de, ja, tr (TR brand: Zihinsel Derleyici)
- **Analytics:** GA4 on HTML pages; disclosed on `/privacy`
- **Download buttons:** Disabled — no public binary URL

### Workbench

- **URL:** https://getassureai.com/app
- **Sidebar:** Compile / Refine / Projects / Library / Settings (i18n)
- **Command deck:** `#compiler-status` pill, **Export** (DOCX), **Audit Manifest** (JSON)
- **Canvas:** click-to-Refine; right-click menu local-only until next deploy
- **Export:** DOCX + Audit Manifest JSON (`audit_manifest_{project}_{date}.json`)
- **Onboarding:** 5-step tour; demo Red-Hat chip
- **Substrate vault:** AWS Textract for PDF/image upload

---

## Known defects and blockers

### P1 — fix before wide promotion

| ID | Issue | Status |
| :--- | :--- | :--- |
| **B1** | Thumbs UI missing | ✅ Fixed |
| **B2** | Model attribution ignores failed drafts | ✅ Fixed |
| **B3** | Clerk gate surprise | ⚠️ Open — document or add loopback bypass |

### P2 — fix before v1.0

| ID | Issue | Status |
| :--- | :--- | :--- |
| P2-1 | Token readout resets to 0 after Send | ⚠️ Open |
| P2-2 | Grounded copy says "0 claims checked" | ✅ Fixed |
| P2-3 | Generic bold labels painted as inferred | ⚠️ Open |
| P2-4 | File path in Extra context — no highlight legend | ⚠️ Open |
| P2-5 | Export `Content-Type` doubled charset | ⚠️ Open |
| P2-6 | GA4 EU consent / Consent Mode v2 | ⚠️ Decision needed |

### Infra notes

| Item | Status |
| :--- | :--- |
| SQLite backup cron | ⚠️ `never_run` in health check |
| EC2 disk after image pull | ⚠️ **3.51 GB** free on last health (guard refuses fallback build under 3 GB) |
| `test_resolve_lock_inference_model` | ⚠️ Expects `gemini-1.5-pro`; runtime is `gemini-3.6-flash` |
| Landing page i18n | ✅ Marketing strings in 7 locales (`fe247c8`); extend architecture body if needed |
| GHCR image race on manual redeploy | ✅ Mitigated — pull retries in `redeploy-app.sh` |

---

## Architecture (short)

```
Source document (PDF/image via Textract substrate)
    ↓
JDF AST compile (Pydantic models, node annotations: redhat / z3)
    ↓
Progressive SSE draft (compiled → audit_complete)
    ↓
Provenance + citation badges; workspace_settings
    ↓
Refine (click or right-click: Edit / Revise / Re-prompt / Send for Revision)
    ↓
Export DOCX and/or Audit Manifest JSON
```

- **Sandbox:** `POST /api/sandbox/verify` — same pipeline, zero persistence
- **Deploy:** EC2 `t4g.small` ARM64; GHCR pull via SSM; hardened `redeploy-app.sh`
- **Auto-heal:** Cron checks loopback `/health` every 5 min; restarts compose if down
- **MCP:** `pem` server for Cursor (`pem_compile`, `pem_combine`, `swarm_start`, etc.)

---

## Commit timeline (launch bundle)

| Commit | Description |
| :--- | :--- |
| `6c225ae` | Document compiler workbench, sandbox API, Textract |
| `192eba3` | Intellectual Compiler branding |
| `6b3f4af` | Visual polish, audit gate, timeouts |
| `307cdaf` | Launch bundle — example chips, language guards, UI polish |
| `71b7024` | Production hardening — SSE reconnect, SQLite retry, Z3 pool |
| `afea575` | ARM64 EC2 fallback build fix |
| `272be96` | Example-chip i18n, auto-resize, SSE reconnect retry |
| `d2f0019` | Red-Hat/Refine onboarding, demo chip, tooltips |
| `6efd85e` | Docs: verify production deploy |
| **`955ef45`** | **Deploy hardening — mutex, pull retries, stale cleanup, auto-heal** |
| **`fe247c8`** | **Mobile layout — landing nav/table, architecture header, workbench header grid, landing i18n** |
| **`7a1cbef`** | **TR brand tagline — Bilgiyi derleyin. Mantığı doğrulayın. Gerçeği teslim edin.** |
| `2da0cf8` | Compiler BYOK keys + Claude Sonnet 4.5 |
| `9b40ba2` / `2da0cf8` | Gemini 3.6 Flash routing |
| `4906d91` | Projects CRUD UI + API |
| **`1b21f8b`** | **Unsaved guards, layout, Audit Manifest workbench UX — current production** |

---

## Recommended next actions

### Immediate

1. ~~Verify EC2 redeploy~~ **Done** — `build_sha: 1b21f8b`, healthy
2. ~~Deploy hardening~~ **Done** — `955ef45` live
3. ~~Mobile layout + landing i18n~~ **Done** — `fe247c8` live
4. ~~Turkish brand copy~~ **Done** — Zihinsel Derleyici (`7a1cbef`)
5. ~~Audit Manifest + unsaved guards on prod~~ **Done** — `1b21f8b`
6. Commit + deploy node context menu (`assure-65` / `assure-56`)
7. Fix `test_resolve_lock_inference_model` to `gemini-3.6-flash`
8. Schedule SQLite backup (`backup: never_run`); watch disk (~3.5 GB)
9. Smoke-test on mobile (390px): landing nav, value table, workbench header, `/architecture` header

### Launch sequence

10. Product Hunt, LinkedIn, X, Reddit — use UTM links in [launch-checklist.md](./launch-checklist.md)
11. Keep download buttons disabled until a real binary URL exists

### Post-launch

12. ~~Landing page i18n~~ **Done** (`fe247c8`); extend architecture body copy i18n if needed
13. PyPI publish when ready
14. Desktop build and signed release
15. EU consent decision for GA4
16. Free-tier live UI verification under real quota (5/day)

---

## Related documents

| Document | Purpose |
| :--- | :--- |
| [launch-checklist.md](./launch-checklist.md) | Item-by-item pass/fail with probe notes |
| [../landing/ICP.md](../landing/ICP.md) | Personas and jobs |
| [../landing/objections.md](../landing/objections.md) | Sales objection answers |
| [../prompt_matrix/PEM.md](../prompt_matrix/PEM.md) | Engine and swarm log |
| [../landing/launch/](../landing/launch/) | Launch post drafts |

---

*Updated 2026-09-04 after production `1b21f8b` (Audit Manifest + unsaved guards) and local node context menu.*
