# Frozen Shell — Day 1 Layout Contract

**Status:** LOCKED on Day 1. No layout changes during Sprints 1–3 except bug fixes that restore this contract.

---

## 3-pane grid

Founder workbench uses a fixed CSS grid on `.app-container.founder-workbench`:

| Column | Width | Content |
| --- | --- | --- |
| 1 — State rail | **48px** (fixed) | `#state-rail` — Sources, Runs, Red-Hat, Grammar |
| 2 — Left pane | **320px** (collapsible to 0) | Runs stack, substrate vault |
| 3 — Main canvas | **minmax(400px, 1fr)** | Main document (`#founder-draft-editor`) |
| 4 — Right drawer | **0px / 400px** | Evidence, staging diff (slides in) |

### Grid templates (CSS)

```
Default:              48px  320px  minmax(400px, 1fr)  0px
Left closed:          48px  0px    minmax(400px, 1fr)  0px
Right open:           48px  320px  minmax(400px, 1fr)  400px
Left closed + right:  48px  0px    minmax(400px, 1fr)  400px
```

Source: `prompt_matrix/static/style.css` — `.app-container.founder-workbench` grid rules.

---

## Day 1 green gate

| Check | Pass criteria |
| --- | --- |
| State rail width | Always **48px** at any viewport ≥ 1024px |
| Resize | 1024px → 1920px → no overflow, no pane overlap |
| Rail visible | `#state-rail` always visible in founder mode |
| Main document | `#founder-draft-editor` always in column 3 |
| No legacy shell | `body.founder-workbench:not(.legacy-workbench)` active |

Automated: `tests/quality_check/test_frozen_shell.py`

---

## What is frozen vs what moves

| Frozen (do not change) | Allowed during sprints |
| --- | --- |
| 48px state rail | Content inside panes |
| 320px left pane width | Staging area diff UI (column 4 drawer) |
| Grid column order | Feature JS inside existing panes |
| minmax(400px, 1fr) canvas minimum | New API endpoints |

Layout bugs (break the gate) → fix immediately with screenshot prompt.
Visual polish → [deferred.md](./deferred.md) until Days 22–24.
