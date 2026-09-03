# Assure — Feature Decision Matrix
## Cut / Hide / Keep / Add | Synthesis of Both Audits

---

## Legend

| Symbol | Meaning |
|--------|---------|
| 🔴 **CUT** | Remove from product entirely. Throw away code if minimal. |
| 🟡 **HIDE** | Keep functionality. Remove from first-run UI. Power users can find it. |
| 🟢 **KEEP** | Keep as-is. Core value proposition. |
| 🔵 **ADD** | New feature. Not in current product. |
| ⏳ **V2** | Valid idea. Post-launch. |

---

## Compose UI

| Feature | Current State | Audit 1 | Audit 2 | Final | Action |
|---------|--------------|---------|---------|-------|--------|
| 3-step onboarding tour | Overlay with Next/Back/Skip | 🔴 Cut | 🔴 Cut | **🔴 CUT** | Remove. Live preview is the tutorial. |
| Workflow selection (Quick/Compare/Refine) | 3 radios, Compare default | 🔴 Cut to 1 | 🟡 Hide | **🟡 HIDE** | Default Compare. "⚙️ Advanced" toggle reveals others. |
| Intent dropdown (5 options) | User must select | 🔴 Cut | 🟡 Auto-detect | **🟡 AUTO-DETECT** | Default "Auto". Chip shows detected intent. Click to override. |
| Copy vs Send radios | Two radios, Send default | 🔴 Cut Copy | 🟡 Hide Copy | **🟡 HIDE COPY** | Default Send. Option+Click or Cmd+Shift+Enter for Copy. |
| Ground checkbox | Checkbox, auto when file attached | 🟢 Already auto | 🟢 Already auto | **🟢 HIDE CHECKBOX** | Runs automatically. No UI element. |
| Advanced options panel | Hidden panel with 5+ options | 🔴 Cut all | 🟡 Keep, collapsed | **🟡 COLLAPSED** | Single "⚙️ Advanced" toggle. Smart defaults inside. |
| Live prompt preview | **Missing** | 🔵 Add | 🔵 Add | **🔵 ADD (P0)** | Real-time panel showing compiled prompt as user types. |
| Binary trust badge | Overlap % + citation count | 🔵 Add | 🔵 Add | **🔵 ADD (P0)** | ✅ Verified / ⚠️ Review needed. No numbers. |
| Confidence statement | **Missing** | 🔵 Add | 🔵 Add | **🔵 ADD (P0)** | "Models agree on X%. Y claims flagged." |
| Token/cost display | Line under answer | 🔴 Cut UI | 🟡 Hover only | **🟡 HOVER ONLY** | Hover on "Get my answer" shows estimate. |
| Thumbs up/down | 👍/👎 under answer | 🔴 Replace | 🟡 Replace | **🔴 REPLACE** | "↻ Refine this answer" button. |
| Suggested follow-ups | **Missing** | 🔵 Add | 🔵 Add | **🔵 ADD (P1)** | 3 contextual chips post-answer. |
| Mobile layout | Desktop-first | 🔵 Add | 🔵 Add | **🔵 ADD (P1)** | Single column, stacked, <768px. |
| Language switcher | Header dropdown (7 languages) | 🔴 Cut | 🟡 Hide | **🟡 HIDE** | Remove from v1 UI. i18n code stays. |
| History | Full tab with search/export | 🔴 Dropdown | 🟡 Dropdown | **🟡 DROPDOWN** | Recent 3 on Compose. Full history behind "View all". |

---

## Product Features

| Feature | Current State | Audit 1 | Audit 2 | Final | Action |
|---------|--------------|---------|---------|-------|--------|
| Compare & Validate (ensemble) | Default workflow | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Core moat. Multi-model consensus. |
| Citation scrub | Automatic post-processing | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Core differentiator. No invented stats. |
| Multi-model merge | Consensus + disagreements | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Default behavior. |
| File upload / grounding | Attach file, auto-ground | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Core value proposition. |
| Local-first / closed to internet | Advanced option | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Default when Ollama detected. |
| Improve loop (bandit) | Backend + some UI | 🔴 Cut | 🟡 Invisible | **🟡 INVISIBLE** | Zero UI exposure. Backend only. |
| Cost router | Backend pricing table | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Backend. No UI. |
| Red-hat critique | Attempt → critic → final | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Triggered by "Refine" button or model disagreement. |
| Rule critic (`--critic rule`) | Local structural check | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Power user CLI feature. |
| Red-team (`--redteam`) | Local injection/PII check | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Power user CLI feature. |

---

## Developer / Power User Surfaces

