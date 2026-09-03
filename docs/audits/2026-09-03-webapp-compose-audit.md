# Audit: Assure webapp — Compose + JDF workbench

| Field | Value |
|---|---|
| Date | 2026-09-03 |
| PR | n/a (pre-ship audit on `p4-account-wallet` tree) |
| Branch | `p4-account-wallet` |
| Surfaces | `prompt_matrix/templates/index.html` (compose pane), `prompt_matrix/static/style.css` (JDF v2 block), `prompt_matrix/static/jdf_canvas.js`, `prompt_matrix/templates/base.html` |
| Auditor | agent (code + structure review; viewports inferred from CSS breakpoints) |
| Standard | `docs/WEBPAGE_AUDIT_STANDARD.md` §A (Assure binding) |
| Preflight | Landing honesty rules; in-app pricing copy in `i18n.py` |

## Summary

The JDF **Design System v2.0** shell (50/50 panes, command deck, aperture/diff/toolbar CSS) is **implemented and wired** to `jdf_canvas.js`. Craft inside the workbench is production-grade for desktop analysts. The **Compose page as a whole fails composition**: it stacks a new document-engineering workstation **above** the full legacy PEM compose form, with a third marketing hero and site chrome — three jobs, two preview panels, and extreme scroll depth. **Ship-blocking for JDF-first users is clarity, not pixels.**

## Gates

| Gate | Result | Notes |
|------|--------|-------|
| Composition | **Fail** | Compose pane = page hero (verify drafts) + JDF workbench + entire legacy `#form`. No single primary job. Users cannot tell whether to use **Inquire** or **Get my answer**. |
| Type & orphans | **Partial** | IBM Plex Sans/Mono matches §A. `compose-hero h1` uses `white-space: nowrap` at ≥840px — overflow risk on narrow desktop. Runtime strings (`Saving…`, `Stream complete`, Z3 badge) bypass i18n. |
| Empty space / rhythm | **Partial** | Workbench internal rhythm is good. Below it, an unmarked jump into the legacy form creates a **dead band** (no divider, no mode switch). Workbench fixed height (`min(720px, 100dvh - 220px)`) ignores hero + header overhead — page scrolls instead of one viewport job. |
| Alignment / shell | **Pass** (workbench) / **Partial** (page) | Command deck pins to workbench bottom correctly (not a third column). Site shell `--shell-max: 1080px` still applies; workbench is full pane width inside compose — acceptable. |
| Color / contrast | **Pass** | Trust Blue `#1A4B8C`, semantic pills, diff del/ins backgrounds align with v2 tokens. Legacy compose uses same token aliases — no purple-on-white drift. |
| Responsive | **Partial** | Workbench stacks at 768px (50vh panes). **390px:** header + hero + stacked workbench + full compose form = very long scroll; touch targets enlarged under `(pointer: coarse)` inside workbench only. iOS `100dvh` helpers scoped to `.jdf-workbench`, not full page. |
| Content honesty / assets | **Partial** | Header/footer closed/open copy is honest. Compose **hero still describes draft verification**, not document engineering — mismatched with JDF workbench title. Upgrade banner cites **$19/mo**; §A preflight cites **Pro $5 / 100 Sends** — reconcile before external launch. |
| Forms / analytics | **Pass** | No new marketing forms. Existing gtag disclosure covered by `test_market_readiness`. Export is real GET to `/api/projects/.../export`. |
| Cache bust | **Pass** | `APP_CSS = assure-37`, `APP_JS = assure-24` in `ui_cache.py`; template uses `css_version` / `js_version`. |

## Findings → recommended fixes

### P0 — do before promoting JDF to primary users

| # | Issue | Evidence | Recommended fix |
|---|--------|----------|-----------------|
| P0-1 | **Dual product on one page** | `index.html`: `#jdf-workbench` then `.layout` > `#form` with full compose journey | **Option A (fast):** Collapse legacy form behind `<details>` (“Classic compose”) default closed on production. **Option B (clean):** Route `/compose` = JDF only; move PEM form to `/verify` or hide when `__INITIAL_JDF__` is present. |
| P0-2 | **Hero copy ≠ workbench** | Hero: `hero.title` “Verify drafts…”; workbench: “Document engineering” | For compose, shorten hero to one line + link to classic flow, or swap hero to JDF-led copy (`jdf.workbench.title` + one support line). Remove redundant `how.restructure` when JDF is visible. |
| P0-3 | **Two live previews** | `#jdf-live-preview` (stream) vs `#live-preview` (compiled prompt) | Label explicitly: “Model stream” vs “Compiled prompt preview”. Consider hiding PEM preview when JDF stream is active. |
| P0-4 | **Save pill clobbered by JS** | `setSavePill` sets `textContent` on `#save-status`, wiping `(v1)` and breaking `data-i18n` | Update pill to `{label} (v{n})` pattern: keep `#version-display` child, only update label span; call i18n catalog for states (`jdf.save.saving`, etc.). |
| P0-5 | **`#version-display` never updates** | Static `1` in HTML; `save_jdf_revision` returns version in API | On successful save, set `#version-display` from `data.version` or revision count. |
| P0-6 | **Runtime English leaks** | `#btn-stop-stream`, truth badge text, stream/save pill strings in `jdf_canvas.js` | Add i18n keys in all 7 locales; use catalog in JS or `data-i18n` + class toggles only. |
| P0-7 | **Empty canvas state** | Blank `#jdf-render-target` until fetch completes | Add skeleton or “No sections yet — double-click to edit or run Inquire” placeholder in `render()` when `body` is empty. |

