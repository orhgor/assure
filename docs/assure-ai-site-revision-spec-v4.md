# Assure AI: Master Site Revision Spec (for Cursor)

Goal: `getassureai.com` looks and reads like a category-defining enterprise technology product—sharp, visual, and product-forward—with zero contradictions across pages. It must never look like a legal brief, an academic thesis paper, or a generic startup template.

Anything marked **[CONFIRM]** is a fact only the founder can supply. Do not guess it. Leave the marker visible until resolved.

---

## 0. Step zero: unify the split-brain codebase first

The live site currently runs two conflicting layout shells and three different vocabularies. Before editing copy:

1. **Git baseline audit**: Run `git log` and `git diff --stat` against the pre-rebuild commit. Produce a table of every change tagged as `Required fix` (Section 2), `Requested` (Sections 3–6), or `Invention` (revert immediately).
2. **One global layout shell**: Force `/`, `/about`, `/architecture`, `/security`, `/privacy`, `/terms`, `/solutions`, and `/contact-us` to use **one shared Header and Footer component** (including the 8-language switcher). Delete the legacy layout wrappers that output `"© 2026 Archeon Cloud, Dubai"` or `"Processed on our servers, never used to train our models."`
3. **Lock product names**: Keep **Assure**, **ParSure**, **CrossSure**, **Red-Hat**, **DraftSure** site-wide with exact camelCase capitalization.

---

## 1. Core rules for the agent

1. **One source of truth for deployment & storage**: All facts about where Assure runs, what is stored, and what reaches Assure servers live in **one file** (`content/deployment-modes.json`) and render dynamically on `/security`, `/privacy`, `/terms`, and the homepage trust summary. Never hard-code server or storage claims on individual pages.
2. **Show the UI, don't write essays**: Never stack paragraphs of text to explain how verification works. Every section pairs a 1–2 sentence headline with a structured UI frame, horizontal system diagram, or scannable spec grid.
3. **No legal boxes on marketing pages**: Legal disclaimers live strictly in `/terms` and `/privacy`. Never put grey disclaimer boxes or defensive hedging on `/` or `/solutions`. Use a quiet footer/trust link (`"How checks are scoped → /terms"`).
4. **No invented proof**: No version tickers (`v4.12`, `JDF v2`), fake latency numbers, unverified compliance badges, or fabricated customer logos.
5. **No emojis or decorative fluff**: Zero Unicode emojis (`✏️`, `🔒`, `🛡️`, `✅`), zero glowing neon gradients, zero stock icons.

---

## 2. All required fixes (resolving every live Red-Hat drift)

