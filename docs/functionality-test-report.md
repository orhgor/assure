# Assure workbench functionality test report

**Date:** 8 September 2026 (refreshed)
**Primary surface:** https://staging.getassureai.com/app?view=founder (founder shell + state rail).
**Production:** https://getassureai.com/app — **200**, `f4e2d20`, UI `assure-127` (pre–founder shell / no free stack).
**Staging:** https://staging.getassureai.com — **200**, git `3cb82a7`, UI `assure-140`, **`stack: free`** (Gemini 3.6 Flash + DeepSeek live).
**Evidence types:** (a) live browser/API this session, (b) source inspection, (c) inference — unused here.

> **Note:** The detailed PASS/FAIL matrix below is from the **5 September 2026** TipTap/local run. Staging was HTTP 200 then at `19f8c19`. Do not use it for current staging parity until EC2 is recovered and redeployed.

Live LLM compile from this machine returned:

```
Drafting with Claude…
litellm.InternalServerError: AnthropicException - 403 Forbidden
```

So C2 / R1 / F3 / P1 were **not** completed end-to-end with a model. Those rows are N/A or FAIL as noted.

Staging vs local: do not treat staging as having the TipTap overhaul until that commit is pushed.

---

## Method

1. Opened local `/app`, dismissed tab-lockout/tour via DOM.
2. Exercised compile-empty, typing, injected JDF preview (gutters, locks, cross-pane).
3. Clicked sidebar (Projects / Library / Settings / Refine / Compile), New project, Rename.
4. `GET /api/projects/default/export?format=docx` and `json`; `GET /api/projects`.
5. `POST /api/projects/default/draft/stream` (8s probe).
6. Forced TipTap `mount()` and captured the schema exception.
7. Compared remaining IDs to `generate.js`, `jdf_canvas.js`, `export_routes.py`, `projects.js`.

---

## Overall PASS rate

| Count | N |
| :--- | ---: |
| Tests in the brief | 81 |
| PASS | 38 |
| FAIL | 31 |
| N/A (no live LLM / not executed destructively) | 12 |
| **PASS / (PASS+FAIL)** | **38 / 69 = 55%** |

This is not a production SLA. It is this session’s scorecard. N/A rows are excluded from the rate so a missing Anthropic token does not inflate failures.

---

## 1. Core flow (Compile → Verify → Dock → Export)

| ID | Result | Keep/Remove | Notes |
| :--- | :--- | :--- | :--- |
| C1 | PASS | Keep | Textarea accepts input. |
| C2 | FAIL | Fix P0 | SSE starts (`Checking budget…`, `Drafting with Claude…`) then **403 Anthropic**. No tokens in the draft panel this run. Stream UI is wired (`generate-stream-preview`). |
| C3 | PASS | Keep | Toast is **“Describe what to compile first.”** not “Please enter a prompt”. |
| C4 | FAIL | Fix P1 | After `compiled`, code calls `setAllGutterState("verifying")` immediately. Injected `setDraftPreview` alone is grey `unverified`; the real compile path skips a stable grey frame. |
| C5 | PASS | Keep | `verified` enables Dock (left + amber strip) and sets gutters green unless `VIOLATION`. |
| C6 | FAIL | Fix P0 | `verified` **always** sets `auditComplete` and **enables Dock** even when `z3_status === "VIOLATION"`. Gutters go red (`error`) but Dock is not blocked. |
| C7 | FAIL | Keep (change spec) | Compile no longer emits `audit_complete`. Opt-in **Run Stress Test?** sits in the amber strip. Findings go to `#redhat-preview`, not into the strip itself. |
| C8 | PASS | Keep | Dock concatenates `body`, hides strip, `clearDraftPreview`. |
| C9 | PASS | Keep | Discard → `resetUi()` clears preview and restores saved tree. |
| C10 | PASS | Keep | Re-compile calls `startDraftStream()` (resets UI then streams). |
| C11 | PASS | Keep | `GET .../export?format=docx` → 200, `application/vnd.openxmlformats-officedocument.wordprocessingml.document`, 37076 bytes this session. |
| C12 | FAIL | Fix P1 | Export has **no** unverified-node warning (`export_routes.py` always builds DOCX). |
| C13 | FAIL | Fix P1 | Empty/minimal trees still download a Word file. No “No content to export”. |

