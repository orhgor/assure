# Assure — full product status

**Date:** 2026-09-02  
**Decision:** **CONDITIONAL GO** — soft launch ready; monitor traffic with `scripts/monitor_launch.py`  
**Tests:** 169 passing (`uv run --python 3.11 python -m unittest discover -s tests`)  
**Container:** `docker compose up -d assure-app` — healthy on `:8765`  
**Git:** `307949b` on `p4-account-wallet` — `feat: complete day 2 launch readiness`  
**Detail checklist:** [launch-checklist.md](./launch-checklist.md)

---

## One-line summary

Assure is a local AI workbench that translates plain questions into model-specific prompts, runs Compare & Validate / Refine & Verify workflows, and checks answers against uploaded files. The public site is live at **getassureai.com**. The workbench installs from source today; there is no public binary or PyPI package yet.

---

## Product identity

| Layer | Name | Role |
| :--- | :--- | :--- |
| **Product** | Assure | Desktop workbench (`assure --web`). Paid tiers for limits and export. |
| **Engine** | PEM (Prompt Engineering Matrix) | Open-source compiler in `prompt_matrix/`. Powers dialect prompts, merge, critique, grounding. |
| **CLI** | `pem` / `assure` | Same entry point. `pem --ci`, `pem eval`, `pem monitor`, MCP server. |
| **Public site** | getassureai.com | Marketing, compiler preview, waitlist. Not the app. |
| **GitHub `orhgor/assure`** | Webpage branch only | Do not clone for the app. Deploy via `./scripts/sync-webpage.sh`. |

---

## Launch decision

All hard gates for a soft launch are satisfied:

| Gate | Status |
| :--- | :--- |
| `getassureai.com` + `www` → Cloudflare Worker | ✅ 200 |
| `hallucination-detection.html` live | ✅ 200 |
| Live Send (3 workflows, grounding, history, export) | ✅ Browser-verified |
| 7 languages on Compose | ✅ Browser-verified |
| Landing page + GA4 + privacy disclosure | ✅ Live |

**Not ready for:** promising a download button, implying Assure pays API costs, or wide promotion before P1 fixes below.

---

## Business model (BYOK)

Assure is **bring your own key (BYOK)**. This is now stated on the landing page (hero, pricing, FAQ, footer). Key facts:

| Who pays | What |
| :--- | :--- |
| **User → provider** | Token usage (Gemini, DeepSeek, Claude, Kimi, Ollama) |
| **User → Assure** | Workbench subscription (Free $0, Pro $19/mo) — limits and features, not model calls |

| Tier | Price | Checks/day | Models | History | Export | Diff panel |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| **Free** | $0 | 5 | Gemini Flash grounding | 7 days | Blocked | Hidden |
| **Pro** | $19/mo | 100 | Claude 3.5 + Gemini 1.5 Pro consensus | Full SQLite | Markdown, HTML, PDF, Prompty | Visible |
| **Team** | Install flag | Unlimited on this machine | All (max 8) | Full | All | Visible |

Team is `ASSURE_EDITION=team` or `assure --web --edition team`. It is an install flag, not a third SaaS plan and not shared workspaces.

**Not on PyPI yet.** `pip install prompt-matrix` does not work. Install: `./scripts/install.sh` then `assure --web`.

---

## Final development steps (completed)

Chronological build-out through Day 2 launch readiness. All steps verified with **169 unit tests** and live Docker pre-flight where noted.

### Phase 0 — Pre-flight hardening (`9b93a8f`, `c727051`)