| ID | Issue on Live Site | Exact Fix Required |
| :--- | :--- | :--- |
| **F1** | **3-Way Server & Storage Clash**: `/security` claims customer AWS/on-prem; `/privacy` claims Assure AWS EC2 storage; `/terms` & `/privacy` claim desktop local storage (`"on this machine"`, `"stays on your computer"`). | Render both deployment modes (**Mode A: Managed Cloud** and **Mode B: Customer VPC / Self-Hosted**) from `content/deployment-modes.json` across `/security`, `/privacy`, `/terms`, and `/`. Delete all legacy desktop phrasing (`on this machine`, `on this computer`, `keys never leave your machine`, `do not Send`). |
| **F2** | **3 Different Navs & Footers**: `/` and `/privacy` have 4 nav links & 7 languages; `/architecture` has 6 nav links & "Archeon Cloud, Dubai"; `/about` and `/security` have a different nav & "© 2026 Assure". | Enforce one global Header (`How it works`, `Solutions`, `Architecture`, `Security`, `About` + Language Switcher + `Launch App` + `Book a review`) and one global Footer across every route. |
| **F3** | **Database & Code Leaks on `/privacy`**: Exposes raw table names (`substrate_vault`, `jdf_revisions`, `user_activity_log`, `audit_log`, `groundrails`). | Purge all internal table and code identifiers from `/privacy` and all public pages. Describe stored data in plain enterprise terms sourced from `content/deployment-modes.json`. |
| **F4** | **Broken Naming & Casing Drift**: `/` spells `Parsure` (lowercase `s`) and omits `CrossSure` and `DraftSure`; `/about` omits `Red-Hat`; `/architecture` uses emojis instead of product names. | Standardize on **`ParSure`**, **`CrossSure`**, **`Red-Hat`**, and **`DraftSure`** across all pages. Replace the emoji list on `/architecture` with the 4 named stages. |
| **F5** | **Stale & Internal Strings**: `PEM engine`, `openrouter`, `Z3 SMT ENGINE`, `v4.12`, `JDF v2`, `"Paste a paragraph on the landing page"`, `"No contradiction survives"`. | Delete every instance site-wide. |
| **F6** | **Duplicated & Weak `/terms` Copy**: `/terms` duplicates the accuracy paragraph and says `"That is a filter, not a guarantee."` | Keep one scope paragraph in `/terms` (Section 6.3). Delete `"That is a filter, not a guarantee."` |
| **F7** | **Persona & Dropdown Chaos**: `/` has two competing persona sections (including `Journalists` and `Solo High-Stakes Creators`), while `/` modal and `/contact-us` have mismatched workflow dropdowns. | Delete the 3-tab role switcher and the 6-card persona grid on `/`. Replace with the 4-card Major ICP Bento Grid (Section 5.5). Sync `/contact-us` dropdown to the exact same 4 verticals. |
| **F8** | **6 Competing CTA Labels**: `Book a Demo`, `Contact sales`, `Open Assure`, `See the proof for your role`, `Try it with your own document`, `Send request`. | Standardize site-wide on Primary CTA: **`Book a review`** (routes to `/contact-us`) and Secondary CTA: **`Launch App`** (links to `https://app.getassureai.com` **[CONFIRM URL]**). |

---

## 3. Founder decisions to confirm (`[CONFIRM]`)

| ID | Decision | Default to use until confirmed |
| :--- | :--- | :--- |
| **D1** | Exact architecture for **Mode A (Managed Cloud)** vs. **Mode B (Customer VPC / On-Prem)** | Render the 2-column table in Section 6.1 from `content/deployment-modes.json` |
| **D2** | Lead worked example on `/` | **Insurance & Claims** (`$1,420,000` draft vs. `$1,450,000` source) |
| **D3** | Active compliance status & subprocessors | Keep AWS, Supabase, Clerk, Stripe, Plausible for Mode A; state DPA & security pack available on request |
| **D4** | High-resolution product UI frame (`hero-product.png` or crisp HTML/CSS UI render) | Clean application window showing compiled coverage opinion, source PDF highlight, and locked Export button |
| **D5** | Legal entity name and self-serve URL | Company line is **Archeon Cloud**. No address on the site. `https://app.getassureai.com` stays **[CONFIRM]** until Launch App is live. |

---

## 4. Visual design system: 2-color Jobsian precision (anti-thesis, anti-law-page)

Why pages look like "law pages" or "thesis projects": narrow single-column text walls, serif or poorly scaled body copy, defensive callout boxes, and zero product UI. 

To make `getassureai.com` look like a category-leading enterprise product (think Linear, Stripe, Ramp):

* **Strict 2-Color Palette + 1 Exception Signal**:
  * **Canvas**: Pure White `#FFFFFF` primary background; Crisp Alabaster `#F7F8FA` for alternating structural bands and card surfaces; `1px solid #E3E6EA` exposed hairline grid borders.
  * **Ink**: Deep Obsidian `#111418` for headlines and primary UI; Muted Slate `#5B6470` for secondary labels and captions.
  * **Single Brand Accent (`#0F5C4D` Deep Verifiable Green)**: Used *exclusively* for the primary `Book a review` button, active interactive states, and `VERIFIED / LOCKED` provenance badges.
  * **Single Exception Signal (`#B42318` Crimson)**: Used *only* inside the product UI frame and Proof inspection block to highlight the `$30,000` conflict and `EXPORT LOCKED` gate. Zero other decorative colors.