---

## 2. Stress Test (Red-Hat)

| ID | Result | Keep/Remove | Notes |
| :--- | :--- | :--- | :--- |
| R1 | N/A | Keep | Endpoint `POST /draft/redhat/stream` exists. Not run (compile never reached `verified`). |
| R2 | FAIL | Fix P2 | Skip **does not** collapse the amber strip. It changes copy to “Stress Test skipped…” and hides Skip. Run remains. |
| R3 | N/A | Keep | Menu `data-act="redhat"` → `runRedhatAnalysis("node")`. Needs a docked node + LLM. |
| R4 | PASS | Keep | Button `#redhat-analyze-full-btn` present on Refine. |
| R5 | N/A | Keep | `patchDockedRedhat` + `patchGutterFromRedhat` implemented. |

---

## 3. Canvas interactions

| ID | Result | Keep/Remove | Notes |
| :--- | :--- | :--- | :--- |
| K1 | PASS | Keep | Hover node → 2 matching lock rows `.cross-highlight`. |
| K2 | PASS | Keep | `mouseleave` on canvas root clears checklist tint. |
| K3 | PASS | Keep | `_highlightCanvasNodesByKey` → 1 node ring. |
| K4 | PASS | Keep | `_clearCanvasHighlights` → 0 rings. |
| K5 | PASS | Keep | Click handler on `#generate-lock-checklist` scrolls + `cross-flash`. |
| K6 | PASS | Keep | `#generate-lock-count` opens panel + `_flashAllLockedNodes`. |
| K7 | FAIL | Fix P1 | **Draft preview** ignores node clicks (`is-draft-preview`). After dock, click selects and switches to Refine. Left pane does **not** show a properties inspector — only target id + inquiry box. |
| K8 | FAIL | Fix P1 / change spec | Refine is **single-click** (docked), not double-click. Double-click is DOM `contentEditable` on paragraphs only. |
| K9 | FAIL | Fix P2 | Menu: Edit, Revise, Re-prompt, Send for Revision, Run Red-Hat. **No Delete**. Delete is toolbar-only on the DOM renderer. |

---

## 4. Lock glyphs

| ID | Result | Keep/Remove | Notes |
| :--- | :--- | :--- | :--- |
| L1 | PASS | Keep | 🔒 via `.lock-glyph` / `[data-lock-key]` (3 hosts / 6 glyph-related nodes on injected doc). |
| L2 | PASS | Keep | Native `title` is `🔒 {key} = {value}`. |
| L3 | FAIL | Fix P2 | Checklist hover rings the **node**, not a special lock-glyph glow. |

---

## 5. Verification gutter

| ID | Result | Keep/Remove | Notes |
| :--- | :--- | :--- | :--- |
| G1 | FAIL | Fix P2 | Real compile jumps to **verifying** (blue pulse). Grey only if preview is set without `setAllGutterState`. |
| G2 | PASS | Keep | Class `verification-gutter verifying`. |
| G3 | PASS | Keep | `verified` → green. |
| G4 | PASS | Keep | `error` → red. |
| G5 | PASS | Keep | `patchGutterFromRedhat` → `warning` on `p-1`. |
| G6 | FAIL | Keep wall / fix P2 | Workbench is **`display:none` at max-width 1024px** (`#mobile-lockout`). Gutter CSS for ≤768px never appears on phones. Touch 40px targets exist in CSS only. |

---

## 6. Refine (surgical)

| ID | Result | Keep/Remove | Notes |
| :--- | :--- | :--- | :--- |
| F1 | FAIL | Fix P1 | Double-click does not load Refine. Single-click on a **docked** node does; aperture classes exist in DOM render. |
| F2 | PASS | Keep | `#inquiry-input` accepts “Tighten the runway sentence”. |
| F3 | N/A | Keep | `inquire()` + SSE; not run (403 / no selected node in preview). |
| F4 | PASS | Keep | `#btn-cancel-edit` → `exitSurgicalMode`. |
| F5 | PASS | Keep | `#jdf-diff-panel` Original / Proposed (local HEAD). Staging lacks this panel. |
| F6 | PASS | Keep | `#jdf-diff-accept` → `acceptRevisionDiff` (wired; not live-LLM). |
| F7 | PASS | Keep | `#jdf-diff-reject` hides panel. |