| Step | Deliverable | Verified |
| :--- | :--- | :--- |
| 0.1 | Root `Dockerfile` (Python 3.11-slim, CPU torch, gunicorn, flask-cors) | ✅ Image builds |
| 0.2 | `docker-compose.yml` — `assure-app` on `:8765`, `./data` volume, `DATABASE_PATH` | ✅ Container healthy |
| 0.3 | SQLite WAL + request-scoped `get_db()` / `close_db()` in `history.py` | ✅ Unit tests |
| 0.4 | CORS for production origins + `/api/health` (`status: ok`) | ✅ `verify_phase0.py` |
| 0.5 | Stripe webhook `/api/webhooks/stripe` → `user_subscriptions` upsert | ✅ SQLite row persisted |
| 0.6 | Clerk script + `app.js` Bearer token forwarding on `/api/*` | ✅ In tree |
| 0.7 | `scripts/verify_phase0.py` — health, CORS, webhook, RAM audit | ✅ All checks PASS |
| 0.8 | `.gitignore` — `data/`, `*.sqlite`, WAL/SHM files | ✅ |

### Day 1 — Feature completion (same branch, pre-commit)

| Step | Deliverable | Verified |
| :--- | :--- | :--- |
| 1.1 | Settings modal — Gemini/Claude keys in `localStorage` | ✅ UI in `index.html` |
| 1.2 | `X-Gemini-Key` / `X-Claude-Key` headers via `app.js` + in-memory backend | ✅ `keys.py` / `web.py` |
| 1.3 | 👍 / 👎 thumbs → `POST /api/feedback` with `status: ok` | ✅ UI + API |
| 1.4 | Model attribution from `models_used` (successful drafts only) | ✅ `quality.py` + `app.js` |
| 1.5 | Ground count — `X grounded, Y inferred` (not "0 claims checked") | ✅ `app.js` |

### Day 2 Morning — Honest copy alignment (`307949b`)

| Step | Deliverable | Verified |
| :--- | :--- | :--- |
| 2.1 | Landing hero — NLI grounding narrative, **Launch App** → `app.getassureai.com` | ✅ `landing/index.html` |
| 2.2 | Purge unshipped claims (citation verification, OpenAlex, dossier exports) from index | ✅ |
| 2.3 | Pricing on landing — Free 5 checks/day, Pro $19/mo 100 checks | ✅ |
| 2.4 | In-app microcopy — grounding placeholders, green/yellow legend | ✅ All 7 locales in `i18n.py` |
| 2.5 | `editions.py` — Free 5/day + Gemini-only; Pro 100/day + Claude+Gemini consensus | ✅ Unit tests |

### Day 2 Afternoon — Launch verification & telemetry (`307949b`)

| Step | Command / artifact | Result |
| :--- | :--- | :--- |
| 3.1 | `scripts/monitor_launch.py` — runs, model mix, feedback, Pro subs | ✅ Created |
| 3.2 | `uv run --python 3.11 python -m unittest discover -s tests` | ✅ 169 OK |
| 3.3 | `uv run --python 3.11 python scripts/verify_phase0.py` | ✅ 4/4 PASS |
| 3.4 | `docker compose up -d --build assure-app` | ✅ Healthy (~235 MiB RAM) |
| 3.5 | Git commit launch assets (`landing/`, `prompt_matrix/`, `tests/`, `scripts/`) | ✅ `307949b` |

**Live monitoring:**

```bash
uv run --python 3.11 python scripts/monitor_launch.py --watch
```

**UTM launch links:**

| Channel | URL |
| :--- | :--- |
| Product Hunt | `https://app.getassureai.com?utm_source=producthunt&utm_medium=launch&utm_campaign=day2` |
| Reddit | `https://app.getassureai.com?utm_source=reddit&utm_medium=community&utm_campaign=consulting` |
| LinkedIn | `https://app.getassureai.com?utm_source=linkedin&utm_medium=social&utm_campaign=launch` |
| X/Twitter | `https://app.getassureai.com?utm_source=x&utm_medium=social&utm_campaign=launch` |

**Public tunnel ($0, external terminal):**

```bash
cloudflared tunnel --url http://localhost:8765
```

---

## What is live today

### Public website

