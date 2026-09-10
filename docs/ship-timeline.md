# Ship Timeline — v1.0 Execution Mode

**Clock starts:** After commit `docs: lock v1.0 user experience, rules, and ship timeline`.

**Rule:** If a day slips, cut scope — do **not** extend the timeline. Defer to [deferred.md](./deferred.md).

---

## Daily gates

| Day | Deliverable | Green gate |
| --- | --- | --- |
| **Day 1** | 3-pane layout frozen | Resize window → no break. State rail always 48px. |
| **Day 2** | Golden path test written (fails) | Test runs. First failure logged. |
| **Day 7** | Sprint 1 done — Difference Engine | Test step 3 passes (side-by-side diff). |
| **Day 14** | Sprint 2 done — Hybrid Merge + Polish | Test steps 4–6 pass. |
| **Day 21** | Sprint 3 done — Full-scan + Benchmark | Test steps 7–9 pass. |
| **Day 22–24** | Visual + copy convergence | [visual-checklist.md](./visual-checklist.md) 100% ✅ |
| **Day 25** | ICP demo | One real user completes golden path unaided. |
| **Day 26** | Ship v1.0 | Tag, deploy, announcement. |
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
