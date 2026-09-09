# Workbench i18n & redundancy audit — 2026-09-09

Scope: `/app` founder shell + legacy workbench (`prompt_matrix/templates/index.html`, workbench JS, `i18n.py`).

## Mixed language — fixed this pass

| Surface | Issue | Fix |
|---------|-------|-----|
| Locale bootstrap | Bare `/app` forced `lang=en`, wiping stored locale | Respect `localStorage.assure_locale`; default `en` only on first visit |
| ⌘K command bar | Label, placeholder, drop zone, status strings hardcoded EN | `command.bar.*` keys + `data-i18n*` on modal; `command_bar.js` uses `translate()` |
| Evidence Inspector | Title, meta labels, loading/error copy hardcoded EN | `evidence.inspector.*` keys + `data-i18n`; `evidence_inspector.js` wired |
| Runs stack cards | Status pills, Send/Delete buttons, dismiss prompt hardcoded EN | `founder.runs.*` keys; `runs_stack.js` uses `translate()` |
| Settings modal | “Need an API key?” banner hardcoded EN | `settings.api_key_*` keys + `data-i18n` |

Cache bust: `APP_JS = assure-127`.

## Terminology — “directive” / yönerge (fixed 2026-09-09)

**Problem:** English internal jargon *directive* was translated to Turkish **yönerge**, which means an official regulation/circular — not “what do you want to investigate or draft?” The legacy Turkish workbench already uses **soru** (question) throughout.

| Key | Was (TR) | Now (TR) | EN source (updated) |
|-----|----------|----------|---------------------|
| `founder.state_rail.directive` | Yönerge | **Soru** | Investigate |
| `founder.cmdk_btn` | ⚡ Yönerge… | **⚡ Soru…** | ⚡ Investigate… |
| `command.bar.label` | İnceleme yönergesi | **Ne incelenecek?** | What to investigate |
| `command.bar.enter_directive` | Önce bir yönerge girin | **Önce bir soru yazın** | Describe what to investigate first |
| `command.bar.placeholder` | …taslağa döküyoruz | **…taslak olarak yazıyoruz** | (unchanged EN) |
| `surgical.click.instruction_label` | Yönergeler | **İsteğiniz** | Instructions |

**Still review (TR, lower priority):**

| Key | Current TR | Note |
|-----|------------|------|
| `founder.state_rail.label` | Tezgah aşamaları | Literal “bench stages”; consider **Çalışma aşamaları** |
| `founder.state_rail.dossier` | Dosya Dışa Aktarma | *Dosya* = file; dossier may need **Dossier dışa aktarma** (loanword) |
| `refine.no_intent` | geliştirme talimatı | *Talimat* OK here (user instruction, not yönerge) |

## Mixed language — still open

~**121–147 workbench keys per locale** still equal English (catalog drift). Largest clusters:

| Prefix | Count (typical) | Examples |
|--------|-----------------|----------|
| `wizard.*` | 16 | New-project wizard steps |
| `jdf.*` | 19–21 | Canvas save/stream/refine toasts |
| `role.*` | 13 | Role switcher labels |
| `generate.*` | 11–12 | Draft header, compile badges |
| `coachmark.*` | 8 | Onboarding tour |
| `projects.*` | 8–9 | Empty states, workspace menu |
| `templates.*` | 8 | Template picker |

**JS still injecting English at runtime** (lower priority, legacy paths):

- `jdf_canvas.js` — save/stream status toasts
- `audit_gate.js` — “Running math check…”
- `inquire_client.js` — “Streaming…”, “Saved”
- `jdf_tiptap.js` — “⚡ Cached”, table labels
- `projects.js` / `new_project_wizard.js` — unsaved-changes confirm, wizard blurbs

**HTML gaps** (have `gettext()` but missing `data-i18n` — breaks live language switch):

- `#btn-cancel-edit`, several `aria-label`s in compose/refine panels
- Inline admin strings in bottom `<script>` block (classes/history admin — low traffic)

## Redundancies (founder vs legacy)

Founder mode (`founder_shell.js`) parks legacy DOM into `#founder-legacy-park` and hides `.founder-legacy-chrome`. Redundancy is **intentional layering**, not accidental duplication in the default path.

| Overlap | Founder path | Legacy path (`?legacy=1`) | Recommendation |
|---------|--------------|---------------------------|----------------|
| ⌘K entry | `#founder-cmdk-btn` → command bar modal | `#header-command-palette-btn` → palette overlay | Keep both; legacy hidden in founder mode |
| Navigation | State rail (Directive/Runs/Grounding/Red-Hat/Dossier) | Sidebar (Workspaces/Sources/Analytics/Settings) | Document split; sidebar parked in founder mode |
| Draft editor | `#founder-draft-shell` (TipTap) | `#panel-draft` compose | Legacy panel CSS-hidden in founder mode |
| Export | `#btn-export-dossier` (founder-only) | `#export-menu` + `#btn-audit-manifest` | Founder shows dossier only; format menu in legacy chrome |
| Red-Hat | Runs card button + state-rail stage | `#generate-redhat-btn`, `#executeRedHatBtn`, `#redhat-run-btn`, surgical view | Map by context; surgical view parked |
| Settings | `#settings-overlay` | `#settings-modal` (API keys dialog) | Merge later; `#export-audit-btn` duplicates manifest export |
| Status | `#workbench-health` | `#save-status`, `#compiler-status`, `#workbench-status-bar` | Status bar parked in founder mode |

**Quick wins (defer until post-launch):**

1. Remove `#export-audit-btn` from settings when `#btn-audit-manifest` is visible.
2. Collapse `#view-surgical` Red-Hat block entirely in founder mode (already parked).
3. Strip dead `#page-hero` block from `/app` template (CSS-hidden).

## Verification

After deploy (`assure-126`):

1. `/app?lang=tr` — open ⌘K, Evidence Inspector (lock pill), Runs card actions → all Turkish.
2. Switch locale via selector → strings update without full English flash.
3. `/app?legacy=1&lang=es` — legacy chrome still works; no founder-only strings leak.

## Related

- Marketing i18n audit: `docs/audits/2026-09-09-marketing-i18n-mixed-wording.md`
- Staging launch runbook: `docs/runbooks/staging-launch-execution.md`