* **Typography & Scannable Hierarchy**:
  * **Primary Sans (`Inter` or system UI)**: Hero H1 at `56px–64px` (`font-weight: 700`, `letter-spacing: -0.03em`, `line-height: 1.08`). Section headers at `36px–40px` (`letter-spacing: -0.02em`). Body copy strictly capped at **2–3 lines max per block** (`16px–18px`, `#5B6470`).
  * **Technical Monospace (`JetBrains Mono`)**: Used *strictly* inside UI frames, pipeline stage tags (`01 / PARSURE`), and source coordinates (`Claim_Form_04.pdf · p. 14`). Never use monospace for body paragraphs.
* **Architectural Bento Grid (No Text Walls)**:
  * Max container width `1200px` with visible `1px solid #E3E6EA` structural dividers, `8px` border radius, and `96px` vertical section padding.
  * Every section is a visual component (Application Window Frame, 4-Node Horizontal Pipeline Bar, Split-Pane Provenance Diff, 2x2 ICP Bento Grid), never a free-floating essay.
* **One Purposeful Motion**:
  * Inside the Hero UI frame only: a clean state transition showing the conflict detected (`$1,420,000` vs `$1,450,000`) and the top-right **Export** button shifting from active to **Locked (`#B42318`)**. Runs once on load; disabled under `prefers-reduced-motion`.

---

## 5. Homepage `/` blueprint (7 sharp sections)

### 5.1 Hero (Centered Typographic Punch + Full Product Window)
* **Eyebrow Pill (Monospace)**: `DETERMINISTIC DOCUMENT COMPILER`
* **H1**: **The output is a document. The product is verification.**
* **Subheadline (2 lines max)**: Assure compiles claim files, contracts, and registers into finished documents—and physically locks export until every figure ties to its source page.
* **CTA Row**: Primary button **`Book a review`** (`#0F5C4D` solid fill) + Secondary button **`Launch App`** (`1px solid #E3E6EA` surface button).
* **Infrastructure logo row (required, directly under the CTA, above the product window)**: one centered row, three marks only, in this order, using the existing files:
  1. On-prem — `marketing/static/onprem-icon.svg` (alt `On-prem`)
  2. AWS — `marketing/static/aws-startups.svg` (alt `AWS`)
  3. Amazon S3 — `marketing/static/s3-icon.svg` (alt `Amazon S3`)
  These are deployment marks, not customer logos. Rule 4 still forbids invented customer logos. Do not replace this row with a sentence. Do not drop it when the product frame is added. Same row, same three files, once on `/security` under the page heading. Nowhere else.
* **Hero Visual (Full-Width Application Frame)**:
  * A crisp, high-resolution frame (`1px solid #E3E6EA`, subtle top browser/app chrome) of the real Assure workbench (`D4`).
  * **Left pane**: Source documents (`Claim_Form_04.pdf, p. 14` and `Register.csv, row 212`) showing Reserve `$1,450,000`.
  * **Center pane**: Compiled Coverage Opinion draft (`Section 1.2`) showing Reserve `$1,420,000` underlined in crimson (`#B42318`).
  * **Top-right bar**: **`Export DOCX / PDF`** button visibly **Locked** with badge: `1 Unresolved Conflict ($30,000 delta)`.

### 5.2 The Problem (High-Contrast 2-Line Strip)
* Rendered inside a clean full-width structural band (`#F7F8FA`):
  > **Language models write fluent drafts, and fluent drafts drift.**  
  > Change a table on page 14 and Section 1.2 keeps the old number. In claims, finance, and procurement, one wrong figure in a signed document is a liability.

