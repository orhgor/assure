# Assure AI — All Functions

**Production:** v1.4.0 (`ee6a9ee`, UI cache `assure-96`) — https://getassureai.com/app
**Staging:** v1.5 (`bc7515c`, UI cache `assure-97`) — https://staging.getassureai.com/app
**Stack:** Flask · Vanilla JS · TipTap · SQLite · JDF (JSON Document Format)
**Last updated:** 2026-09-08

This document inventories every major product function as implemented in the codebase. It is a reference for demos, onboarding, and release planning—not a marketing brochure.

---

## Development status (what shipped and runs smoothly)

### v1.4 — The Welcoming Scalpel ✅ production

| Area | Status | Notes |
|------|--------|-------|
| New Project Wizard (3-step) | ✅ Live | Templates, sources, prompt → project + JDF seed |
| SQLite prompt library | ✅ Live | CRUD `/api/prompts`, vault UI |
| Project templates API | ✅ Live | 5 seeded templates |
| First-compile coachmark | ✅ Live | One-time overlay after wizard |
| Floating action bar (Polish) | ✅ Live | Rewrite / Ground / History / Delete |
| Status bar + toast micro-UX | ✅ Live | Compiler status, non-blocking toasts |
| Warm i18n copy (7 locales) | ✅ Live | “Assemble”, “Polish”, etc. |
| **Tests** | ✅ Green | CI pytest + Playwright on v1.4 merge |

**Production tag:** `v1.4.0` · merge `ee6a9ee`

---

### v1.5 — Enterprise Compliance & CI Integration ✅ staging

| Feature | Status | Verification |
|---------|--------|--------------|
| **CI/CD drift endpoint** | ✅ Shipped | `POST /api/projects/<id>/drift-check` — config vs vault policy, Z3-backed findings |
| **Sign-off workflow** | ✅ Shipped | Document + node sign-off routes, list API, UI panel in command deck **More** |
| **Document locking** | ✅ Shipped | Lock version + content hash; `PUT /jdf` → 409 when locked; lock banner + disabled edits |
| **JSON/YAML import** | ✅ Shipped | `POST /import-config` → JDF section; UI upload in **More** |
| **Audit report PDF bundle** | ✅ Shipped | `export?format=audit-pdf` or `format=pdf&audit_bundle=1` — TOC, Z3, Red-Hat, sign-offs, lock hash |
| **Global user activity log** | ✅ Shipped | `user_activity_log` table + middleware; `GET /api/audit-log` |
| **OMP Decision Log UI** | ✅ Shipped | Timeline from `/api/omp/memories?tags=project:{id}` |
| **Multi-page PDF vault** | ✅ Shipped | Up to 50 pages; page markers in extracted text; Z3 reasons cite page number |
| **All Functions reference** | ✅ Doc | This file (`docs/assure-ai-all-functions.md`) |
| **Tests (pytest)** | ✅ Green | 422+ pass (incl. 8 new v1.5 test modules) |
| **Tests (Playwright)** | ✅ Green locally | 22/22 pass; lock UI uses API + reload (no `refreshLockState` race) |
| **Deploy staging** | ✅ Live | `staging.getassureai.com/health` → `assure-97`, healthy |

**Branch:** `feat/v1.5-enterprise-compliance` → merged **`staging`** @ `c5d979a` (+ lock-test fixes `0629e66`, `bc7515c`)

**Not in v1.5 (deferred v2.0):** semantic conflict detection (NLI), SSO/RBAC, project API keys for drift CI.

**CI note:** pytest job green on every staging push. Playwright job can flake on slow compile timeouts (`status_bar`, `surgical_refine`, `version_slider`) under CI load—unrelated to v1.5 code; lock UI test stabilized.

---

## Table of contents

