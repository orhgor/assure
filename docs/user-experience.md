# Assure AI — User Experience (v1.0 Definition)

**Status:** Locked. Execution Mode starts after commit `docs: lock v1.0 user experience, rules, and ship timeline`.

---

## 1. Who the user is

Assure is for founders, quality assurance professionals, corporate executives, and compliance officers (like insurance claim advisors). Their job is to create binding, high-stakes documents, policies, or analyses where being wrong costs money or legal liability. They need to extract the absolute best from multiple LLMs, but they are afraid of hallucinations, unverified data, and the tedious friction of manually comparing AI outputs.

## 2. What the user wants to accomplish

To author, compile, and output mathematically verified, bulletproof documents and data analysis without ever doubting the underlying truth of the content.

## 3. The Golden Path

*(The exact steps from opening the app to the final outcome)*

1. I start my workbook or continue from where I left off in my Main document.
2. I write an intent (a question, an instruction, or adding a source document) in the orchestrator command bar.
3. I ask 2–3 different models simultaneously and see their responses generated side-by-side in a staging area, **with the exact differences between them visually highlighted.**
4. I review the differences and choose which response—or a **hybrid combination** of the best parts from both—gets pushed into the Main document.
5. I run a local or full Red-Hat analysis (2 times minimum) on these compiled responses to expose hallucinations or missing references.
6. Once the data is in the Main document, **I let the AI do the final revisioning here**—fixing grammar, polishing tone, and creating the correct explanation flow without deleting the verified data.
7. I trigger Assure to do a full-context analysis of the Main document to scan for verifiable issues, numbers, and citations, finding exactly where the document must be strengthened.
8. I ask the AI to show what benchmarked/standard works do, and enhance specific weak points by doing local (targeted) prompting.
9. I select specific parts in the Main document to rewrite, reconfigure, or reanalyze until perfect.
10. In all prompting, Assure writes the correct prompt structure, finds the best-coupled LLMs, runs Red-Hat, displays the side-by-side differences, and lets me cleanly compile the final work.

## 4. What the user sees at each step

- **What is on the screen:** A dynamic workspace. The **Main Document** is anchored as the final compilation zone. When a query is run, an **AI Staging Area** opens, showing two model responses side-by-side. The UI explicitly highlights where Model A differs from Model B.
- **What is the primary action:** *Triage, Hybrid-Merge, and Polish.* The user compares the highlighted differences, clicks to merge their preferred parts into the Main document, and then triggers grammar/flow revisions directly on the Main canvas.
- **What the system does in response:** It translates raw human intent into complex prompt chains, routes them to optimal LLMs, runs a diff-engine to highlight discrepancies between the models, and executes Z3/Red-Hat verifications on the compiled text.

## 5. What the user never has to do

- **No copy-pasting:** The user never copy-pastes between ChatGPT, Claude, and MS Word.
- **No manual text comparison:** The user never has to read two AI responses side-by-side to guess what is different; the system highlights the discrepancies automatically.
- **No prompt engineering:** The user never has to write complex "Act as a lawyer…" prompts. The system writes the prompt.

## 6. What "done" looks like for v1.0

When I (the creator) can personally open Assure, write an intent, watch the system highlight the differences between two models, seamlessly hybrid-merge the best parts into the Main document, and run a final grammar polish to output a flawless dossier.

## 7. What is explicitly OUT of scope for v1.0

- Building a generic consumer chatbot interface.
- Exact replication of existing benchmarked platform features that don't directly serve the verifiable JDF compilation workflow.

## 8. How I will know it works

An insurance claims officer uploads a policy, types a raw question, watches Claude and DeepSeek generate answers side-by-side, instantly sees the highlighted difference in how they calculated a liability limit, selects the correct calculation to push to the Main document, and runs a final AI polish for professional grammar—achieving defense-grade accuracy in minutes.

---

## The Power of the Difference Engine

By defining Step 3 (highlighting differences) and Step 4 (hybrid merging), you have created a workflow that solves the biggest problem with using multiple AIs: *cognitive overload*.

Instead of reading 500 words from Claude and 500 words from DeepSeek to figure out who is right, Assure just highlights the 3 sentences where they disagree. The user makes a choice, pushes it to the Main document, and lets the Main document handle the grammar and flow (Step 6).

---

## Technical path to v1.0

Follow this build order. Do not skip ahead.

### Sprint 1 — Difference Engine (Days 3–7)

1. **Orchestrator endpoint:** Create a single Celery task (`assure.run_multi_model_orchestrator`) that takes the user's intent, sends it to Claude **and** DeepSeek at the same time, and returns both JDF arrays to the frontend.
2. **Diff UI (staging area):** Dual-column layout inside the Staging Area. Claude on the left, DeepSeek on the right. Run `ast_diff.py` on them to highlight exactly where DeepSeek added a number or Claude removed a clause.
3. **Click-to-merge:** Hover over a highlighted block, click merge, and TipTap's `insertContentAt` injects it into the Main Document.

**Green gate (Day 7):** Golden path test step 3 passes — side-by-side diff visible.

### Sprint 2 — Hybrid Merge + Polish (Days 8–14)

4. Hybrid selection: merge best parts from both columns into Main document.
5. Red-Hat on compiled responses (minimum 2 passes).
6. Grammar/flow revision on Main document without deleting verified data.

**Green gate (Day 14):** Golden path test steps 4–6 pass.

### Sprint 3 — Full-scan + Benchmark (Days 15–21)

7. Full-context analysis of Main document (numbers, citations, verifiable issues).
8. Benchmark comparison and targeted local prompting on weak points.
9. Selective rewrite/reanalyze until export-ready.

**Green gate (Day 21):** Golden path test steps 7–9 pass.

---

## Related docs

| Doc | Purpose |
| --- | --- |
| [ship-timeline.md](./ship-timeline.md) | Hard day gates and ship date |
| [visual-checklist.md](./visual-checklist.md) | Executive-grade visual standard |
| [frozen-shell.md](./frozen-shell.md) | Locked 3-pane layout (Day 1) |
| [deferred.md](./deferred.md) | Everything not on the golden path |
