# Assure — launch checklist & product assessment

**Purpose:** Single document for launch readiness, market assessment, positioning, feature inventory, and development history.  
**Product:** Assure (workbench) · **Engine:** PEM (`prompt_matrix`)  
**Last updated:** 2026-09-02  
**Decision:** **CONDITIONAL GO** — soft launch ready; fix P1 items before wide promotion  
**Tests:** 169 passing locally (`python -m unittest discover -s tests`)  
**Live site:** https://getassureai.com/ · Worker commit `c0a7739` · CSS `?v=30`

---

## How to use this document

| Section | Use it for |
| :--- | :--- |
| [Executive summary](#executive-summary) | One-page status for stakeholders |
| [Product positioning](#product-positioning) | Messaging, differentiation, what Assure is / is not |
| [Business model](#business-model-byok) | Pricing, tiers, who pays for what |
| [Ideal customer profile](#ideal-customer-profile) | Personas, jobs, buying triggers |
| [Competitive landscape](#competitive-landscape) | Alternatives and honest limits |
| [Feature inventory](#feature-inventory) | Shipped, partial, cut, not built |
| [Development timeline](#development-timeline) | What was built, when, and what landed |
| [Launch verification](#launch-verification-checklist) | Pass/fail checklist (Sections 1–14) |
| [Blockers & next steps](#remaining-blockers-and-open-items) | P1/P2 and launch sequence |

**Legend (checklist items)**

- `[x]` Pass — probed in browser, live URL, or unittest/code review this session.
- `[ ]` Fail, not run, or blocked — note explains which.

---

## Executive summary

Assure is a **local AI workbench** that translates plain questions into model-specific prompts, runs **Compare & Validate** / **Refine & Verify** workflows, and **checks answers against uploaded files**. The compiler engine is PEM (open-source foundations: PEM, LiteLLM, Jinja2). The paid product is the workbench.

| Area | Status |
| :--- | :--- |
| **Public website** | Live at `getassureai.com` + `www` (200). BYOK pricing copy deployed. GA4 live and disclosed. |
| **Core product** | End-to-end Send works: three workflows, grounding highlights, history, four export formats. |
| **i18n** | All 7 locales verified on Compose (`en es zh fr de ja tr`). |
| **Distribution** | Source install only. No PyPI. No public desktop download. Waitlist on site. |
| **Launch** | Conditional GO. Fix thumbs UI, model attribution, Clerk gate before Product Hunt. |

**One line:** Talk to AI like a colleague — we handle the translation and the check. You bring your own API keys; Assure charges for the workbench, not the models.

---

## Product positioning

### What Assure is

- A **private workbench** for researchers, analysts, and consultants who already pay for Gemini, DeepSeek, Claude, or Kimi.
- A **prompt compiler** that restructures natural language into each model's dialect — no prompt engineering required.
- A **validation layer**: Compare & Validate (multi-model consensus), Check against my files (citation scrub + grounded/inferred highlights), Refine & Verify (draft → critique → rewrite).
- **BYOK (bring your own key):** keys stay in `.env` on the user's machine. Sends go only to the provider they connected.

### What Assure is not

- Not a chatbot or a new foundation model.
- Not a wrapper that only forwards text — it compiles, merges, filters, and checks.
- Not a cloud that stores briefs — questions and files stay on the local machine (Send goes to the chosen provider).
- Not an observability suite (PromptLayer / LangSmith) — it is a one-person workbench for one checked answer.
- Not live search — standalone PEM has no web. Do not promise current market data without an upload.
- Not shared workspaces, SSO, or team seats in this repo — Team edition is unlimited Sends **on this machine**, not multi-user SaaS.

### Positioning statement

> For knowledge workers who cannot paste client or research material into random chats, Assure is the local workbench that writes the prompt, asks more than one model when needed, and flags claims that were not in your files — without learning prompt engineering.

### Messaging pillars (live on site)

1. **Zero prompt engineering** — ask naturally; we translate.
2. **Private by design** — keys and copy stay here; Send goes only to who you connected.
3. **Defensible answers** — Compare & Validate, grounded highlights, citation scrub.
4. **BYOK transparency** — you pay providers for tokens; Assure charges for the workbench ($5/mo Pro).
5. **Open-source engine** — PEM CLI free with source install; workbench is the paid product.

---

## Business model (BYOK)

| Who pays | For what |
| :--- | :--- |
| **User → API provider** | Token usage (Gemini, DeepSeek, Claude, Kimi, Ollama) |
| **User → Assure** | Workbench subscription — daily Send limits, history, export, diff panel |

### Tiers (implemented in `editions.py`)

| Tier | Price | Sends/day | Compare models | History | Export | Red-hat diff |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Free** | $0 | 10 | 2 (Gemini + DeepSeek when live) | 7 days | Blocked | Hidden |
| **Pro** | $5/mo | 100 | Up to 8 | Full | MD, HTML, PDF, Prompty | Shown |
| **Team** | Install flag | Unlimited on this machine | Up to 8 | Full | All | Shown |

- Team = `assure --web --edition team` or `ASSURE_EDITION=team`. Not a third SaaS SKU. Not shared workspaces.
- Stripe test checkout + Clerk optional when keys in `.env`. `--edition pro` works without them on this machine.
- **Not on PyPI yet.** Install: `./scripts/install.sh` then `assure --web`.
- Download buttons on site stay **disabled** until a real binary URL exists.

### Pricing copy (live 2026-09-02)

Hero, pricing section, FAQ, and footer state: bring your own keys; Assure charges for the workbench; provider bills tokens; Free tier still pays the provider.

---

## Ideal customer profile

Source: `landing/ICP.md`. Archetypes for copy — no public customer list yet.

### Hero personas (3)

| Persona | One-line | Pain | Why Assure |
| :--- | :--- | :--- | :--- |
| **Sovereign Analyst** (Lena) | Needs answers but cannot send client data to public AI | NDA brief cannot go into random chat | Copy stays here; Check drops unsourced percents |
| **Privacy-First Researcher** (Marek) | Validated citations without exposing unpublished work | Invented papers; draft should not sit in vendor chat | Research intent + ground strips names not in file |
| **Prompt Reluctant Professional** (Priya) | Uses AI but won't learn prompt engineering | Won't learn dialects, XML, eval harnesses | Compose is three steps; compiler writes the prompt |

### Six jobs (landing "Who Assure is for")

| Job | Use-case page | Core workflow |
| :--- | :--- | :--- |
| Strategy consultant | `consultant.html` | Compare & Validate + Check against files |
| Academic researcher | `researcher.html` | Attach PDF; ground strips invented citations |
| Policy analyst | `analyst.html` | Compare & Validate; analysis intent |
| Technical writer | — | Check docs against repo notes |
| Marketing strategist | — | Same question, multiple models, overlap view |
| Compliance officer | — | Closed / Copy / Check — not a regulator product |

### Buying trigger

They buy when a **leak, fake citation, or weekend of copy-paste** costs more than $5/month. They already pay for at least one model.

### Objections (see `landing/objections.md`)

- "I already have ChatGPT Plus" → Assure compiles, compares, checks files; not a new model.
- "Just a wrapper" → compile + merge + citation pass + local keys.
- "Is it private?" → Copy never calls a provider; Send goes to who they connected; do not claim Send never leaves.
- "Do I need to be a developer?" → install script + paste key; more than a website, less than a pipeline.

---

## Competitive landscape

| Alternative | What it does | Assure difference |
| :--- | :--- | :--- |
| **ChatGPT / Claude.ai** | One model, one box | Multi-model Compare; file grounding; keys local |
| **Perplexity** | Live web search | Assure has no live search; checks *your* files |
| **PromptLayer / LangSmith** | Team prompt versioning, traces, evals | One-person workbench; not observability |
| **Open-source prompt tools** | Engine / CLI | Assure = workbench (UI, validation, export) |
| **Fabric / llm CLI** | Run prompts | PEM shapes dialects; Assure adds merge + check |

**Strategic mention on site:** Footer + FAQ acknowledge OSS roots (PEM, LiteLLM, Jinja2) and link `/install` for free CLI — builds trust without underselling the workbench.

---

## Feature inventory

### Core product — shipped & verified

| Feature | Status | Notes |
| :--- | :--- | :--- |
| Compose one-screen UI | ✅ | Question, live preview, Get my answer |
| Compare & Validate (default) | ✅ | Dual-model draft + merge |
| Quick Answer | ✅ | Single model + format pass |
| Refine & Verify | ✅ | Draft → critique → rewrite + diff panel |
| Auto intent detection | ✅ | Chips override; 5 intents with benefit copy |
| Check against my files | ✅ | Grounded/inferred span highlights |
| Citation scrub | ⚠️ | Code present; not proven with hallucinated citation in test |
| Binary trust badge | ✅ | Verified / Review needed |
| Confidence sentence | ✅ | In `#confidence-line` (not dead `#trust-strip`) |
| Previous work / history | ✅ | Grouped, search, View, Refine, Export |
| Export MD/HTML/PDF/Prompty | ✅ | All four verified on Team |
| 7-language UI | ✅ | Full Compose walk; 12 keys fixed 2026-09-02 |
| Example chips | ✅ | Compare AWS/GCP, paper, email |
| Cost router (backend) | ✅ | Static pricing table; cheap route |
| Improve loop / bandit (backend) | ✅ | API feedback works; no thumbs in UI |
| MCP server (`pem`) | ✅ | compile, combine, critique, swarm tools |
| Waitlist API | ✅ | `POST /api/waitlist`; Supabase migration in tree |
| P4 credit wallet (branch) | 🔶 | Clerk + Fernet + `/account/usage`; not launch-critical |

### UI / product — partial or defective

| Feature | Status | Issue |
| :--- | :--- | :--- |
| 👍 / 👎 thumbs | ❌ P1 | API works; UI shows only "Refine this answer" |
| Model attribution | ❌ P1 | Can name a model whose draft failed |
| Token readout after Send | ⚠️ P2 | Resets to 0; tooltip and history correct |
| Grounded copy | ⚠️ P2 | "0 claims checked" when spans exist |
| Extra context file path | ⚠️ P2 | Legend hidden unless file picker used |
| Tour overlay | Cut | i18n strings exist; DOM not deployed |
| Language switcher | Hidden in product | Visible in catalogs; hidden in header per audit |
| Library / Learn / Classes UI | Not walked | Routes exist; no landing promise |

### Cut / out of scope (v1)

| Item | Decision |
| :--- | :--- |
| 3-step onboarding tour | Cut — live preview is the tutorial |
| Shared workspaces / SSO | Out of scope |
| Live web search in PEM | By design |
| Public desktop download | Not until `dist/` built and URL set |
| PyPI publish | Not yet |
| CheckClaim / Forge product | Internal only |
| Contact Sales / enterprise tier | Explicitly no |

### Landing & marketing — live

| Asset | URL / path |
| :--- | :--- |
| Home + compiler demo | `/` |
| Check outputs | `/hallucination-detection.html` |
| Pricing (BYOK) | `/pricing.html` |
| Install | `/install` |
| Privacy + GA4 disclosure | `/privacy` |
| Terms | `/terms` |
| Use cases | `/use-cases/consultant|researcher|analyst.html` |
| ICP reference | `landing/ICP.md` |
| Objections | `landing/objections.md` |
| Launch drafts | `landing/launch/` (PH, LinkedIn, X, newsletter) |
| Product Hunt draft | `landing/PRODUCT_HUNT.md` |

---

## Development timeline

Chronological record of Assure launch work. Swarm run hashes and engineering detail lived in `prompt_matrix/PEM.md` before this doc became the source of truth.

### 2026-08-31 — Foundation & pre-launch

| Milestone | Result |
| :--- | :--- |
| Pre-launch checklist created | Section checkboxes + go/no-go framework |
| Worker deploy `9d48c07` | Check outputs page path established |
| Team Compare & Validate Send | `run_hash` + quality scores on this machine |
| Compose UX polish | Tour later cut; intent benefit copy; Copy/Send radios; Steve Jobs pass |
| Landing visual pass | Accent `#FF6B35`, scroll fade, industry tiles |
| ICP + objections + use-case pages | Lena/Marek/Priya; consultant/researcher/analyst |
| Install scripts | `install.sh` / `install.ps1`; landing Quick start |
| Terms + privacy | App `/terms` + landing `terms.html` |
| CI workflow | `.github/workflows/ci.yml` — unittest + `pem --ci` + eval |
| PyPI packaging prep | `pyproject.toml` 0.1.0; not published |
| Canonical domain | Migrated narrative to `getassureai.com` |

### 2026-09-01 — DNS, HTTPS, account features

| Milestone | Result |
| :--- | :--- |
| `getassureai.com` HTTPS 200 | Apex live; `www` attached later |
| P2.3 history diff | API `GET /api/history/diff`; Compare selected in UI |
| P2.4 mobile polish | Compose stacks at 640px |
| P4 credit wallet | Clerk TEXT ids, Fernet settings, `spend_credit`, `/account/usage` |
| P3.1 waitlist | `POST /api/waitlist`, Supabase RLS |
| Swarm infrastructure | `swarm_start` + `swarm_status`; `pem_apply_diff`; truncation fixes |

### 2026-09-02 — Launch phases (verification pass)

**Phase 1 — Deploy landing & DNS**

- Synced `landing/` to `webpage` branch; `wrangler deploy`
- `getassureai.com` + `www.getassureai.com` attached as Worker custom domains
- `hallucination-detection.html` → 200
- Cache buster → `?v=29` then `?v=30`

**Phase 2 — Live Send (Team edition)**

- Browser pass: 4 live Sends (ids 191–193)
- ✅ Quick Answer, Compare & Validate, Refine & Verify
- ✅ Grounding (5 grounded + 3 inferred spans)
- ✅ History View/Refine/Export (all 4 formats)
- ❌ Thumbs absent in UI (API OK)
- ❌ Model attribution names failed drafts
- Note: Clerk keys in `.env` gate Compose unless blanked for test

**Phase 3 — i18n (7 languages)**

- Browser walk: `en es zh fr de ja tr`
- Fixed 12 silent EN fallbacks (`diff`, `save`, `error.catalog`, etc.)
- Language switcher + `assure_lang` cookie verified
- 168 → 169 tests passing

**Phase 4 — Launch decision**

- Conditional GO documented
- P1 blockers: B1 thumbs, B2 attribution, B3 Clerk gate

**Same day — Product & messaging**

- P0/P1 visual audit gaps fixed (headings neutral, legend file-attached only, JSON highlight)
- Landing rewrite: "zero prompt engineering" / translation hero
- Hero: one Assure (removed duplicate brand)
- Google Analytics 4 (`G-54F5NE9Y0P`) + privacy disclosure
- **BYOK pricing transparency** — hero, pricing, FAQ, footer OSS line
- Deploy commit `c0a7739`; live verified BYOK copy + `site.css?v=30`

### Key hand-applied features (not swarm-landed)

| Area | What landed |
| :--- | :--- |
| Deep compiler | Audience blocks + CoT on research/analysis |
| Visual audit | `audit_spans()`, green/yellow highlights, legend |
| Compose one-screen | Tour cut; live preview; Refine replaces thumbs in UX intent |
| History evolution | Diff between runs; export formats |
| Landing | Unified design, waitlist modal, compiler demo in browser |

### Swarm notes (for context)

- Pro edition hit 100 Sends/day cap during several swarm runs — HTML dumps truncated; patches not applied blindly
- Last useful swarm: Blue Ocean landing `b648448f` — partial; hand-applied follow-up
- Forge/swarm confidence often 0.00 on truncated dumps — do not apply truncated `index.html`
- **Rule going forward:** log milestone rows in this document, not PEM.md

---

## Launch verification checklist

Corrections vs common mistakes:

- Hallucination page: `hallucination-detection.html` (not `/hallucination-detection`)
- Correct domain: **getassureai.com** (`getassure.com` is parked elsewhere)
- Use-case pages: consultant, researcher, analyst only
- Team = unlimited Sends on **this machine**, not shared workspaces
- Grounding drops invented dates/percents/publication names — not every answer shows exact phrase "Data not available in current context"

---

### 1. Installation and setup

- [ ] **1.1** `./scripts/install.sh` / `install.ps1` — Not re-run this pass. Scripts exist; venv present.
- [x] **1.2** `source prompt_matrix/.venv/bin/activate` — Pass.
- [x] **1.3** `assure --web` — Pass on `http://127.0.0.1:8765`.
- [ ] **1.4** First run, no provider key — Connect flow not re-run (keys already in `.env`).
- [x] **1.5** Sign-in — Off on loopback by default.
- [x] **1.6** `.env` gitignored — Pass.
- [x] **1.7** `--edition team` — Pass. Unlimited Sends on this machine.

**Gate:** Partial (1.1, 1.4). Core path works.

---

### 2. Core product functionality

Phase 3 browser pass — all 7 locales.

- [x] **2.1** First-visit tour — Cut. No overlay DOM.
- [x] **2.2** Tour dismissal — N/A.
- [x] **2.3** Model pills — Pass (6 targets + connection state).
- [x] **2.4** Connected / not connected — Pass.
- [x] **2.5** Workflow radios — Pass (Quick / Compare / Refine).
- [x] **2.6** Default Compare & Validate — Pass.
- [x] **2.7** Intent dropdown — Pass (Auto + 5 intents, translated).
- [x] **2.8** Example chips — Pass.
- [x] **2.9** Copy vs Send radios — Pass.
- [x] **2.10** Advanced options — Pass.

**Gate:** Pass.

---

### 3. Send and answer validation

Phase 2 browser pass — Team, 4 live Sends.

- [x] **3.1–3.6** Send, Compare, Quick, Refine, diff, ground — Pass.
- [ ] **3.7** Citation scrubber — Not proven (no hallucinated citation in test Sends).
- [ ] **3.8** Research four-part shape — Not run this pass.
- [x] **3.9** Trust/confidence line — Pass (`#confidence-line`; `#trust-strip` dead).
- [ ] **3.10** Thumbs — **Fail.** No 👍/👎 in UI. API OK.
- [x] **3.11–3.12** Self-improving API, spinner — Pass.
- [ ] **3.13–3.15** Timeout, Free quota UI, Cmd+Enter — Not run / code only.

**Defects:** P1 attribution; P2 token readout, grounded copy, bold labels, extra-context legend.

**Gate:** Pass on pipeline. P1 thumbs + attribution open.

---

### 4. History and export

- [x] **4.1–4.10** List, View, Refine, 4 exports, search — Pass. Delete/Clear confirmed in code only.

**Gate:** Pass.

---

### 5. Library and classes

- [ ] **5.1–5.6** Learn, save, version, diff, class export — Not run in UI.
- [x] **5.7** Free class export blocked — Pass in code.

**Gate:** Partial. Secondary feature.

---

### 6. CLI and developer surfaces

- [x] **6.1–6.2, 6.4–6.5, 6.8** — Pass.
- [ ] **6.3, 6.6, 6.7** — Not run (`eval --direct`, monitor JSON, mcp stdio).

**Gate:** Partial. Compile CLI green.

---

### 7. HTTP API

- [x] **7.1–7.4, 7.6–7.9, 7.12** — Pass.
- [ ] **7.5, 7.10–7.11** — Keys POST, library routes not run.

**Gate:** Pass for P0 routes.

---

### 8. Editions and gates

- [x] **8.1–8.13** — Pass at plan/code level. Live Free UI not re-switched.

**Gate:** Pass (code).

---

### 9. i18n and localization

- [x] **9.1–9.13** — Pass. 12 keys fixed. Tour strings translated; tour DOM absent.

**Gate:** Pass.

---

### 10. Privacy and security

- [ ] **10.1–10.2** Copy click, closed→Ollama — Not run.
- [x] **10.3–10.8** — Open Send, gitignore, sign-in defaults, history flags — Pass.

**Gate:** Partial.

---

### 11. Landing page and marketing

Live: Worker `c0a7739`, domains `getassureai.com` + `www`, cache `?v=30`.

- [x] **11.1–11.19** — Pass (hero, check outputs, pricing, terms, privacy, GA4, use cases, audit, PH draft).
- [x] **11.20** BYOK transparency — Pass live. Hero, pricing, FAQ, footer OSS. Deploy `c0a7739`.

**Open:** EU GA4 consent — no banner / Consent Mode v2. Decision needed for EU ICP.

**Gate:** Pass.

---

### 12. Desktop builds (optional)

- [ ] **12.1–12.2, 12.4** — Not built. Scripts exist.
- [x] **12.3** Frozen paths — Pass in code.

**Gate:** Skip for source-install launch.

---

### 13. GitHub Actions (CI)

- [x] **13.1–13.4** — Pass locally (169 tests). Confirm on GitHub before merge.

**Gate:** Pass locally.

---

### 14. Forge engine (internal, optional)

- [x] **14.1** swarm MCP unittest — Pass.
- [ ] **14.2–14.6** — Not reliable; last swarm confidence 0.00.

**Gate:** Skip.

---

## Launch go / no-go

| Gate | Status |
| :--- | :--- |
| Installation | Partial |
| Compose UI | **Pass** |
| Send & answers | **Pass** (P1 open) |
| History & export | **Pass** |
| Library | Partial |
| CLI | Partial |
| HTTP API | **Pass** |
| Editions | **Pass** |
| i18n | **Pass** |
| Privacy | Partial |
| Landing | **Pass** |
| Desktop | Skip |
| CI | **Pass** (local) |
| Forge | Skip |

### Hard gates (all true → soft launch OK)

| Condition | Result |
| :--- | :--- |
| `getassureai.com` → Worker | ✅ |
| `hallucination-detection.html` 200 | ✅ |
| Live Send (3 workflows + export) | ✅ |
| 7 languages on Compose | ✅ |
| BYOK messaging live | ✅ |

**Decision: CONDITIONAL GO** — usable end-to-end today; fix P1 before wide promotion.

---

## Remaining blockers and open items

### P1 — before Product Hunt

| ID | Item |
| :--- | :--- |
| B1 | Ship 👍/👎 or remove thumbs claim from all copy |
| B2 | Fix model attribution when a draft fails |
| B3 | Clerk gate: document override or `ASSURE_REQUIRE_LOGIN=false` |

### P2 — before v1.0

| ID | Item |
| :--- | :--- |
| P2-1 | Token readout resets to 0 after Send |
| P2-2 | Grounded copy contradicts span count |
| P2-3 | Generic bold labels painted inferred |
| P2-4 | Extra context path does not show legend |
| P2-5 | Export Content-Type doubled charset |
| P2-6 | EU GA4 consent decision |

### Launch sequence (when P1 done)

1. Confirm GitHub Actions green
2. `python -m build && twine upload dist/*` (PyPI)
3. Product Hunt — `landing/launch/product-hunt.md`
4. LinkedIn, X, newsletter — `landing/launch/`
5. Waitlist email — Supabase migration applied
6. Keep download buttons disabled until binary URL exists

---

## Architecture (reference)

```
User question → PEM compile (intent + dialect + audience)
             → Workflow: single | ensemble | redhat
             → LiteLLM → provider
             → audit_spans + citation scrub
             → Answer + history.sqlite (local)
```

- **Install:** `./scripts/install.sh` → `assure --web` → http://127.0.0.1:8765
- **Deploy site:** `./scripts/sync-webpage.sh /path/to/webpage-worktree` → commit `webpage` only → `npx wrangler deploy`
- **GitHub `orhgor/assure`:** webpage branch = public site only. Do not clone for the app.

---

## Related files

| Path | Contents |
| :--- | :--- |
| `landing/ICP.md` | Full persona write-ups |
| `landing/objections.md` | Sales objection answers |
| `landing/launch/` | Post-launch copy drafts |
| `docs/audits/assure-feature-decision-matrix.md` | Cut/hide/keep audit |
| `docs/product-status.md` | Short snapshot (may duplicate this doc) |
| `CHANGELOG.md` | Version history |
| `prompt_matrix/editions.py` | Tier gates |
| `tests/test_market_readiness.py` | Landing regression tests |

---

*Update this document after each launch milestone, deploy, or blocker fix. Append rows to [Development timeline](#development-timeline) — not PEM.md.*
