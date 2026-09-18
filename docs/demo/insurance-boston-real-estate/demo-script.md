# Demo Script — The Compliance Loop (20–30 min live)

**Project:** Boston RE Insurance Demo
**Template:** Compliance Memo
**Goal:** Show config-vs-policy drift detection, adversarial audit, surgical fix, and exportable audit trail.

---

## Before you start (2 min)

**Opening line:**
*"Insurance compliance is a loop: ingest sources, assemble a structured document, verify numbers, stress-test with an adversarial reviewer, fix what breaks, and export proof for regulators. Assure automates that loop on one canvas."*

**Set context:** Synthetic redacted MA real-estate policy + rating engine JSON — same shape as NAIC filings and internal actuarial configs.

---

## Step 1 — Ingest (3 min)

**Actions:**
1. Open **Sources** (left pane) → confirm two files:
   - `naic-underwriting-policy-redacted.pdf`
   - Rating config (JSON as text file or pasted excerpt)
2. Ensure both are **included** for compilation (checkbox on each file).

**Expected outcome:**
- Files show **Verified** after Textract (PDF) or ingest.
- Sources search works if audience asks about a clause.

**Talking points:**
- *"Sources is your substrate vault — every claim can trace to uploaded evidence."*
- *"We treat compliance docs like source code: versioned, inspectable, grounded."*

**Drift seeded in demo assets:**

| Field | Policy PDF | Config JSON (wrong) |
|-------|------------|---------------------|
| Wind/hail deductible | 2% | 5% |
| Max liability | $2M | $2.5M |
| Inspection interval | 24 months | 12 months |

---

## Step 2 — Assemble (4 min)

**Actions:**
1. Go to **Write / Assemble** view.
2. Paste or confirm prompt:

   ```
   Summarize the Massachusetts commercial real estate underwriting obligations in the source.
   Report the wind/hail deductible percentage, the maximum liability in USD, and the inspection
   interval in months. Cite each figure.
   ```

   > **Do not ask for a policy-vs-engine drift comparison on a policy-only project.** The drift
   > clause needs `rating-engine-config.json` in the vault as well as the policy: the policy alone
   > states the *duty* to match the engine ("Material drift between policy language and engine
   > parameters requires immediate escalation"), never the engine's own numbers. Ask for the drift
   > comparison only when both sources are uploaded (step 1).

3. Click **✨ Assemble** → wait for stream → **Accept & Dock**.

**Expected outcome:**
- JDF canvas shows structured sections (Executive Summary, Findings).
- Confidence coloring on claims (green/yellow/red spans if enabled).
- First-compile coachmark may appear on brand-new projects — dismiss or use as talking point.

**Talking points:**
- *"Assemble doesn't give you a chat paragraph — it gives you a structured document AST you can verify node by node."*

---

## Step 3 — Verify (5 min)

**Actions:**
1. Watch **status bar**: Working → **✅ Verified** or **⚠️ risks found**.
2. Click **Why this score** (Z3 explanation) on a flagged claim if visible.
3. Open **source conflict** badge if keyword scan finds PDF vs JSON number mismatch.
4. Toggle **confidence overlay** on docked canvas.

**Expected outcome:**
- Z3 flags numeric mismatch between policy (2%, 24 months, $2M) and config (5%, 12 months, $2.5M).
- Explanation cites source labels (policy vs config text).
- Status bar: *"⚠️ N risks found — Click to see details."*

**Talking points:**
- *"This is drift detection — the kind that causes bind errors and regulatory findings."*
- *"Z3 is symbolic verification, not vibes — each claim gets a reason string."*

**Honest caveat:** Keyword conflict scan is not full semantic NLI; mention v2.0 roadmap if asked.

---

## Step 4 — Audit (5 min)

**Actions:**
1. Click **Full Audit** (compile + Z3 + Red-Hat in one pass) **OR** run Stress Test after normal Assemble.
2. Open audit appendix on canvas — claim / Z3 / Red-Hat columns.
3. Point to Red-Hat findings (missing audit trail, unsupported superlative, etc.).

**Expected outcome:**
- Red-Hat critiques at least one claim (e.g., config version mismatch, unstated assumption).
- Status bar reflects issues count.

**Talking points:**
- *"Red-Hat is the adversarial reviewer — it tries to break your narrative before the regulator does."*
- *"Full Audit is one button for teams that won't run verify and audit separately."*

---

## Step 5 — Fix (5 min)

**Actions:**
1. Hover paragraph → **floating bar**: ✏️ Rewrite / 🔍 Ground / 📜 History / 🗑️ Delete.
2. Or click node → surgical popover → **✏️ Polish**.
3. Instruction example:

   ```
   Align the wind deductible, max liability, and inspection interval with the rating engine JSON.
   Use 2%, $2,000,000, and 24 months. Note the config_version correction in the audit trail section.
   ```

4. Accept diff → save.

**Optional:** Replace Sources file with `rating-engine-config-corrected.json` and re-run Verify.

**Expected outcome:**
- Node content updated; version increments on save pill.
- Re-verify shows fewer issues.

**Talking points:**
- *"Surgical Polish fixes one claim without rewriting the whole memo — aperture-controlled editing."*
- *"Every fix is a revision you can roll back."*

---

## Step 6 — Export (3 min)

**Actions:**
1. Footer **More** → **Audit Report** (manifest) or command-deck **Export Audit Report**.
2. Download JSON manifest (`audit_manifest_boston-re-insurance-demo_YYYY-MM-DD.json`).
3. Optional: export canvas **PDF** via footer export links.

**Expected outcome:**
- JSON includes Z3 logs, Red-Hat entries, source hashes, model signatures (per manifest schema).
- Audience sees portable proof for compliance file.

**Closing line:**
*"You walked in with a PDF and a JSON config. You walked out with a verified document, an adversarial audit trail, and an export your compliance officer can file — in one session, on one platform."*

---

## Q&A prep

| Question | Answer |
|----------|--------|
| Is this NAIC/DORA certified? | Assure assists compliance workflow; certification is your process + our audit exports. |
| LLM hallucinations? | Z3 + grounding + Red-Hat; show confidence overlay. |
| On-prem? | Discuss EC2/docker deploy; production runs on your AWS stack today. |
| vs. generic AI? | Structure (JDF AST), verification, and exportable audit trail — not chat. |

---

## Timing guide

| Segment | Minutes |
|---------|---------|
| Intro + Ingest | 5 |
| Assemble + Verify | 9 |
| Audit + Fix | 10 |
| Export + Q&A | 6 |
| **Total** | **~30** |