Diff Accept/Reject apply to **Send for Revision**, not every Refine.

---

## 7. Re-prompt

| ID | Result | Keep/Remove | Notes |
| :--- | :--- | :--- | :--- |
| P1 | N/A | Keep | Menu Re-prompt fills inquiry + `inquire()`. |
| P2 | FAIL | Fix P1 | Re-prompt streams into `#jdf-live-preview`, **not** a diff card. Diff is `_pendingDiff` only for **Send for Revision**. |
| P3 | N/A | Keep | Accept exists only on the revision diff panel. |
| P4 | N/A | Keep | Same. |

---

## 8. Project management

| ID | Result | Keep/Remove | Notes |
| :--- | :--- | :--- | :--- |
| PJ1 | PASS | Keep | Projects list loaded (default + 4 fixtures). First paint showed “Loading…” then filled. |
| PJ2 | PASS | Keep | + New reveals name field, Create, Cancel. Create not submitted (avoid extra projects). |
| PJ3 | PASS | Keep | Project rows are buttons; `switchToProject` reloads JDF. |
| PJ4 | PASS | Keep | ✏️ → editable input + ✓ / ✕. |
| PJ5 | N/A | Keep | 🗑️ on non-default; `confirm` + DELETE API. Not executed. Default has no delete. |
| PJ6 | PASS | Keep | `confirmLeave`: “You have unsaved changes. Switching projects will lose them. Continue?” not the shorter spec string. |

---

## 9. TipTap editor

| ID | Result | Keep/Remove | Notes |
| :--- | :--- | :--- | :--- |
| T1–T9 | FAIL | Fix P0 | `mount()` throws: `No node type or group 'paragraph' found (in content expression 'paragraph block*')` because StarterKit `paragraph: false` while list/doc schema still requires `paragraph`. Canvas **falls back** to the old DOM renderer. Undo/redo/selection as TipTap: **not available**. DOM dblclick-edit still works on paragraphs. |

Staging does not ship TipTap at all (`19f8c19`).

---

## 10. Left pane (local HEAD only)

| ID | Result | Keep/Remove | Notes |
| :--- | :--- | :--- | :--- |
| LP1 | FAIL | Fix P1 | Dropdown Gemini/Claude/DeepSeek persists `localStorage`. Draft pipeline **ignores** `target_ai`; this run drafted with **Claude** anyway. |
| LP2 | PASS | Keep | Checkbox present; dock skips ledger merge when unchecked (client). Inference still runs on the server. |
| LP3 | PASS | Keep | Empty: “No recent prompts yet.” Push on compile start is wired. |
| LP4 | PASS | Keep | Duplicate no-ops without `surgicalTargetId` (no toast). |
| LP5 | PASS | Keep | Split no-ops if first child / no selection. |
| LP6 | PASS | Keep | Merge same. |

On **staging**, LP1–LP6 controls are **absent** (N/A there).

---

## 11. Multi-prompt collection

| ID | Result | Keep/Remove | Notes |
| :--- | :--- | :--- | :--- |
| M1–M5 | N/A | Keep | Dock `concat` + `prompt_cycle` stamp in `generate.js`. Not proven with three live compiles (403). |
| M6 | FAIL | Fix P2 | `current_version` / save pill version exist. **No UI listing each prompt cycle.** Audit copy mentions version history; canvas has no cycle timeline. |

---

## 12. Additional UI

| ID | Result | Keep/Remove | Notes |
| :--- | :--- | :--- | :--- |
| U1 | PASS | Keep | Settings view activates. |
| U2 | PASS | Keep | Library view activates. |
| U3 | PASS | Keep | `#locale-select` 7 locales; change uses `?lang=` navigation (this session stayed `en` when set in JS without reload). |
| U4 | PASS | Keep | Feedback button opens `#tester-feedback-modal`. |
| U5 | PASS | Keep | Help is an external link, not in-app docs. |
| U6 | PASS | Keep | Save pill showed “◌ Unsaved”. |
| U7 | FAIL | Fix P2 | Engine chip shows **Ready**, not Idle / Processing / Verified / Issues. `AssureCompilerStatus` is a separate control. |

---

## FAILED tests (short)

