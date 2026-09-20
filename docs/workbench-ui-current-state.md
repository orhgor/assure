# Assure Workbench — Current UI State

**Purpose:** Baseline for the next UI propagation prompt (revise, add, enhance).
**Branch / deploy snapshot:** Git `staging` @ `3cb82a7` (PRs #46–#51). **Production live** `f4e2d20` / `assure-127`. **Staging EC2** **healthy** — UI `assure-140`, free stack (`gemini-3.6-flash` + DeepSeek), ~9.6 GB disk free.
**Live inventory (product):** `docs/product-status.md` (current snapshot) · `docs/assure-ai-all-functions.md` §3.

### Planned next — research synthesis (PR 1 / PR 2)

**Spec:** [runbooks/research-synthesis-pr1-pr2.md](./runbooks/research-synthesis-pr1-pr2.md)
**Blocked on:** golden path Steps 5–10 (Red-Hat multi-pass, benchmark, export complete). Staging infra recovered 2026-09-10.

| Gap today | PR 1 fix | Persistence |
|-----------|----------|-------------|
| No run detail modal | `GET /api/runs/<id>` + UI modal | Already on server |
| No iterate from prior run | `parent_run_id` + `previous_context` on `POST /api/runs` | SQLite `runs` v23 |
| Verify/Red-Hat only per-run | `POST /api/drafts/verify`, `/api/drafts/redhat` | EC2 pipeline |
| No draft version history | `draft_snapshots` table + snapshot/restore APIs | SQLite — **not** LocalStorage |
| Compare / merge | PR 2 — client diff + selective insert to Active Draft | Draft via existing `PUT /api/drafts` |

Active draft autosave already uses **`PUT /api/drafts`** → `drafts` table (`founder_draft.js`).

---

### What changed since the last status pass (2026-09-08)

| Area | Before | Now (on `staging` git) |
|------|--------|------------------------|
| Layout shell | Multi-column sidebar + canvas | **Founder shell** — 48px `#state-rail` + 320px runs column + canvas (`48px \| 320px \| 1fr`) |
| Runs / filters | Single list | **`#runs-stack`** with `data-filter`, empty states, card button hierarchy |
| Hotkeys | Unguarded | **Shift+1–5** state filters; **Cmd+K** guarded in `command_bar.js` + `workbench_ux.js` |
| Compile pipeline | Inline stream | **Orchestrator** SSE — tokens → parse claims → `verification_complete` lock pills |
| Header | Show Workspaces / Active Draft eyebrow | Removed; document title **Untitled Document** |

---

## 1. Current Layout & Architecture

| Question | Answer |
|----------|--------|
| **1.1 Workbench structure** | **Multi-column.** Shell: **header** → **icon sidebar** + **left tool pane** + **resizable splitter** + **right JDF canvas** → **command-deck footer** → **disclaimer strip**. Status bar sits above content. Root: `#assure-app` / `#workbench-root`. Analytics uses **`mode-full-view`**: left pane expands, canvas hidden. |
| **1.2 Navigation** | Sidebar: **Write**, **Draft**, **Polish**, **Sources**, **Analytics**, **Settings**. Analytics is an **in-shell view** (`AssureNav.switchView("analytics")` → `#view-analytics`), not a location change. Header: logo, trust strip, **role switcher**, locale, engine status. Shared left accordions (hidden on Projects and Analytics): Sources, Argument Spine, Document Structure, Refine Workspace. |
| **1.3 Primary actions** | **Document Lifecycle stepper** in Draft (`#view-generate`): (1) **Assemble**, (2) **Full Audit**, (3) **Red-Hat**, (4) **Accept & Dock** (`#generate-accept-dock-phase` only). **Lock version:** command-deck left `#compliance-lock-btn`. **Download:** `#export-menu`. **Audit Report:** `#btn-audit-manifest`. Model / lock toggle / quick node actions: **Advanced**. |
| **1.4 Footer** | Command deck: compiler status, version slider, lock, signature, restore; right: shortcuts, save pill, Download, Audit Report, Reasoning Graph, More (PDF, JSON/YAML, Sign-Off, Decision Log). Disclaimer strip below. Status bar `#workbench-status-bar` above the panes. |

---

## 2. Button System

| Question | Answer |
|----------|--------|
| **2.1 Current variants** | `.btn` + `.btn-primary`, `.btn-outline`, `.btn-ghost`, `.btn-success`, `.btn-danger`, `.btn-sm` / `.btn-lg`, `.btn-feedback`, `.btn-trust-action`, `.btn-trust-redhat`, **`.btn-text-subtle`** (stepper Advanced). Step actions also use **`.step-action-btn`** (full-width, 11px). |
| **2.2 States** | Hover, `:focus-visible`, active, disabled, `.is-busy` spinner. Tooltips: `.tooltip-trigger` + `data-tooltip`. Stepper items: `.is-active`, `.is-completed`, `.is-disabled` (visual; steps are not `pointer-events: none` so Assemble/Full Audit stay clickable). |
| **2.3 Sizing** | Default `.btn` padding via `--spacing-*`. Stepper actions forced `btn-sm` + 4px/8px padding. Command deck mostly `btn-sm`. Mixed `--spacing-*` / `--space-*` tokens remain. |
| **2.4 Icons** | No icon library. Unicode emoji in sidebar; SVG brand mark; ⓘ for provenance. Stepper uses **numeric markers** (1–4) that become **✓** when completed. |
| **2.5 Inconsistencies** | (1) Emoji vs professional trust UI. (2) Duplicate CSS blocks for `.btn-primary`. (3) `#generate-accept-dock` CSS still in `style.css` though the node was removed. (4) Stepper `role="tab"` without a tablist/tabpanel pattern. (5) Full Audit still **re-compiles** rather than verifying the existing draft. |

---

## 3. Trust Signals & Provenance

| Question | Answer |
|----------|--------|
| **3.1 Z3 verification display** | Still multi-layer: verification **gutter**, confidence overlay (`.bg-green-200` / yellow / red), **ink stamps**, **laser beam**, wow **checkmarks**, status bar, TipTap `.z3-reason-icon`. **Not unified** this sprint. |
| **3.2 Provenance information** | ⓘ (`.provenance-info-btn`) on verified nodes → **`#provenance-panel-drawer`**. Open adds `.is-open`, removes `hidden`, `aria-hidden="false"`. Shows source, page, excerpt, Z3 rule, confidence bar, verified-at, “View Full Source”. Escape / close button dismisses. |
| **3.3 Audit Bundle export** | Still labeled **Audit Report** in the command deck (`#btn-audit-manifest`). |
| **3.4 Confidence explanation** | Overlay legend + `data-confidence-reason` + tooltips. No numeric-formula popover. |

---

## 4. Interactions & Micro-UX

| Question | Answer |
|----------|--------|
| **4.1 Surgical refinement** | Unchanged: Polish view + Refine Workspace; node click → floating bar + `#jdf-surgical-popover`. |
| **4.2 Diff view** | Unchanged: left `#jdf-diff-panel` **and** canvas x-ray overlay (dual surfaces). |
| **4.3 Reasoning graph** | Unchanged: `#wow-reasoning-graph-btn` in footer → Cytoscape drawer. Still a third right-rail vs provenance. |
| **4.4 Loading states** | Assemble: `#generate-compiling`, `#gate-loader`, SSE. Verify: laser + stamps (wow). **Stepper** listens for compile/audit custom events. Laser is still **end-of-SSE**, not per-node progress. |
| **4.5 Stepper flow** | `AssureStepper` (`workbench_stepper.js`): start → Write active; `assure:compile:verified` → Write+Verify completed, Audit+Ship highlighted; `assure:audit:complete` (Red-Hat finish **or skip**) → Ship active. Dock button enablement still owned by `generate.js`. |

---

## 5. Responsiveness & Accessibility

| Question | Answer |
|----------|--------|
| **5.1 Responsive layout** | Desktop-first. Stepper: 4 columns, **2×2 under 1024px**. Analytics charts: 2-col → 1-col under 1024px. Mobile hint `#mobile-lockout` still dismissible, not a hard block. Collapsible sidebar. |
| **5.2 Keyboard** | Shortcuts: Assemble ⌘↩, Accept & Dock ⌘⇧D, `?`. Escape closes provenance. Stepper tabs are not a real ARIA tab widget. |
| **5.3 Focus indicators** | `:focus-visible` on `.btn` and controls. |

---

## 6. CSS & Design System

| Question | Answer |
|----------|--------|
| **6.1 Styling framework** | Custom CSS `style.css`. No Tailwind CDN. Chart.js 4.4.1 from jsDelivr on the workbench (and leftover `/analytics` page). Fonts: IBM Plex Sans / Mono. |
| **6.2 Design system** | CSS variables: trust blue, confidence green, surfaces, borders. Stepper/analytics reuse `--surface-card`, `--border-color`, `--text-muted` with hex fallbacks. |
| **6.3 Components** | `.workbench-stepper-container`, `.step-item`, `.analytics-chart-card`, `.analytics-kpis`, drawers, `.command-deck`, `.assure-view`. |

---

## 7. Known Issues

| Question | Answer |
|----------|--------|
| **7.1 Remaining UI issues** | (1) **Wow stack still cluttered** — gutter + overlay + stamp + check + ⓘ. (2) **Dual diff** (left panel + x-ray). (3) **Reasoning graph** orphaned in footer. (4) **Full Audit** re-runs compile. (5) **Active Works** dashboard hierarchy not addressed. (6) Standalone **`/analytics`** page remains (backend unchanged). (7) Chart dataset labels **hardcoded English**. (8) Dead CSS for `#generate-accept-dock`. (9) Docs drift (`assure-ai-all-functions.md`, `product-status.md`). (10) Production behind this branch. |
| **7.2 User feedback** | Still **no structured pilot quotes** in repo. Empty `docs/demo/insurance-boston-real-estate/feedback-template.md` — moved off this branch 2026-09-18, now on `test-fixtures` (`git show test-fixtures:docs/demo/insurance-boston-real-estate/feedback-template.md`). |

---

## 8. Analytics Dashboard (post-sprint)

**Verdict:** Embedded and size-constrained. Leftover standalone route is unused by the sidebar.

| Item | Detail |
|------|--------|
| Navigation | `#view-analytics` in left pane; `mode-full-view` hides canvas. No `window.location.href = "/analytics"`. |
| Layout | KPIs 3-col; charts `.analytics-charts-grid` 2-col; cards **height 280px**, canvas **max-height 200px**. Velocity table spans full width. |
| Chart.js | `responsive: true`, `maintainAspectRatio: false`; destroy/recreate on re-entry (`AssureAnalytics.render()`). Brand colors (`#1A4B8C`, `#2e7d32`) instead of Material greens. |
| Load | Fetch only when the analytics view opens (workbench). Standalone `/analytics.html` still auto-boots if those IDs exist without `#assure-app`. |
| Remaining | `/analytics` route (Python router untouched); English chart labels; no skeleton/empty illustration; table copy not i18n. |
| Tests | `test_analytics_embedded_constraints` asserts first `.analytics-chart-card` height ≤ 280px. Legacy `test_analytics_dashboard_loads` still hits `/analytics`. |

**Files:** `templates/index.html` (`#view-analytics`), `static/analytics.js`, `static/app_nav.js`, `templates/analytics.html` (legacy).

---

## 9. Wow Effects — Still a Bolt-On Layer

**Verdict:** **Not part of this sprint.** Laser, ink stamps, diff x-ray, reasoning graph remain in `wow_effects.js`, triggered from `generate.js` / `jdf_canvas.js`, optional via `?wow=0` / `__ASSURE_WOW_EFFECTS__`.

Still true:

- Laser arms on click and sweeps on `verified` SSE — not tied to gate-loader phases.
- Stamps / checkmarks stack with gutters, overlay, and provenance ⓘ.
- Provenance preview still treats `.ink-stamp` as a verified hint.
- X-ray duplicates `#jdf-diff-panel`.
- Reasoning graph is a separate canvas drawer from provenance.

**Propagation:** unify or remove; do not treat laser/stamps as “integrated” yet.

---

## 10. Four-Step Workflow (post-sprint)

**Verdict:** The broken grid is **replaced**. Remaining gaps are semantics and a11y, not “empty Audit column.”

| Item | Now |
|------|-----|
| Markup | `.workbench-stepper-container` > `.stepper-timeline` > four `.step-item`s with `.step-marker` + `.step-action-btn`. |
| Audit | **Red-Hat** `#generate-redhat-btn` → `AssureGenerate.runRedhatStress()`. Always visible (not `workbench-advanced-only`). |
| Accept & Dock | **Only** `#generate-accept-dock-phase`. Summary and draft strip duplicates removed. Draft strip keeps **Discard**. `generate.js` `dockButtons()` still accepts a legacy `#generate-accept-dock` if present. |
| Advanced | `#workbench-advanced-toggle-btn` + hidden `#workbench-show-advanced`. Toggles `workbench-advanced-on` / `off` via `AssureWorkbenchClarity`. |
| Events | `assure:compile:start`, `assure:compile:verified`, `assure:audit:complete` (including Red-Hat skip). |
| Still wrong | Full Audit = **new compile stream** with `fullAudit: true`, not “verify this draft.” No stepper connectors. `role="tab"` without tab pattern. 800ms dock-sync interval **removed**. |

**Files:** `index.html`, `workbench_stepper.js`, `workbench_clarity.js`, `generate.js`, `style.css`, `test_ui_revision_sprint.py`, `test_action_grouping.py`, `test_clarity.py`.

---

## Quick reference — key files

| Area | Files |
|------|--------|
| Shell / markup | `prompt_matrix/templates/index.html` |
| Styles | `prompt_matrix/static/style.css` |
| Stepper | `prompt_matrix/static/workbench_stepper.js` |
| Founder shell / state rail | `prompt_matrix/static/founder_shell.js`, `state_rail.js`, `runs_stack.js` |
| Orchestrator / command bar | `prompt_matrix/services/orchestrator.py`, `static/command_bar.js`, `static/orchestrator.js`, `routers/orchestrator_routes.py` |
| Polish Main | `prompt_matrix/services/polish_document.py`, `static/founder_polish.js`, `routers/polish_routes.py` |
| Compile / SSE / dock | `prompt_matrix/static/generate.js` |
| Nav | `prompt_matrix/static/app_nav.js` |
| Analytics | `prompt_matrix/static/analytics.js` (`templates/analytics.html` leftover) |
| Wow | `prompt_matrix/static/wow_effects.js` |
| Provenance | `prompt_matrix/static/provenance_panel.js` |
| Roles / clarity | `prompt_matrix/static/role_workbench.js`, `workbench_clarity.js` |
| i18n | `prompt_matrix/i18n.py` — includes `stepper.title`, `stepper.advanced`, `generate.redhat_short` in all 7 locales |
| Tests | `tests/playwright/test_state_rail.py`, `test_ui_revision_sprint.py`, `test_action_grouping.py`, `test_clarity.py`, `test_provenance_panel.py` |

---

## Suggested next-prompt anchors

1. **Preserve** multi-column shell, lifecycle stepper, embedded analytics, slide-in provenance.
2. **Unify trust chrome** — one node decoration system (pick gutter **or** stamp, keep ⓘ as inspect).
3. **One diff surface** — x-ray or left panel, not both.
4. **Full Audit** as a gate on the current draft, not a second Assemble.
5. **Reasoning graph** inside the provenance/inspect drawer.
6. **Active Works** visual hierarchy (not started).
7. **Retire or redirect** `/analytics` when backend changes are allowed.
8. **Promote** founder shell to production after staging EC2 recovery + green `/health` (`assure-124`, `build_sha` starts with `ffd422f` or later).

---

## Revision checklist

| Area | State | Next stance |
|------|--------|-------------|
| Action-phase stepper | Shipped | **Keep**; polish connectors / a11y |
| Accept & Dock | Single button | **Keep** |
| Full Audit = re-compile | Still true | **Fix** |
| Laser / stamps | Bolt-on | **Fix or remove** |
| Dual diff | Still true | **Fix** |
| Reasoning graph | Footer orphan | **Fix** |
| Analytics embedded + 280px | Shipped | **Keep**; i18n labels / drop standalone page |
| Provenance slide drawer | Shipped | **Keep**; make it the inspect hub |
| Show advanced | Header control | **Keep** |
| Active Works dashboard | Unchanged | **Fix** (next sprint) |