| Feature | Current State | Audit 1 | Audit 2 | Final | Action |
|---------|--------------|---------|---------|-------|--------|
| CLI (`pem` / `assure`) | Full feature set | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Power user surface. Document well. |
| `pem eval --dataset` | Batch evaluation | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | CI/testing surface. |
| `pem --ci` | JSON output for CI | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | GitHub Actions integration. |
| `pem monitor` | Usage from history.sqlite | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Power user analytics. |
| MCP server (`pem mcp`) | stdio MCP tools | 🔴 Cut v1 | 🟡 Keep, dev docs | **🟡 DEV DOCS** | Keep in repo. Not on landing page. |
| Swarm (`swarm_develop`) | Architect → dev → review | 🔴 Cut v1 | 🟡 Keep, no docs | **🟡 NO DOCS** | Research project. Revisit at 10K users. |
| Desktop app (PyInstaller) | Build scripts exist, dist/ empty | 🔴 Cut v1 | 🟡 Keep, no marketing | **🟡 NO MARKETING** | Don't sell what isn't downloadable. |
| Classes / Library / Learn | Full class system | 🔴 Cut | 🟡 Hide behind tab | **🟡 HIDE** | "Prompt Library" tab. Not on Compose. |
| Class export (cursorrules, mdc, etc.) | Pro+ feature | 🔴 Cut v1 | 🔴 Cut v1 | **🔴 CUT FROM V1** | Valid for v2. Not launch-critical. |
| Class versioning (snapshot/rollback/diff) | Implemented | 🟡 Hide | 🟡 Hide | **🟡 HIDE** | Keep in Library tab. |

---

## Editions & Pricing

| Feature | Current State | Audit 1 | Audit 2 | Final | Action |
|---------|--------------|---------|---------|-------|--------|
| Free (10 Sends/day) | Implemented | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Acquisition tier. |
| Pro ($5/mo, 100 Sends) | Implemented | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Revenue tier. |
| Team (unlimited, same machine) | Separate SKU | 🔴 Merge | 🔴 Merge | **🟡 MERGE INTO PRO** | "Pro (Team)" — same SKU, different label. |
| Self-hosted (unlimited) | Separate SKU | 🔴 Merge | 🔴 Merge | **🟡 MERGE INTO PRO** | "Pro (Self-hosted)" — install method, not edition. |
| Stripe checkout | Test mode | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Optional. `--edition` still works without keys. |
| Clerk cloud auth | Optional | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Optional. Self-hosted skips it. |

---

## Landing Page & Marketing

| Feature | Current State | Audit 1 | Audit 2 | Final | Action |
|---------|--------------|---------|---------|-------|--------|
| Landing page hero | Prompt-first, 3 personas | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Good copy. Don't change. |
| Trust row (local-first, pick provider, no lock-in) | Implemented | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Strong messaging. |
| Check outputs page | Live on Worker | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | `hallucination-detection.html`. |
| Download OS buttons | Disabled (no URL) | 🟢 Keep disabled | 🟢 Keep disabled | **🟢 KEEP DISABLED** | No public installer yet. |
| Pricing page | Free/Pro/Team/Self-hosted | 🟡 Merge | 🟡 Merge | **🟡 2 TIERS** | Free + Pro only. |
| Terms page | Implemented (7 locales) | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Required. |
| Privacy page | Implemented | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Required. |
| ICP pages (Lena/Marek/Priya) | `landing/ICP.md` | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Internal reference. |
| Use-case pages | Consultant, researcher, analyst | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | SEO + conversion. |
| Product Hunt draft | `landing/PRODUCT_HUNT.md` | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Launch asset. |

---

## Infrastructure & Backend

| Feature | Current State | Audit 1 | Audit 2 | Final | Action |
|---------|--------------|---------|---------|-------|--------|
| SQLite history | `history.sqlite` | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Core data store. |
| `history.sqlite` schema | executions, prompt_versions, performance | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Supports v2 features (diff, evolution). |
| Jinja2 templates per model | `config.json` | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Core compiler. |
| Dialect lint | Pre-send validation | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Quality gate. |
| LiteLLM runner | `litellm_runner.py` | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Model abstraction layer. |
| Token counter (tiktoken) | `token_counter.py` | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Accurate counts. |
| GitHub Actions CI | `.github/workflows/ci.yml` | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Unittest + CI + eval. |
| Desktop build CI | `.github/workflows/desktop.yml` | 🟡 Keep | 🟡 Keep | **🟡 KEEP** | Don't remove. Just don't market. |
| `docs/launch-checklist.md` | Pre-launch tests | 🟢 Keep | 🟢 Keep | **🟢 KEEP** | Update after this audit. |

---

## Summary Count

| Category | 🔴 Cut | 🟡 Hide | 🟢 Keep | 🔵 Add | ⏳ V2 |
|----------|--------|---------|---------|--------|------|
| Compose UI | 3 | 8 | 2 | 6 | 0 |
| Product Features | 0 | 1 | 10 | 0 | 0 |
| Developer Surfaces | 2 | 5 | 5 | 0 | 0 |
| Editions & Pricing | 0 | 2 | 3 | 0 | 0 |
| Landing & Marketing | 0 | 1 | 10 | 0 | 0 |
| Infrastructure | 0 | 0 | 10 | 0 | 0 |
| **TOTAL** | **5** | **17** | **40** | **6** | **0** |

**Philosophy:** Hide 17 features from first-run. Cut 5. Keep 40. Add 6. The product becomes simpler without losing capability.

---

*Matrix synthesized from Red-Hat Audit #1 (Original) and Red-Hat Audit #2 (Combined Assessment) | 2026-08-31*