### 5.3 The 4-Stage Pipeline (Connected Horizontal Schematic)
* **Heading**: **Four stages from raw case file to locked deliverable.**
* **Layout**: A 4-column horizontal schematic connected by a continuous `1px` progress line, each node featuring a monospace stage index (`01` to `04`), exact product name, and 2-line function:
  1. `01` **ParSure** — *Ingest & Anchor.* Parses PDFs, scans, tables, and ERP exports. Every extracted figure keeps its exact page, cell, or row coordinate.
  2. `02` **CrossSure** — *Verify Constraints.* Compiles figures and domain rules into formal mathematical constraints across the entire case file. Every contradiction lists the two sources in conflict.
  3. `03` **Red-Hat** — *Stress-Test.* Runs an adversarial pass against the draft to flag unsupported claims, omitted clauses, and policy gaps before sign-off.
  4. `04` **DraftSure** — *Lock & Export.* Compiles the final Word or PDF document. Export unlocks only when all checks pass and open findings are resolved, shipping with a complete audit record.

### 5.4 Proof: Zoomed-In Provenance Inspection
* **Heading**: **Every figure anchored. Every conflict blocked.**
* **Layout**: A 3-column architectural diff card (`1px solid #E3E6EA`) using the canonical values:
  * **Column 1 (Source Evidence — ParSure)**:
    * `Claim_Form_04.pdf · p. 14` → Reserve: **`$1,450,000`**
    * `Register.csv · row 212` → Reserve: **`$1,450,000`**
  * **Column 2 (Compiled Draft — DraftSure)**:
    * `Coverage_Opinion.docx · Section 1.2` → Stated Reserve: **`$1,420,000`**
  * **Column 3 (Gate Verdict — CrossSure)**:
    * Status pill (`#B42318`): **`EXPORT LOCKED`**
    * Diagnostic: `$30,000 discrepancy between Section 1.2 ($1,420,000) and Source Register ($1,450,000). Resolve or re-sync to unlock export.`

### 5.5 Major ICPs: Built for High-Liability Teams (2x2 Bento Grid)
* **Heading**: **Where a single wrong figure is a liability.**
* **Sub**: Built for institutional teams that sign their names to multi-source deliverables.
* **Layout**: A 2x2 architectural Bento Grid (`#FFFFFF` cards on `#F7F8FA` background). Each tile has a clean top tag for **Buyer Roles**, a visual **Inputs → Output** pill flow, and a 1-line **Blocked Export Check**:

  1. **Insurance & Claims Operations**
     * *Buyer Roles*: `Claims VPs · Coverage Counsel · Complex Loss Adjusters · Underwriters`
     * *Flow*: `Master Policy + FNOL + Loss Register` → **`Coverage Opinion & Reserve Determination`**
     * *Blocked Check*: Locks export when the narrative reserve (`$1,420,000`) drifts from the loss register (`$1,450,000`) or cites an inapplicable exclusion.
  2. **Institutional Finance & Deal Advisory**
     * *Buyer Roles*: `M&A Managing Directors · Private Credit Underwriters · Fund Controllers`
     * *Flow*: `QoE Report + Excel Model + Credit Agreement` → **`Sell-Side CIM & IC Memo`**
     * *Blocked Check*: Locks export when Adjusted EBITDA in the executive summary contradicts the sensitivity table on page 38.
  3. **Public Sector & Regulated Procurement**
     * *Buyer Roles*: `Contracting Officers · Bid Directors · Defense Systems Integrators`
     * *Flow*: `RFP Specs + Bill of Materials + Vendor Pricing` → **`Tender Compliance & Acceptance Dossier`**
     * *Blocked Check*: Locks export when line-item BoM pricing fails to sum to the contract ceiling or omits a mandatory statutory clause.
  4. **Clinical, Pharma & Industrial QA**
     * *Buyer Roles*: `Regulatory Affairs VPs · Clinical Trial Directors · Industrial QA Auditors`
     * *Flow*: `Clinical CSR Tables + Lab Logs + SAP Exports` → **`Regulatory Submission & Batch Release Log`**
     * *Blocked Check*: Locks export when patient cohort N-counts or SAP invoice quantities diverge from underlying source tables.

