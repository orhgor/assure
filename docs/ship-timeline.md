# Ship Timeline — v1.0 Execution Mode

**Clock starts:** After commit `docs: lock v1.0 user experience, rules, and ship timeline`.

**Rule:** If a day slips, cut scope — do **not** extend the timeline. Defer to [deferred.md](./deferred.md).

**Day 2 gate: PASS** — `npx playwright test tests/e2e/golden_path.spec.js` runs; Steps 1–2 pass on `cb865f2`.

**Sprint 1 (Difference Engine) — merged 2026-09-10:** `POST /api/projects/<id>/orchestrate`, `.staging-canvas` with Claude/DeepSeek panes, `.diff-highlight`, `.push-to-main-btn` click-to-merge. **Staging live 2026-09-10:** free stack (`gemini/gemini-3.6-flash` + `deepseek/deepseek-chat`), `POST /api/runs/compare`, UI `assure-140`.

**Sprint 2 Step 6 (Polish Main) — merged 2026-09-10:** `POST /api/projects/<id>/polish`, `.main-polish-btn`, lock-pill `strict_preservation`, inline diff preview. Golden path Step 6 blocked until Step 5 (Red-Hat) exists.

**Scope cut (2026-09-10):** v1.0 ship gate = **Steps 1–4 only**. Steps 5–10 → [deferred.md § v1.1 backlog](./deferred.md). PR #46 scan code may exist on staging; not in v1.0 validation.

---

## Daily gates

| Day | Deliverable | Green gate |
| --- | --- | --- |
| **Day 1** | 3-pane layout frozen | Resize window → no break. State rail always 48px. **✅ PASS** |
| **Day 2** | Golden path test written (fails) | Test runs. First failure logged. **✅ PASS** |
| **Day 7** | Sprint 1 done — Difference Engine | Test steps 3–4 pass (side-by-side diff + click-to-merge). **🟡 VERIFY** — staging live (`3cb82a7`, free stack); run E2E Steps 1–4 on `staging.getassureai.com` |
| **Day 14** | ~~Sprint 2 polish + Red-Hat~~ | **Deferred v1.1** — Steps 5–6 not v1.0 gate |
| **Day 21** | ~~Sprint 3 scan + benchmark~~ | **Deferred v1.1** — Steps 7–9 not v1.0 gate |
| **Day 22–24** | Visual + copy convergence | [visual-checklist.md](./visual-checklist.md) 100% ✅ |
| **Day 25** | ICP demo | One real user completes **Steps 1–4** unaided (orchestrate → diff → merge). |
| **Day 26** | Ship v1.0 | Tag, deploy, announcement — **Steps 1–4 green**; v1.1 backlog unchanged. |
| **Day 27+** | Deferred list only | Nothing new. Only [deferred.md](./deferred.md). |

---

## Execution flow

```
FROZEN SHELL (Day 1)
   │
   ▼
GOLDEN PATH TEST (Day 2)
   │  └─ Defines "done" forever
   │
   ▼
BUILD BY SPRINT (Days 3–21)
   │  ├─ Only golden path code
   │  ├─ Visual issues → docs/deferred.md
   │  ├─ Layout bugs → screenshot prompt to Cursor → fix immediately
   │  └─ Scope requests → docs/deferred.md (no exceptions)
   │
   ▼
CONVERGE (Days 22–24)
   │  ├─ Walk visual checklist against Executive-Grade Standard
   │  ├─ Read every string aloud; rewrite anything robotic
   │  └─ Zero new features. Only the checklist.
   │
   ▼
ICP DEMO (Day 25)
   │  └─ One real user. Take notes. Do not coach them.
   │
   ▼
SHIP v1.0 (Day 26)
   │
   ▼
v1.1 BACKLOG (Day 27+)
   └─ Only what the ICP demo revealed + docs/deferred.md
```

---

## Human role in the loop

Cursor does the labor. **You** make the calls. Do not outsource judgment.

| Task | Who |
| --- | --- |
| Functional code | Cursor |
| Feature scope decisions | **You** — nobody else can decide what is v1.0 |
| Visual acceptance | **You** — take the screenshot, decide if it's good enough |
| Copy wording approval | **You** — read every string aloud, approve or rewrite |
| ICP demo | **You** — nobody else can show a real user |
| "Ship it" decision | **You** — no AI can make this call |
| Rollback decision | **You** — if production breaks, only you decide |

---

## Single rule

**The shell is frozen on Day 1. The golden path test decides done. Everything else goes to [deferred.md](./deferred.md).**