- **URL:** https://getassureai.com/ (apex and www)
- **Worker:** Cloudflare `assure`, custom domains attached
- **Pages:** Home, pricing, install, privacy, terms, about, audit, hallucination-detection, three use-case pages (consultant, researcher, analyst), 404
- **Compiler preview:** Runs in browser only; does not Send
- **Analytics:** GA4 (`G-54F5NE9Y0P`) on all 12 HTML pages; disclosed on `/privacy`
- **Waitlist:** Modal on home; backend via Supabase migration in tree
- **Download buttons:** Disabled — no public binary URL
- **Cache:** Landing CSS `?v=30` (local tree; redeploy with `./scripts/sync-webpage.sh` to push BYOK copy live)

### Workbench (local app)

- **Start:** `assure --web` → http://127.0.0.1:8765
- **Sign-in:** Off by default on loopback. LAN requires `--http-pass`
- **Clerk:** If both `CLERK_PUBLISHABLE_KEY` and `CLERK_SECRET_KEY` are in `.env`, Compose requires cloud sign-in (P1 — see blockers)
- **Providers tested:** Gemini, DeepSeek, Claude (live Sends). Kimi key rejected 401 on all endpoints. Ollama not connected on test machine.

---

## What works (verified)

### Compose UI — Pass

Full browser walk across all 7 locales (`en es zh fr de ja tr`):

- Model pills with connected / not-connected state
- Workflows: Quick Answer, Compare & Validate (default), Refine & Verify
- Intent dropdown with benefit copy in every language
- Example chips, Send / Copy radios, Advanced options
- Language switcher persists via `?lang=` and `assure_lang` cookie

### Send pipeline — Pass

| Workflow | Verified |
| :--- | :--- |
| Compare & Validate | Dual-model draft + merge |
| Quick Answer | Single model + format pass |
| Refine & Verify | Draft → critique → rewrite + diff panel |
| Ground against files | Grounded / inferred span highlighting |
| Spinner / busy state | Button disables, Working… shown |

### History & export — Pass

- Grouped list by date, View dialog, Refine pre-fills Compose
- Search via `/api/history/search`
- Export: Markdown, HTML, Prompty, PDF (all four verified on Team)
- Delete / Clear all: confirmation in code (not executed in test pass)

### HTTP API — Pass (core routes)

`/api/health`, `/api/status`, `/api/catalog`, `/api/i18n`, `/api/render`, `/api/feedback`, `/api/history`, `/api/history/<id>/export`, `/api/prompts`, `/api/webhooks/stripe`

### Docker / persistence — Pass

| Item | Status |
| :--- | :--- |
| `docker compose` assure-app | ✅ Healthy on `:8765` |
| `data/history.sqlite` | ✅ Volume-mounted; `user_subscriptions` table live |
| Phase 0 audit script | ✅ `scripts/verify_phase0.py` |
| Launch telemetry | ✅ `scripts/monitor_launch.py` |

### CLI — Partial Pass

| Command | Status |
| :--- | :--- |
| `assure --help` | ✅ |
| `pem eval --dataset …` | ✅ compile-only |
| `pem --ci gemini analysis "…" --copy` | ✅ JSON stdout |
| `pem monitor --show-cost` | ✅ |
| `pem eval --direct` | Not run |
| `pem mcp` (stdio) | Not started this session |

### CI — Pass locally

- `.github/workflows/ci.yml` exists
- 169 unit tests OK locally
- Confirm GitHub Actions green before merge

---

## Known defects and blockers

### P1 — fix before wide promotion

| ID | Issue | Status |
| :--- | :--- | :--- |
| **B1** | Thumbs UI missing | ✅ **Fixed** — 👍/👎 in `#feedback-section` (Day 1) |
| **B2** | Model attribution ignores failed drafts | ✅ **Fixed** — `models_used` only (Day 1) |
| **B3** | Clerk gate surprise | ⚠️ Open — document or add loopback bypass |

### P2 — fix before v1.0

| ID | Issue | Status |
| :--- | :--- | :--- |
| P2-1 | Token readout resets to 0 after Send | ⚠️ Open |
| P2-2 | Grounded copy says "0 claims checked" | ✅ **Fixed** — dynamic ground count (Day 1) |
| P2-3 | Generic bold labels painted as inferred | ⚠️ Open |
| P2-4 | File path in Extra context — no highlight legend | ⚠️ Open |
| P2-5 | Export `Content-Type` doubled charset | ⚠️ Open |
| P2-6 | GA4 EU consent / Consent Mode v2 | ⚠️ Decision needed |