* **Section Footer Link**: `Explore vertical workflows and SAP/ERP reconciliation on Solutions →` (`/solutions`)

### 5.6 Enterprise Deployment & Trust Summary
* **Heading**: **Run in our cloud or entirely inside yours.**
* **Layout**: A crisp 3-column architectural spec bar rendered directly from `content/deployment-modes.json`:
  * **Column 1 — Deployment Topology**: Managed Cloud Workspace (`app.getassureai.com`) or Customer VPC / On-Premises inside your own AWS account or servers.
  * **Column 2 — Data Residency & Storage**: Zero model training on customer files. In Customer VPC mode, zero documents or prompts ever reach Assure servers.
  * **Column 3 — Bring Your Own Model (BYOK)**: Connect your own Azure OpenAI, AWS Bedrock, Anthropic, or OpenAI keys with zero token markup **[CONFIRM]**.
* **Links**: **`Inspect Security Architecture →`** (`/security`) and quiet text link **`How checks are scoped →`** (`/terms`).

### 5.7 Closing CTA Band
* **Headline**: **Test Assure against your hardest case file.**
* **Sub**: 30 minutes with the engineers who built it. Bring a real multi-document file; we will show you exactly what Assure catches.
* **Buttons**: **`Book a review`** (Primary `#0F5C4D`) and **`Launch App`** (Secondary).

---

## 6. Subpages (unified layout, zero contradictions)

### 6.1 `/security`
Scannable CISO datasheet—not a wall of prose:
1. **3-Sentence Executive Summary**: Where processing runs, what is stored, and what leaves the customer perimeter.
2. **2-Column Deployment Comparison Table** (rendered dynamically from `content/deployment-modes.json`):

| Architecture Dimension | Mode A: Managed Cloud Workspace | Mode B: Customer VPC / Self-Hosted |
| :--- | :--- | :--- |
| **Where processing runs** | Assure-managed AWS compute **[CONFIRM regions]** | Customer's own AWS account, VPC, or on-premises servers |
| **Where documents are stored** | Encrypted tenant storage (`AES-256`) during active projects | Customer's internal storage; zero document egress to Assure |
| **Prompts & questions** | Stored in encrypted project history; never used for training | Retained strictly inside customer perimeter |
| **What reaches Assure servers** | Workspace files, verification states, and billing telemetry | Zero case files or prompts **[CONFIRM license check mechanism]** |
| **Model providers** | Customer API keys (BYOK) or managed endpoints; zero markup | Customer private endpoints (Bedrock, Azure OpenAI, local models) |
| **Encryption** | `TLS 1.3` in transit; `AES-256` at rest | Customer KMS/HSM policies (`TLS 1.3` / `AES-256`) |
| **Retention & deletion** | User-controlled project purge; deleted on account closure | Governed 100% by customer IT lifecycle policies |
| **Audit record** | Exported with every verified DOCX/PDF deliverable | Stored locally and exported with deliverables |

3. **Subprocessors Table**: List AWS, Supabase, Clerk, Stripe, and Plausible Analytics as applicable to **Mode A only**, noting that **Mode B** requires zero external Assure subprocessors **[CONFIRM]**.
4. **Compliance & Contact**: State only true today items. Security mail is `privacy@getassureai.com`. Do not invent a second security address.

### 6.2 `/privacy`
* Apply **F1**, **F2**, and **F3**.
* Render storage and processing facts directly from `content/deployment-modes.json` so `/privacy` and `/security` can never contradict each other.
* Delete all mentions of `substrate_vault`, `jdf_revisions`, `user_activity_log`, `audit_log`, `groundrails`, `"stays on your computer"`, `"keys never leave your machine"`, and `"The Deterministic Truth Engine"`.