### P1 — after EC2 deploy / first user feedback

| # | Issue | Evidence | Recommended fix |
|---|--------|----------|-----------------|
| P1-1 | **History / Library / Learn not on v2** | Only JDF block uses v2 tokens; other panes use legacy spacing | Incremental token pass on `#pane-history`, library cards — not launch-blocking. |
| P1-2 | **Full-viewport JDF mode** | Workbench embedded under header + hero | Optional “Expand workbench” control: fixed overlay using global `.workspace-shell` at `100dvh` (hide site hero). |
| P1-3 | **`role="tree"` semantics** | `#jdf-render-target role="tree"` but nodes are articles without `aria-*` tree props | Use `role="document"` on canvas or implement treeitem/aria-level on sections. |
| P1-4 | **Reduced motion** | `.is-streaming-skeleton` shimmer always runs | `@media (prefers-reduced-motion: reduce) { .is-streaming-skeleton::after { animation: none; } }` |
| P1-5 | **Pricing copy drift** | `upgrade.hint` $19 vs audit standard $5/100 Sends | Single source in `i18n.py`; align with landing pricing page. |
| P1-6 | **Stop stream discoverability** | `#btn-stop-stream` hidden until stream starts; no keyboard shortcut | Document in workbench hint; optional Esc-to-abort when streaming (partially: Escape exits surgical mode only). |
| P1-7 | **Red-hat findings region** | `#redhat-findings` populated on SSE but no heading | Add small “Red-hat findings” label with `data-i18n` when content appears. |

## What already passes (keep)

- **50/50 layout + command deck** inside `.jdf-workbench.workspace-shell` — correct structure; deck does not stack as a third column.
- **Runtime CSS bindings** — `jdf-node-body`, `node-toolbar`, `dock-btn`, `node-target`, aperture prev/next, diff cards match `jdf_canvas.js`.
- **Production IDs** — `#inquiry-input`, `#btn-inquire`, `#jdf-render-target`, `#save-status`, `#truth-ledger-badge`, `#btn-export-docx` wired with legacy fallbacks.
- **Trust palette** — primary, success, danger, warning tokens consistent with landing Trust Blue.
- **Mobile stack** — 768px column layout and table `overflow-x: auto` present.
- **Cache bust** — `assure-37` / `assure-24` bumped with visual/JS changes.

## Viewports checked

Code/CSS review against profile viewports (no live browser CDP this pass):

- [x] **390** — workbench stacks 45–50vh; header + hero + form = high scroll; compose H1 nowrap rule may clip
- [x] **768** — workbench column split; command deck wraps
- [x] **1080** — 50/50 desktop layout; workbench ~720px tall inside page
- [x] **1440** — same; ample horizontal space for canvas toolbars

**Residual:** Live CDP pass recommended post-deploy on `app.getassureai.com` to confirm no horizontal overflow and touch target sizes on real iOS Safari.

## Architecture diagram (current compose page)

```
┌─ site-header (nav, privacy chip) ─────────────────────────┐
├─ page-hero (H1: verify drafts…) ──────────────────────────┤
├─ jdf-workbench.workspace-shell ───────────────────────────┤
│  ├─ app-container: left-pane (inquiry) │ right (canvas)  │
│  └─ command-deck (save, truth, export)                    │
├─ legacy .layout #form (full PEM compose journey) ─────────┤
│     question, intent chips, file upload, Get my answer    │
└─ site-footer ─────────────────────────────────────────────┘
```

**Target for JDF-first launch:**

```
┌─ site-header ─────────────────────────────────────────────┐
├─ compact hero OR none ────────────────────────────────────┤
├─ jdf-workbench (primary, ~full remaining viewport) ───────┤
└─ classic compose (collapsed / separate route) ────────────┘
```

## Residual risk

- Audit is **static** (templates + CSS + JS). Animation timing, SSE stream UX, and DOCX download UX not exercised in browser this pass.
- **Clerk / auth** states (sign-in wall, usage nav) not reviewed pane-by-pane.
- **Observability** (`/health`, backup freshness) is ops-facing — correctly not surfaced in compose UI yet.
- Implementing P0-1 is a **product decision** (JDF-primary vs dual-mode); craft fixes P0-4–P0-7 can land independently.

## Suggested next PR (minimal)

1. `jdf_canvas.js` — save pill + version display + i18n runtime strings  
2. `index.html` — `<details class="classic-compose">` wrapping `#form`, closed by default  
3. `index.html` + `i18n.py` — compose hero variant for JDF-led copy  
4. `style.css` — `prefers-reduced-motion` for shimmer  

Estimated scope: **~150 lines**, no backend changes.