### Not proven / not run

- Citation scrubber (no hallucinated citation in test Sends)
- Research intent four-part shape
- Free-tier quota UI block
- Copy-mode click-through
- Closed-to-internet → Ollama Send
- Library / Classes UI (5.1–5.6)
- Desktop build (`dist/` empty)
- First-run no-key Connect flow (keys already present on test machine)
- Windows install script on Mac

---

## Not built

| Item | Notes |
| :--- | :--- |
| Public desktop download | Scripts exist (`build-desktop.sh`, `assure.spec`); no signed release |
| PyPI package | `python -m build` not published |
| Shared workspaces / SSO | Explicitly out of scope |
| Tour overlay DOM | i18n strings exist; widget not in current `index.html` |
| CheckClaim / Forge product | Internal only; last swarm confidence 0.00 |
| Kimi on test machine | Provided key returned 401 on all Moonshot endpoints |

---

## Architecture (short)

```
User question
    ↓
PEM compiler (intent + dialect + audience)
    ↓
Workflow: single | ensemble | refine
    ↓
LiteLLM → Gemini / DeepSeek / Claude / Kimi / Ollama
    ↓
Quality pass (audit_spans, citation scrubber)
    ↓
Answer panel + history.sqlite on local machine
```

- **Keys:** `prompt_matrix/.env`, gitignored
- **History:** Local SQLite; `PEM_ENABLE_HISTORY` and `PEM_STORE_PROMPTS` off by default
- **MCP:** `pem` server for Cursor (`pem_compile`, `pem_combine`, `swarm_start`, etc.)
- **Landing Worker:** Static assets + routes for `/privacy`, `/install`, `/terms`, `/about`

---

## Messaging status

| Area | Status |
| :--- | :--- |
| Hero (NLI grounding) | ✅ Updated locally — **deploy with sync-webpage** |
| BYOK / localStorage keys | ✅ Landing + Settings modal |
| Pricing ($0 / $19) | ✅ Landing index + `editions.py` aligned |
| OSS foundations (PEM, LiteLLM, Jinja2) | ✅ Footer + FAQ |
| Privacy (keys client-side, Send to provider only) | ✅ Consistent |
| Thumbs / feedback loop | ✅ UI + API (B1 closed) |
| EU analytics consent | ⚠️ Disclosed but no opt-in banner |

Launch copy drafts ready in `landing/launch/` (Product Hunt, LinkedIn, Twitter, newsletter).

---

## Distribution channels

| Channel | Status |
| :--- | :--- |
| Website | Live |
| Source install | `./scripts/install.sh` |
| Waitlist | Form on site; Supabase schema in tree |
| Product Hunt | Draft only |
| PyPI | Not published |
| Desktop `.app` / `.exe` | Not built for release |

---

## Recommended next actions

### Immediate (post Day 2)

1. Deploy honest-copy landing: `./scripts/sync-webpage.sh` + `wrangler deploy`
2. Push branch / open PR; confirm GitHub Actions green
3. Run launch monitor during first traffic: `uv run --python 3.11 python scripts/monitor_launch.py --watch`
4. Decide **B3** (Clerk loopback bypass or document sign-in requirement)

### Launch sequence

5. Product Hunt, LinkedIn, X, Reddit — use UTM links above
6. Optional public tunnel: `cloudflared tunnel --url http://localhost:8765`
7. Keep download buttons disabled until a real binary URL exists

### Post-launch

8. `python -m build && twine upload dist/*` (when ready for PyPI)
9. Desktop build and signed release
10. EU consent decision for GA4
11. Library / Classes browser pass
12. Free-tier live UI verification under real quota (5/day)

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

*This document reflects the tree and verification passes through 2026-09-02 (Day 2 afternoon). Update after deploys, PyPI publish, or production traffic.*