### 6.3 `/terms`
* Apply **F1**, **F2**, **F6**, and **F7**.
* Delete all desktop-era strings (`on this machine`, `on this computer`, `filter, not a guarantee`, `PEM engine`) and fix `"do not Send"`.
* Use this single **Scope of Verification** paragraph:
  > Assure performs consistency checks and source-page linking across the documents you supply, for the figures and rules configured for your case. It does not replace professional, legal, clinical, or underwriting judgment, which remains with the authorized reviewer.

### 6.4 `/architecture`
* Engineered as a clean developer documentation page (like Stripe Docs), not an academic thesis.
* Replace all Unicode emojis (`✏️`, `⚙️`, `🔒`, `🛡️`, `🔍`, `✅`) with numbered monospace badges (`01`–`04`) matching `ParSure`, `CrossSure`, `Red-Hat`, and `DraftSure`.
* Include one **System Glossary Table** mapping `ParSure`, `CrossSure`, `Red-Hat`, `DraftSure`, `Substrate Vault`, `Z3 Truth Ledger`, `Laya`, and `JDF` to their exact engineering roles.
* Show a clean horizontal state diagram of the Export Gate: `Draft` → `Constraint & Rule Check` → `Findings Open (Export Locked)` → `Resolved & Signed` → `Export Unlocked + Audit Record`.

### 6.5 `/solutions`, `/about`, `/contact-us`
* **`/solutions`**: Dedicated sections for the 4 Major ICPs (`Insurance & Claims`, `Institutional Finance & Deal Advisory`, `Public Sector & Regulated Procurement`, `Clinical, Pharma & Industrial QA`), including the SAP/ERP invoice and register reconciliation workflow moved from `/about`.
* **`/about`**: Concise company profile sharing the exact same product thesis and vocabulary as `/`.
* **`/contact-us`**: Clean 5-field form (`Full Name`, `Work Email`, `Organization`, `Primary Vertical` dropdown synced to the 4 ICPs, `What documents do you want to verify?`). Primary submit button: **`Book a review`**, plus a quiet link below: *"Evaluating on your own? Launch App →"*.

---

## 7. Automated acceptance checks (pre-merge gate)

Run `node scripts/verify-site-copy.mjs` before merging:

1. **Zero Banned Strings (case-insensitive)**: `on this machine`, `on this computer`, `stays on your computer`, `keys never leave your machine`, `filter, not a guarantee`, `No contradiction survives`, `Z3 SMT ENGINE`, `v4.12`, `JDF v2`, `PEM engine`, `openrouter`, `The Deterministic Truth Engine`, `Paste a paragraph on the landing page`, `Solo High-Stakes Creators`, `Journalists & Fact-Checkers`. `Archeon Cloud` is the company line and is not banned.
2. **Exact Product Casing (case-sensitive)**: Zero matches for `Parsure`, `Par Sure`, `Cross Sure`, `Draft Sure`, `Crosssure`, `Draftsure`.
3. **Zero Database/Code Identifier Leaks**: Zero public matches for `substrate_vault`, `jdf_revisions`, `user_activity_log`, `audit_log`, `groundrails`, `start_char`, `end_char`, `node_id`.
4. **Zero Emojis**: Unicode emoji regex returns zero matches across all rendered templates.
5. **Single-Source Deployment Rendering**: `/security`, `/privacy`, `/terms`, and `/` import deployment facts from `content/deployment-modes.json` with zero hard-coded `AWS EC2` or storage strings outside that file.
6. **Canonical Numbers Match**: `$1,420,000`, `$1,450,000`, `$30,000`, `p. 14`, `row 212`, and `Section 1.2` are identical everywhere the worked example appears.
7. **Unified Global Shell**: Every route renders the identical Header, 8-language switcher, and Footer component.
8. **Zero Unresolved `[CONFIRM]` Tags**: Production build fails if any `[CONFIRM]` string remains.
9. **Infrastructure logos**: `/` and `/security` each render `onprem-icon.svg`, `aws-startups.svg`, and `s3-icon.svg` once, with alts `On-prem`, `AWS`, and `Amazon S3`, in that order. No other logo images on those pages.