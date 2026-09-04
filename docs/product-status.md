# Assure — full product status

**Date:** 2026-09-04
**Code check & test report:** [2026-09-04-codecheck-and-test-report.md](./audits/2026-09-04-codecheck-and-test-report.md) (local test snapshot + full audit plan)
**Decision:** **GO** — mobile layout + landing i18n live on `p4-account-wallet`
**Production:** `https://getassureai.com/health` → `status: healthy`, `build_sha: cc34589`, UI `assure-53` / `assure-42`, landing CSS/JS `44` / `32`, ~7 GB disk free
**Tests:** **266 passing** (`uv run pytest tests/ -q`)
**Container:** `assure-assure-app-1` on EC2 — GHCR pull + hardened redeploy script (`scripts/aws/redeploy-app.sh`)
**Git:** `cc34589` on `p4-account-wallet` — post-launch ops (Plausible, tester feedback, optional Sentry)
**Detail checklist:** [launch-checklist.md](./launch-checklist.md) · **Post-launch ops:** [post-launch-ops.md](./post-launch-ops.md)

---

## One-line summary

Assure is **The Intellectual Compiler** — *Compile intent. Verify logic. Ship truth.* Upload a source document, compile a structured JDF draft with provenance and Z3 checks, refine surgically, and export a build artifact (DOCX with optional References). Marketing at **getassureai.com** (`/` landing, `/app` workspace, `/architecture` deep-dive). Zero-Risk Paste Test runs the real pipeline via `POST /api/sandbox/verify` with no persistence.

---

## Phase 3 — Document Compiler cycle (2026-09-04)

Latest commit **`7a1cbef`** on branch **`p4-account-wallet`**. GitHub Actions App Docker (ARM64) + EC2 SSM redeploy confirmed. Recent ancestry: mobile layout + landing i18n (`fe247c8`), deploy hardening (`955ef45`), launch bundle (`d2f0019`…`6efd85e`).

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
| Default view **Compile** (not Projects); stream collapses after compile | ✅ Live (`fe247c8`) |
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
| **Staging / production separation** — GitHub Environments, `cd-staging.yml`, `docker-compose.staging.yml` | ✅ |
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
| Full suite | ✅ **266 passed** (`uv run pytest tests/ -q`, 2026-09-04) |
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
| Production health | ✅ `healthy` @ `7a1cbef` |
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
- **Sidebar:** Compile / Refine with i18n labels
- **Command deck:** `#compiler-status` pill + audit gate integration
- **Export:** Build Artifact (DOCX with optional References)
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
| Landing page i18n | ⚠️ Mostly EN-only (workbench is 7-locale) |
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
Refine (click-to-refine, surgical view) → Build Artifact export (DOCX + References)
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

---

## Recommended next actions

### Immediate

1. ~~Verify EC2 redeploy~~ **Done** — `build_sha: 7a1cbef`, healthy
2. ~~Deploy hardening~~ **Done** — `955ef45` live
3. ~~Mobile layout + landing i18n~~ **Done** — `fe247c8` live
4. ~~Turkish brand copy~~ **Done** — Zihinsel Derleyici (`7a1cbef`)
5. Smoke-test on mobile (390px): landing nav, value table, workbench header, `/architecture` header
6. Schedule SQLite backup (`backup: never_run` in health)

### Launch sequence

7. Product Hunt, LinkedIn, X, Reddit — use UTM links in [launch-checklist.md](./launch-checklist.md)
8. Keep download buttons disabled until a real binary URL exists

### Post-launch

9. ~~Landing page i18n~~ **Done** (`fe247c8`); extend architecture body copy i18n if needed
10. PyPI publish when ready
11. Desktop build and signed release
12. EU consent decision for GA4
13. Free-tier live UI verification under real quota (5/day)

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

*Updated 2026-09-04 after mobile layout + TR tagline deploy (`7a1cbef`) verified on production.*