| ID | What went wrong |
| :--- | :--- |
| C2 | Compile SSE 403 from Anthropic; no draft tokens. |
| C4 / G1 | Compile never holds grey gutters. |
| C6 | Z3 FAIL still enables Dock. |
| C7 | No auto `audit_complete` in the amber strip. |
| C12 / C13 | Export never warns or blocks. |
| R2 | Skip does not hide the draft strip. |
| K7 / K8 | Preview not clickable; Refine is click not dblclick. |
| K9 | No Delete on context menu. |
| L3 | No lock-glyph-specific glow. |
| G6 | App hidden ≤1024px. |
| F1 | Dblclick ≠ Refine. |
| P2 | Re-prompt ≠ diff card. |
| T1–T9 | TipTap schema crash. |
| LP1 | Model selector does not change the server model. |
| M6 | No per-cycle history UI. |
| U7 | Ready pill ≠ four compiler states. |

---

## Features to REMOVE (or stop advertising)

| Feature | Why |
| :--- | :--- |
| Spec “tokens appear in a left-pane draft panel” | Draft is canvas preview; left stream box is hidden after `compiled`. Keep canvas; drop the old cramped-panel story. |
| Spec “Dock disabled on Z3 FAIL” as current behavior | Code does the opposite. Either block dock (fix) or change the test spec — do not document both. |
| TipTap as “done” on staging | Not deployed; local mount crashes. Hide from users until schema is fixed. |
| Model selector as a functional compile control | UI-only; live compile used Claude. Remove or wire `target_ai` before showing it on staging. |
| Mobile gutter QA at 390px | `#mobile-lockout` is the product. Don’t promise phone gutters until that gate is intentional. |
| Context-menu Delete (as a listed item) | It isn’t there. Don’t list it. Toolbar delete is enough **or** add the item. |

Do **not** remove: compile, opt-in Stress Test, dock-append, export DOCX, projects, cross-pane, gutters, lock glyphs, Refine, Help/Feedback.

---

## Features to FIX (priority)

### P0
1. **Compile 403** — local/staging keys for the model actually used (`anthropic/claude-sonnet-4-5`). Without this, C2/R1/F3 are dead.
2. **TipTap schema** — do not set `paragraph: false` without replacing every `paragraph` in StarterKit (lists). Or keep StarterKit paragraph and map it to JDF.
3. **Z3 VIOLATION vs Dock** — disable Accept & Dock (and strip Dock) when math check fails, **or** label Dock as “dock with errors”.

### P1
4. Wire `target_ai` from `#generate-model-select` through `run_draft_pipeline`.
5. Export guards: empty tree error; optional unverified warning.
6. Click vs dblclick: one documented way to enter Refine; enable selection after dock only, with a toast in preview.
7. Re-prompt should open the same Accept/Reject diff as Revision, or the menu labels should match.
8. Push TipTap + left pane only after T1 and LP1 pass.

### P2
9. Skip Stress Test: optional collapse of the Run row; keep strip until Dock/Discard.
10. Context-menu Delete.
11. Lock-glyph hover glow.
12. Prompt-cycle history in the left pane.
13. Align “Ready” chip with compiler states **or** drop U7 from the product spec.
14. Grey gutter frame before verifying, if design still wants G1.

---

## Recommendations (next phase)

1. **Unblock compile** (keys / fallback model) and re-run C2, C5, R1, M1–M3 on staging with one short prompt.
2. **Fix TipTap StarterKit** so `is-tiptap` never sticks after a failed mount (destroy + remove class in `catch` — already attempted; schema must compile).
3. **Ship one honest compile story:** canvas preview → Math Check → optional Stress Test → Dock appends. Update QA tables to that story (C7, C4, K8).
4. **Do not merge left-pane model selector to staging** until the backend honors it.
5. **Export policy:** decide empty/unverified behavior in `export_routes.py` before marketing “verified documents only”.
6. Keep cross-pane, gutters, lock glyphs, projects, opt-in Red-Hat — those passed where they could be tested without a model.

---

## Limitations

- No successful Claude/Gemini completion this session.
- Destructive project delete not run.
- Clipboard undo/redo not exercised (TipTap down).
- Staging UI for LP/T/F5 is older than local HEAD.
- Pass rate is not a substitute for a Playwright suite; several PASS rows are code-verified plus injection, not a full user compile.