1. [Product surfaces](#1-product-surfaces)
2. [REST & SSE API](#2-rest--sse-api)
3. [Workbench UI](#3-workbench-ui)
4. [Verification & truth engine](#4-verification--truth-engine)
5. [Export & import](#5-export--import)
6. [Database persistence](#6-database-persistence)
7. [Auth, editions & billing](#7-auth-editions--billing)
8. [AI targets, intents & workflows](#8-ai-targets-intents--workflows)
9. [OMP & developer MCP](#9-omp--developer-mcp)
10. [Observability & safeguards](#10-observability--safeguards)
11. [JavaScript modules map](#11-javascript-modules-map)
12. [Known limitations](#12-known-limitations)

---

## 1. Product surfaces

### 1.1 Public / marketing

| Route | Function |
|-------|----------|
| `GET /` | Landing page — hero, pipeline, personas, glossary |
| `GET /architecture` | How-it-works architecture page |
| `GET /pricing` | Pricing + Stripe checkout entry |
| `GET /privacy`, `/terms`, `/about` | Legal and about pages |
| `POST /api/waitlist` | Email waitlist (Supabase when configured) |
| `POST /api/sandbox/verify` | **Zero-Risk Paste Test** — lock inference + Z3 + Red-Hat, no login, no persistence |

**Landing client features:** interactive demo tabs (`landing-demo.js`), sandbox verify, “Send to Workbench” handoff to `/app`.

### 1.2 Authenticated app

| Route | Function |
|-------|----------|
| `GET /app` | Main JDF workbench (compose pane) |
| `GET /compose` | Redirect → `/app` |
| `GET /history` | Previous work / execution history |
| `GET /learn` | Learn-a-class-from-prompt |
| `GET /library` | Saved prompt classes |
| `GET /connect` | Provider API key setup |
| `GET /signin`, `/signup`, `/signout` | Clerk or self-hosted auth |
| `GET /account` | Account profile |
| `GET /account/usage` | Credits and usage |

---

## 2. REST & SSE API

### 2.1 Core (`web.py`)

#### Auth & account

| Method | Endpoint | Function |
|--------|----------|----------|
| GET | `/api/auth/config` | Auth mode and signed-in state |
| GET | `/api/auth/me` | Current user id + email |
| POST | `/api/auth/session` | Clerk token → session |
| POST | `/api/auth/logout` | End session |
| POST | `/api/account/delete` | Delete account |
| GET | `/api/account/export` | Export user data (JSON) |
| GET/POST | `/api/settings` | User keys and preferences |

#### Billing & usage

| Method | Endpoint | Function |
|--------|----------|----------|
| GET | `/api/subscription` | Subscription tier |
| POST | `/api/billing/checkout` | Stripe checkout URL |
| POST | `/api/billing/portal` | Stripe customer portal |
| POST | `/api/webhooks/stripe` | Stripe webhooks |
| GET | `/api/usage` | Credit wallet + transactions |

#### i18n & health

| Method | Endpoint | Function |
|--------|----------|----------|
| GET | `/api/i18n` | UI strings (7 locales) |
| GET | `/api/health` | Basic health |
| GET | `/health` | Extended health (disk, backup, OMP, UI versions) |

#### PEM compose engine (Q&A flow)

| Method | Endpoint | Function |
|--------|----------|----------|
| GET | `/api/catalog` | Targets, intents, personas, edition |
| GET | `/api/status` | Provider key readiness |
| POST | `/api/keys` | Save provider API key |
| POST | `/api/intent` | Auto-detect intent from task |
| POST | `/api/preview` | Compile prompt preview (no model) |
| POST | `/api/render` | Run workflow: single / ensemble / redhat |
| POST | `/api/copy` | Clipboard ack |
| POST | `/api/export` | Export class as cursorrules / mdc / fabric / dspy |
| POST | `/api/feedback` | Thumbs up/down on run |
| POST | `/api/tester-feedback` | Anonymous tester feedback |

#### History & library

| Method | Endpoint | Function |
|--------|----------|----------|
| GET | `/api/history`, `/api/history/search` | List/search past runs |
| GET | `/api/history/diff` | Diff between run hashes |
| GET | `/api/history/<id>` | Single history item |
| DELETE | `/api/history/<id>`, `/api/history` | Delete one / clear all |
| GET | `/api/history/<id>/export` | Export run (md, html, prompty, pdf, plain) |
| GET | `/api/library` | Saved classes + prompts |
| POST | `/api/library/classes` | Create class |
| POST | `/api/library/prompts` | Save prompt under class |
| DELETE | `/api/library/classes/<id>`, `/api/library/prompts/<id>` | Delete |
| POST | `/api/library/classes/<id>/versions` | Snapshot class version |
| POST | `/api/library/classes/<id>/rollback` | Rollback N versions |
| GET | `/api/library/classes/<id>/diff` | Class version diff |
| POST | `/api/learn` | Extract class structure from prompt (no API call) |

#### Upload validation

| Method | Endpoint | Function |
|--------|----------|----------|
| POST | `/api/upload/validate` | Validate attachment size + PDF page count |

---

### 2.2 Projects & documents (`routers/`)

#### Projects

| Method | Endpoint | Function |
|--------|----------|----------|
| GET | `/api/projects` | Dashboard list (status: drafting / verifying / audited / ready_to_export) |
| POST | `/api/projects` | Create (title, template_id, prompt, source_md, prompt_id) |
| PATCH | `/api/projects/<id>` | Rename |
| DELETE | `/api/projects/<id>` | Delete (not `default`) |
| GET/PUT | `/api/projects/<id>/files` | `source_md` + last compiled manifest |
| GET/PUT | `/api/projects/<id>/settings` | Project settings (e.g. show_citations) |

#### JDF document

| Method | Endpoint | Function |
|--------|----------|----------|
| GET | `/api/projects/<id>/history` | JDF revision list |
| GET | `/api/projects/<id>/jdf` | Fetch document (optional `?version=`) |
| PUT | `/api/projects/<id>/jdf` | Save revision (full doc, node patch, optimistic lock → 409; **document lock → 409**) |
| POST | `/api/projects/<id>/import-pdf` | PDF → JDF tree |
| POST | `/api/projects/<id>/import-config` | **v1.5** JSON/YAML → JDF config section |
| GET | `/api/projects/<id>/nodes/<node_id>/history` | Per-node revision list |
| POST | `/api/projects/<id>/nodes/<node_id>/restore` | Restore node snapshot |

#### Draft / compile (SSE)

| Method | Endpoint | Function |
|--------|----------|----------|
| POST | `/api/projects/<id>/draft/stream` | Stream: draft → compile → locks → Z3 → optional Red-Hat |
| POST | `/api/projects/<id>/draft/redhat/stream` | Red-Hat-only on existing draft |

**SSE events:** `token`, `status`, `compiled`, `verified`, `usage`, `audit_complete`, `error`, `[DONE]`

#### Inquire / refine (SSE)

| Method | Endpoint | Function |
|--------|----------|----------|
| POST | `/api/projects/<id>/inquire/stream` | Surgical node rewrite + Z3 + optional Red-Hat |

#### Node operations

| Method | Endpoint | Function |
|--------|----------|----------|
| POST | `/api/projects/<id>/refine-node` | Non-streaming surgical rewrite |
| POST | `/refine-node` | Alias (project_id in body) |
| POST | `/api/projects/<id>/nodes/<node_id>/ground` | Ground node: `auto`, `search`, `llm` |

#### Sources (Substrate Vault)

| Method | Endpoint | Function |
|--------|----------|----------|
| POST | `/api/substrate` | Edge/worker text ingest |
| POST | `/api/projects/<id>/substrate/upload` | Upload PDF/image → Textract (**up to 50 pages** on PDF) |
| GET | `/api/projects/<id>/substrate` | List vault files + claim counts |
| DELETE | `/api/projects/<id>/substrate/<file_id>` | Remove file |
| PATCH | `/api/projects/<id>/substrate/<file_id>` | Toggle **included** for compile grounding |

#### Export & adoption

| Method | Endpoint | Function |
|--------|----------|----------|
| GET | `/api/projects/<id>/export` | **docx**, **md**, **html**, **pdf**, **audit-pdf**, **json** |
| POST | `/api/projects/<id>/infer-locks` | Extract lock candidates from sources |
| POST | `/api/projects/<id>/apply-locks` | Apply locks → truth ledger |
| GET | `/api/projects/<id>/export-audit` | JSON audit manifest |
| POST | `/api/projects/<id>/drift-check` | **v1.5** CI config vs policy drift (Z3 findings) |
| POST | `/api/projects/<id>/lock` | **v1.5** Lock current JDF version + content hash |
| GET | `/api/projects/<id>/lock` | **v1.5** Lock status for project/version |
| GET | `/api/projects/<id>/sign-offs` | **v1.5** List sign-offs |
| POST | `/api/projects/<id>/sign-off` | **v1.5** Document-level sign-off |
| POST | `/api/projects/<id>/nodes/<node_id>/sign-off` | **v1.5** Node-level sign-off |
| GET | `/api/audit-log` | **v1.5** User activity log (scoped by `user_id`) |

#### Conflicts & comments

| Method | Endpoint | Function |
|--------|----------|----------|
| GET | `/api/projects/<id>/conflicts` | Keyword-level vault disagreements |
| GET/POST/DELETE | `/api/projects/<id>/comments` | Node-scoped comments |

#### v1.4 — Templates & prompt library

| Method | Endpoint | Function |
|--------|----------|----------|
| GET | `/api/project-templates` | Wizard templates (compliance-memo, research-paper, blank, contract-review, blog-post) |
| GET/POST/PUT/DELETE | `/api/prompts`, `/api/prompts/<id>` | SQLite prompt library |

#### Sandbox & OMP

| Method | Endpoint | Function |
|--------|----------|----------|
| POST | `/api/sandbox/verify` | Public paste-test verification |
| POST | `/api/omp/remember` | Store OMP memory |
| GET | `/api/omp/recall` | Recall by key |
| GET | `/api/omp/memories` | List memories by tags |

---

## 3. Workbench UI

### 3.1 Navigation

- **Top tabs:** Compose · Previous work · Learn · Classes
- **Sidebar:** Write (projects) · Draft (Assemble) · Polish (surgical) · Sources (vault) · Settings
- **Resizable panes**, keyboard shortcuts (`?` sheet), 7-locale i18n
- **Safeguards:** single-tab guard, unsaved-changes confirm, session compile limit, mobile hint
- **Onboarding:** 5-step tour + first-compile coachmark (v1.4)

### 3.2 Write (Projects)

- Active Works dashboard with status vitals
- **+ New** → 3-step **New Project Wizard** (template → sources → prompt)
- Inline quick-create, rename, delete, switch project

### 3.3 Draft (Assemble)

- Intent textarea + model picker (Gemini / Claude / DeepSeek)
- **✨ Assemble** — SSE compile pipeline
- **Full Audit** — compile + Z3 + Red-Hat in one pass
- Lock-numbers toggle, stream preview, node/lock summary
- **Accept & Dock**, Re-assemble, Discard
- Inferred locks checklist, Red-Hat preview, preflight gate
- Quick actions: duplicate node, split section, merge with next
- Recent prompts history

### 3.4 Polish (Surgical)

- Select canvas node → refine with intent (SSE inquire stream)
- Stress Test toggle, Run Red-Hat on full document
- Revision diff panel (accept/reject), dock streamed draft
- Z3 truth error detail, Red-Hat findings panel

### 3.5 JDF Canvas

- TipTap rich editor ↔ JDF AST sync
- **Confidence overlay** — Verified / Uncertain / Hallucination spans
- Version history slider + restore + **🔒 Lock version** (v1.5)
- **Lock banner** when document version is locked (v1.5)
- **Floating action bar:** ✏️ Rewrite · 🔍 Ground · 📜 History · 🗑️ Delete
- **Surgical popover:** Polish, Ground (auto/search/llm), History, Delete
- **Node menu:** Edit, Revise, Re-prompt, Send for Revision, Node history, Red-Hat on node
- Node revision modal, node conflict modal, source conflict modal
- Citation drawer, Audit Report appendix on canvas
- **Argument Spine** and **Document Structure** trees
- Status bar: ✅ Verified · ⚠️ risks · ⚡ Cached · Working…

### 3.6 Sources (Substrate Vault)

- Search, upload (PDF/PNG/JPG/TIF), drag-and-drop
- Toggle included per file, delete, claim counts per source

### 3.7 Command deck (footer)

- Compiler status + version slider + **lock control** (v1.5)
- **Download:** Word · Markdown · HTML · PDF · **Audit PDF bundle** (v1.5)
- **Audit Report** modal + JSON export
- **More →** Upload PDF · **Import JSON/YAML** · **Sign-Off** · **Decision Log** (v1.5)
- Stop stream

### 3.8 Settings & library

- Settings overlay: citations in DOCX, link to Connect
- Vault prompts grid (CRUD via `/api/prompts`)
- Connect page: paste Claude / Gemini / DeepSeek / Kimi keys

### 3.9 Legacy compose (still wired)

- Quick Answer / Compare & Validate / Refine & Verify workflows
- Personas, audience selector, file attach, live preview
- **Get my answer** → `/api/render`, grounded span highlighting, save to library

---

## 4. Verification & truth engine

| Function | Description |
|----------|-------------|
| **Z3 / Math Check** | Locks metrics in `truth_ledger`; validates claims → node annotations |
| **Red-Hat / Stress Test** | Adversarial DeepSeek critique on nodes or full document |
| **Lock inference** | DeepSeek extracts metric candidates from sources; user accepts → ledger |
| **Confidence overlay** | Span scores: green (>0.8), yellow (0.4–0.8), red (<0.4) + “Why this score” |
| **Source conflicts** | Keyword-level numeric/date/term scan across included vault files |
| **Audit manifest** | JSON export: Z3 logs, Red-Hat logs, provenance, truth ledger, JDF body |
| **Audit PDF bundle** | **v1.5** Formal compliance PDF: document, Z3 spans, Red-Hat, sign-offs, lock hash, build SHA |
| **Drift check** | **v1.5** Compare structured config to vault policy for CI pipelines |
| **Preflight gate** | Blocks dock until Math Check + Stress Test pass (configurable flow) |

---

## 5. Export & import

| Context | Formats |
|---------|---------|
| JDF project | docx, md, html, pdf, **audit-pdf**, json |
| History run | markdown, html, prompty, pdf, plain |
| Saved class | cursorrules, mdc, fabric, dspy (Pro+) |
| Audit | JSON manifest + **PDF compliance bundle** (v1.5) |
| Import | PDF → JDF; **JSON/YAML → JDF section** (v1.5); vault upload |
| Account | JSON user export |

DOCX optional **References / citations** section via project setting `show_citations`.

---

## 6. Database persistence

**Schema version:** 17 (SQLite)

| Table | Purpose |
|-------|---------|
| `projects` | Title, owner, source_md, last_compiled_json |
| `jdf_revisions` | Versioned JDF trees + truth_ledger |
| `node_revisions` | Per-node snapshot history |
| `substrate_vault` | Uploaded source files + Textract text |
| `source_conflicts` | Keyword conflict records |
| `project_comments` | Node comments |
| `pipeline_cache` | Compile/Red-Hat cache (OMP-indexed, TTL) |
| `project_templates` | **v1.4** wizard templates |
| `prompts` | **v1.4** prompt library |
| `sign_offs` | **v1.5** Document/node reviewer approvals |
| `document_locks` | **v1.5** Version lock + content hash |
| `user_activity_log` | **v1.5** Global API activity audit |
| `token_ledger_entries` | Usage tracking |
| `project_budgets` | Per-project token limits |
| `audit_log` | Structured request audit trail (export/JDF/substrate events) |
| `executions` | PEM compose run history |
| `user_subscriptions` | Clerk user → tier |

**External:** Supabase waitlist · Stripe · Clerk · OMP server memories

---

## 7. Auth, editions & billing

### Auth
- Clerk cloud auth or self-hosted HTTP basic
- Session cookies, CSRF (production), BYOK headers (not persisted)

### Editions

| Edition | Daily sends | Ensemble | History | Class export |
|---------|-------------|----------|---------|--------------|
| Free | 5 | 1 target | 7 days metadata | none |
| Pro | 100 | 8 targets | full | cursorrules, mdc, fabric, dspy |
| Team | unlimited | 8 | full | same as Pro |
| Self-hosted | unlimited | 8 | full | same as Pro |

### Billing
- Stripe checkout, portal, webhooks
- Credit wallet when cloud credits enforced

---

## 8. AI targets, intents & workflows

### Targets
`claude` · `gemini` · `deepseek` · `kimi` · `ollama` · `cursor` (compile-only)

### Intents
`research` · `design` · `comparison` · `debug` · `analysis`

### Workflows (`POST /api/render`)
- **single** — Quick Answer
- **ensemble** — Compare & Validate
- **redhat** — Refine & Verify (draft → critique → rewrite)

### Draft pipeline models
- Draft: Claude Sonnet
- Lock inference: DeepSeek Chat
- Red-Hat: DeepSeek-R1 (via cost governor)

---

## 9. OMP & developer MCP

### OMP (product-integrated)
- `/api/omp/*` routes for remember / recall / list
- Internal use: vault indexing, compile cache keys
- Reported in `/health`

### PEM MCP (developer only — not in product UI)
- stdio MCP for Cursor: `pem_compile`, `pem_combine`, `pem_critique_rewrite`, `pem_dialect_lint`, `pem_export`, `swarm_start/status/develop`, `pem_apply_diff`

---

## 10. Observability & safeguards

- Sentry (server + browser)
- Plausible analytics (landing)
- Rate limiting on export and substrate ingest
- Project ownership middleware
- Cost governance: budgets, token ledger, SSE budget errors
- Upload limits: 10 MB default; vault Textract **50 pages** max (PDF)
- Audit logging: EXPORT_*, JDF_PUT, SUBSTRATE_*, DRIFT_CHECK, DOCUMENT_LOCK, etc.
- **User activity log** (v1.5): middleware on `/api/*` → `user_activity_log`
- CLI: `flask prune-cache` for expired pipeline_cache

---

## 11. JavaScript modules map

| Module | Function |
|--------|----------|
| `app.js` | Legacy compose Send/render |
| `app_nav.js` | Sidebar, compiler status bar, toasts |
| `projects.js` | Project dashboard CRUD |
| `new_project_wizard.js` | v1.4 3-step wizard |
| `generate.js` | Draft/compile SSE + coachmark |
| `jdf_canvas.js` | Canvas, save, export, conflicts |
| `jdf_tiptap.js` | TipTap ↔ JDF bridge |
| `inquire_client.js` | Inquire SSE refine |
| `surgical_click.js` | Floating bar + surgical popover |
| `substrate_vault.js` | Sources UI |
| `vault_prompts.js` | Prompt library UI |
| `confidence_highlighter.js` | Overlay painting |
| `audit_gate.js` | Preflight gate before dock |
| `adoption.js` | Lock inference UI |
| `onboarding.js` | First-run tour |
| `toast.js` | Non-critical notifications |
| `compliance_ui.js` | **v1.5** Lock, sign-off, decision log, config import UI |
| `sandbox.js` | Landing paste test |
| `landing-demo.js` | Landing interactive demo |

---

## 12. Known limitations

| Area | Limitation |
|------|------------|
| Source conflicts | Keyword-level only; semantic NLI deferred to v2.0 |
| Drift check | Numeric/key overlap vs policy text; not semantic NLI |
| Vault upload | Multi-page PDF up to 50 pages; images still single-page |
| Sign-off / lock | No unlock route; no SSO-backed reviewer identity (v2.0) |
| Audit PDF | Fallback PDF engine if WeasyPrint/Playwright unavailable |
| Team edition | Shared workspaces not in this repo |
| Cursor target | Compile/copy only — no model Send |
| Sandbox / landing demo | No document persistence |
| CI drift auth | Clerk session required; project API key deferred |

---

## Related documents

- [Insurance demo pack](./demo/insurance-boston-real-estate/README.md)
- [Deploy flow](../.cursor/rules/deploy-flow.mdc)
- [Launch checklist](./launch-checklist.md)

---

## Release checklist (quick reference)

| Milestone | Branch / SHA | Environment | UI cache |
|-----------|--------------|-------------|----------|
| v1.4.0 | `main` / `ee6a9ee` | Production | `assure-96` |
| v1.5 | `staging` / `bc7515c` | Staging | `assure-97` |
| v1.5 → production | Pending merge `staging` → `main` | — | — |

---

*Generated from codebase inventory. For API details see route handlers in `prompt_matrix/web.py` and `prompt_matrix/routers/`.*
