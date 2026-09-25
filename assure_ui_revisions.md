# Assure + Parsure UI Revision Brief

**Goal:** Revise the UI into a lean, premium, enterprise-grade product with one obvious action, one clear status, strong hierarchy, and minimal chrome — where trust, provenance, and quality are visible without feeling noisy.

**Design direction:** Steve Jobs-style restraint for an enterprise verification product — fewer visible controls, more whitespace, clearer hierarchy, one obvious primary action per screen, advanced actions hidden until needed, status and evidence doing the visual work.

**Critical revision:** Shift the UI language from **parser-first** to **evidence-first**. Plymouth Rock is not a PDF-only client. The UI should feel like multimodal evidence intake, not a parser dashboard.

---

## 1. Overall UI Principles

### One screen, one question
Each screen answers one thing clearly:
- **Assure:** "Can I trust this document / claim?"
- **Parsure:** "Is this evidence good enough, and what needs attention?"

If a control does not help answer that question immediately, move it into:
- an overflow menu
- a side panel
- a command palette
- or a secondary workspace

### Reduce chrome
The current UI has too many simultaneously visible controls.

**Revision rule:** Show only:
- one primary action
- one clear status area
- one or two supporting actions
- everything else behind "More" or an inspector

### Use whitespace as structure
Do not rely on boxes and borders to create hierarchy.

**Use:** margin, spacing, typography, subtle separators, careful alignment  
**Avoid:** dense clusters of buttons, heavy outlines everywhere, multiple nested chrome layers, noisy metadata competing with the main task

### Make confidence and status visible, not decorative
The product should feel like it is **explaining trust**, not just showing documents.

**Primary visual signals:**
- confidence
- verification status
- provenance
- conflict state
- review requirement

**Color only when it means something:**
- green = verified / accepted
- amber = partial / review needed
- red = conflict / rejected
- gray = neutral / inactive




## 2. User Flow and Order — frictionless


### Parsure flow — intake, quality, triage

**Step 1: Upload**
- User uploads evidence (photo, scan, handwritten note, table, mixed bundle)
- Brief loading state
- Arrive at intake overview
**Step 2: Intake overview**
- See what entered the system
- See quality summary (one line or 3 cards max)
- See what needs attention
- See what's ready for replay
- Primary action: "Review" or "Finalize" on a document that needs it

**Step 3: Document detail**
- See the document as the focus
- See quality score, modality, source type
- See issues (page quality, signature, fields needing review)
- See replay eligibility
- Primary action: "Send to Assure" or "Review now"

**Step 4: Queue or send**
- Documents needing attention sit in a queue
- Documents ready for verification are sent to Assure
- User always knows what's next

**Parsure tone:** Calm triage. "Here's what came in. Here's its quality. Here's what needs attention. Here's what to do next."

### Assure flow — verification, decision, dossier

**Step 1: Open a document**
- Document is the hero
- See status strip (verified, conflicts, review needed, next action)
- See the evidence (the document, the sources)
- Primary action: "Review" or "Finalize" at the top

**Step 2: See the evidence**
- Document is dominant
- Sources are visible
- Confidence is visible
- Conflicts are visible
- User sees the material they're verifying

**Step 3: See verification results**
- See what's verified (green)
- See what needs review (amber)
- See what's conflicted (red)
- See why each field is in its state
- User understands the state at a glance

**Step 4: Review and correct**
- See fields that need attention
- See why each field needs attention
- See the source evidence for each field
- See accept / correct / dispute actions
- Correction is surgical, guided, not editing a raw dump

**Step 5: Finalize and sign-off**
- Finalize the verified document
- Sign-off is a formal action, not a utility button
- Export is one primary action, advanced options behind overflow
- User produces a dossier

**Assure tone:** Calm decision-making. "Here's the evidence. Here's the state. Here's what needs your attention. Here's what to do."

### The transition — Parsure to Assure

- User sends it from Parsure to Assure
- Parsure shows "Sent to Assure" or "Queued for review"
- Assure shows the document as ready for verification
- User doesn't lose context — they see the same document, now in the verification workspace



**Rule:** The transition should feel like moving from intake to decision, not like switching apps.

### What the user never has to do

- Search for the primary action
- Figure out where they are
- Guess what a status means
- Dig through menus to do something basic
- See the same information in three places
- Edit a raw document dump
- Parse technical status messages

### Button and icon clarity

**Buttons:**

- **Primary button:** One per screen. Filled ink. Says what it does.
- Good: "Finalize", "Review", "Upload", "Send to Assure"
  - Avoid: "Submit", "Process", "Execute", vague verbs

- **Secondary button:** Outline, subdued. Used sparingly.
  - Good: "View source", "Compare", "Details"
  - Avoid: rows of 3+ outline buttons

- **Tertiary action:** Text link or overflow menu.
  - Good: "More ▼", "History", "Export variants"
  - Avoid: making tertiary actions look like primary or secondary

- **Danger/compliance action:** Only shown when needed. Visually restrained.
  - Good: "Sign off", "Reject", "Dispute"
  - Avoid: red everywhere, alarming styling unless critical

**Rule:** If a button label needs explanation, rename it.

**Icons:**

- Use icons only when they add meaning
- Keep icon style consistent (one style, one weight)
- Don't use icons as decoration
- Status icons (check, warning, conflict) are clear and standard
- Navigation icons are minimal (one per nav item)
- Avoid icon clutter — more than 4 icons in one row is too many

**Good icons:**
- Check / verified / accepted
- Warning / amber / review needed
- Conflict / rejected
- Upload
- More / overflow
- Back / navigation

**Avoid:**
- Abstract metaphors
- Multiple icon styles in one interface
- Icons that don't map to a clear meaning
- Icon overload (rows of icons with no text)



### Wording clarity — crystal clear

The user should understand what they're looking at and what to do without reading twice.

**Principles:**

- Say what something **is**, not what it **does** technically
  - Good: "Insurance policy" — the user knows what it is
  - Avoid: "Document type 3" — the user has to guess

- Say what **happened** or **needs to happen**, not the system state
  - Good: "3 fields need review" — the user knows what to do
  - Avoid: "Status: pending review" — vague, no action implied

- Say **why** something is in a state, when it matters
  - Good: "Low confidence — page quality is poor" — the user understands
  - Avoid: "Confidence: 0.42" — the user has to interpret

- Use familiar words, not system jargon
  - Good: "Upload", "Review", "Verify", "Export"
- Avoid: "Ingest", "Parse", "Compile", "Route" — these are system verbs, not product actions

- Keep labels short — one line, one idea
  - Good: "Verified", "Review needed", "Conflict detected"
  - Avoid: "Document verification status: verified" — too long

- One status, one word when possible
  - "Verified" — one word, clear
  - "Review needed" — two words, clear
  - Avoid: "Pending verification review required" — too many words

**Rule:** If a user has to pause and think about what a label means, rewrite it.



---

## 3. Assure UI Revisions

Assure is the **verification and dossier workspace**. Feel: calm decision-making surface.

### What Assure should emphasize
- document truth
- source evidence
- verification results
- conflict detection
- review / correction
- dossier export

### What Assure should de-emphasize
- development-tool feel
- too many visible knobs
- technical routing logic
- too much shell chrome
- competing tabs and panels at once

### A. Header revision — even quieter

**Current issue:** The header is improved, but it still risks becoming a control strip.

**Revised header structure — three zones, minimal:**

**Left**
- Assure logo / wordmark
- current workspace / document name

**Center**
- one compact trust or status summary
  - `Verified` / `3 conflicts` / `Needs review` / `High confidence` / `Pending`
  - Or a compact line: "3 of 12 fields verified" / "2 conflicts detected"

**Right**
- one primary action (filled ink button)
- one overflow menu

### Header wire diagram:

```
[Assure logo + wordmark]   [ Status: Verified · 3 conflicts ]   [Finalize ▼]
```

Three zones, one line. Left = brand. Center = status (takes remaining space, compact chip). Right = primary action + overflow. Nothing else.

**Rule:** The header should feel like a premium instrument, not a command console. If it shows more than 2–3 actions, it is too busy.
**Move out of the header completely:**
- role selector
- locale selector
- audit report
- decision log
- sign-off
- version slider
- export variants
- developer/founder controls

### B. Sidebar / navigation — quieter

**Keep only essential navigation:**
- Workspaces
- Sources
- Analytics
- Settings

**Revise the visual treatment:**
- reduce icon noise
- strengthen active state
- make inactive items quieter
- collapse secondary items by default

**Goal:** The sidebar should feel like a navigation spine, not a dashboard.

### C. Main workbench — document as hero (stronger)

**Current issue:** The brief already says this, but it should be stated more strongly.

**Revision:** The main document / evidence view must dominate the layout.

**Rule:** The side panes should frame the document, not compete with it.

**Why:** This is the core of the Jobs-style restraint.

**Recommended layout:**
- center: document / evidence area (dominant)
- right: inspector / conflict / provenance (supporting)
- left: navigation / source list (supporting)

**In the center area — prioritize:**
- the current document
- the current claim / field
- the confidence state
- the main evidence source

**Do not flood the center with controls.** The document is the interface.

### D. Evidence / inspector panel — quieter

The inspector should be the place for detail, not the default visual burden.

**Put here:**
- source span
- provenance
- version history
- verification details
- confidence breakdown
- conflict details
- replay / correction history

**Make it:**
- compact
- quiet
- information-rich
- collapsible by section

**Visual rule:** The inspector should be rich, but never louder than the main document.

### E. Buttons and actions — simpler hierarchy

**There are too many button styles and too many visible actions.**

**Revised action hierarchy:**

**Primary action** — one filled button only (ink, not blue)

**Secondary actions** — outline buttons (sparingly)

**Tertiary actions** — text links or overflow menu

**Dangerous / compliance actions** — only shown when needed and visually restrained

**Rule:** If there are more than 2–3 visible actions in one region, the design is probably too busy.

### F. Quality warnings — calm, not alarmist

**Issue:** Warnings need to be obvious, but not loud.

**Revision:** Use:
- amber chips
- subtle labels
- short explanatory text

**Avoid:**
- red-banner overload
- scary UI
- noisy alert language unless truly critical

**Example copy:**
- "Page quality is low."
- "Signature is faint."
- "This field needs review."
- "Replay available after policy update."

**Why:** You want enterprise calm, not panic.

### G. Empty states — tighter, more premium

**Keep:**
- clean layout
- clear next step

**Add:**
- a single suggested action
- one short sentence
- optional secondary link

**Suggested tone:**
- "No documents yet. Upload evidence to begin."
- "No review items. New intake will appear here."

**Rule:** Empty states should feel like a clean starting point, not a missing-state warning.

### H. Status hierarchy — clearer

**Issue:** There are many useful states, but they need tighter ranking.

**Revision:** In the UI, always answer:
1. Is it good?
2. Is it trusted?
3. Is it conflicted?
4. What needs attention?
5. What should happen next?

**Why:** This keeps the experience focused.

## 4. Parsure UI Revisions — evidence-first, calm triage


Parsure should feel like the **intake and quality intelligence layer**.

It should be visually different from Assure, but still from the same system family.

### Shift from parser-first to evidence-first

**Issue:** The parsing page still centers JDF vs Textract too much.

**Revision:** Make the UI feel like **multimodal evidence intake**, not a parser dashboard.

**What to change:**
- Treat JDF / Textract as **secondary implementation details**
- Make **quality**, **modality**, **review state**, and **replay readiness** the main visual signals
- Replace PDF-centric examples like `filename.pdf` with generic evidence examples:
  - photo
  - scan
  - handwritten note
  - table image
  - mixed bundle

**Why:** Plymouth Rock is not a PDF-only client.

### Parsure hero area — calm triage

**The hero should be simple and calm.**

**Show:**
- what entered the system
- how many documents/pages
- quality state
- what needs attention

**Avoid:**
- large technical banners
- too many counters
- too much "machine status" language

### Parsure summary bar — revised

**Current:** 4 stat cards (Documents parsed · JDF CLI · Textract · Avg parse confidence) — too dashboard-like.

**Revised options (pick one):**

**Option A — single compact summary line:**
```
Documents: 12   Pages: 45   JDF: 3   Textract: 9   Avg confidence: 89%
```
One line, calm, information-dense without being busy.

**Option B — 3 cards max:**
+```
[ Documents: 12 ]   [ Pages: 45 ]   [ Avg confidence: 89% ]
```
Drop JDF/Textract split from summary — it's a routing detail, not a summary stat.

**Recommendation:** Option A for the hero area. Calmer and more compact.




### Document cards — more multimodal

**Issue:** The current examples still read a bit PDF-first.

**Revision:** On Parsure cards, show:
- source type (photo / scan / handwritten note / table image / mixed bundle)
- modality
- quality score
- review state
- issue summary
- replay eligibility

**Example revised card:**
```
phone photo · insurance policy
Mar 15, 2026

Pages: 4      Quality: 0.42      ⚠ 3 fields need review
Signature: faint      Replay: available after policy update
```

**Keep:**
- filename / source
- document type
- status
- confidence / quality

**Add:**
- source type / modality
- quality score
- review requirement
- replay eligibility
- provenance readiness
- one short issue summary

**Rule:** Cards should be readable at a glance.
### Document card — wire diagram

+```
[ source type · modality ]   [ document type ]
[ filename                  ]   [ quality score ]
[ date                        ]   [ review: 3 fields ]

[ status line ]
[ signature: faint | replay: available | issue: low page quality ]
```

Title dominant on left. Metadata secondary. Status line at bottom. Parser badge optional (small, secondary). Card readable at a glance.


### Parsure detail vs Assure review — boundary

**Parsure document detail** answers:
- What came in?
- What's its quality?
- What's its modality?
- What needs attention?

**Assure review screen** answers:
- Can I trust this?
- What's the evidence?
- What's the conflict?
- What should I do?

**Rule:** Parsure = intake and quality intelligence. Assure = verification and decision. Don't blur the boundary. Parsure shows quality and issues; Assure shows verification and action.



### Assure summary strip — detail

+```
[ Status: Verified ]   [ Conflicts: 3 ]   [ Review: 5 fields ]   [ Next: Finalize ▼ ]
```

One compact strip at the top of the main review screen. Left to right: trust state, conflict count, review requirement, next action. Calm. Not a dashboard. Just the essentials.

### Parsure document detail — calm triage

**Show:**
- page quality
- field-level provenance
- correction history
- replay history
- parser / source metadata

**Hide by default:**
- deep technical details
- all raw JSON
- all routing internals

**Add:**
- page quality summary
- modality / type summary
- fields needing attention
- replay eligibility explanation
- provenance timeline

**The question this screen should answer:**
- what is wrong?
- how bad is it?
- what should happen next?

### Parsure analytics / corpus view — premium report, not BI wall

**Keep:**
- drift / quality trends
- error patterns
- replay outcomes
- correction outcomes

**Change:**
- make charts quieter and more editorial
- avoid dense KPI overload

**Add:**
- a few strong summary metrics
- trend lines
- issue distributions
- replay improvement view

**Rule:** Analytics should feel like a premium report, not a BI dashboard wall.

## 5. Shared Visual System — unified, but different emphasis


Assure and Parsure should feel like one product family.

### Shared design language
- same typography
- same spacing rhythm
- same border treatment
- same neutral base palette
- same icon style
- same confidence colors

### Different emphasis
- **Assure** = decision / verification / dossier
- **Parsure** = intake / quality / traceability

That difference should be communicated through layout and content priority, not by making them look like different products.

### Keep
- design tokens: ink `#0A0A0A`, paper `#FAFAF7`, surface `#FFFFFF`, rule `#E8E8E4`, muted `#6B6B66`
- semantic colors: verified `#0F6E3F`, partial `#B8730E`, contradicted `#A32D2D`, unverified `#9A9A94`
- Inter font (400/500/600/700) for chrome
- Serif for document body (18px at 1.65)
- 8px grid
- Hairline borders (rule color)
- One accent (ink), four semantic colors, everything else monochrome

### Add / strengthen
- **Whitespace:** More breathing room. The current 40px top padding on the parsing dashboard is good. Apply similar breathing room to Assure.
- **Hierarchy:** Stronger heading weights, lighter metadata. Typography should do more of the work.
- **Status colors:** Use them sparingly. Green/amber/red/gray only when they mean something.
- **Micro-interactions:** Subtle hover states, quiet transitions. No surprises.

### Avoid
- new hues
- heavy outlines
- too many button variants
- dense KPI walls
- dashboard-heavy layouts
- developer-console feel

---
## 6. Typography Direction

Typography should carry a lot of the hierarchy.

### Suggested approach
- strong headline weight
- restrained body size
- small label text
- consistent spacing
- minimal uppercase usage

### Rule
If the typography is strong enough, you need fewer boxes and fewer labels.

---
## 7. Microcopy Style

The copy should be:
- concise
- calm
- precise
- non-hysterical
- non-marketing-heavy

### Good tone
- "Verified"
- "Needs review"
- "Low confidence"
- "Replay available"
- "Source conflict detected"

### Avoid
- dramatic language
- too much explanation in the main UI
- cluttered helper text
- repeated warnings unless truly necessary

---
## 8. What to Hide by Default

Move these out of the default visual field:
- version slider
- decision log
- sign-off
- audit report
- locale selector
- role selector
- developer / founder-only controls
- technical routing details
- advanced export variants
- internal policy metadata

They can still exist — just not dominate the screen.

---
## 9. Interaction Design Rules

### A. Progressive disclosure
Show:
- summary first
- detail only on expand

### B. One primary action per screen
The user should know what to do next immediately.

### C. Stable layouts
Avoid content jumping or excessive mode-switching.

### D. Calm feedback
Use subtle transitions and modest status updates.

### E. Always show the reason
When confidence is low, the UI should say why:
- low page quality
- missing provenance
- weak extraction
- conflict detected
- signature unclear

---

## 10. Specific Revision Priorities
### High priority
1. Simplify the header — even quieter, one status, one action, one overflow
2. Reduce visible controls everywhere
3. Make status and confidence the hero
4. Keep the document as the dominant hero
5. Make side panes quieter — frame the document, don't compete
6. Shift UI language from parser-first to evidence-first
7. Make Parsure look like calm triage, not a technical dashboard
8. Make quality warnings calm, not alarmist

### Medium priority
9. Tighten card design — more multimodal, less PDF-first
10. Clean up sidebar
11. Make inspector less noisy
12. Normalize button hierarchy
13. Tighten empty states — more premium
14. Improve copy — calmer, more precise

### Lower priority
15. Refine micro-interactions
16. Polish hover states
17. Tune empty states
18. Improve animated transitions lightly

---


The revised UI should feel like:
## 11. Final Product Framing
> **A premium enterprise verification instrument: quiet, precise, spacious, and trustworthy.**

Not:
- busy
- technical
- cluttered
- developer-centric
- dashboard-heavy

---

## 12. One-Line Design Brief to Give the Team



> **Design Assure + Parsure as a calm, enterprise-grade evidence system with one obvious action, one clear status, strong hierarchy, and minimal chrome — where trust, provenance, and quality are visible without feeling noisy. Shift the language from parser-first to evidence-first. Plymouth Rock is a multimodal evidence client, not a PDF-only client.**
## Screen-by-Screen Revision Checklist

### 0) Overall UI Rules

**Keep:**
- lean, clean, premium, enterprise-grade feel
- strong hierarchy
- calm, minimal chrome
- one obvious primary action per screen
- status/confidence/evidence as the main visual language

**Remove or reduce:**
- dense header clutter
- too many visible controls
- repeated technical metadata
- multiple competing button styles
- developer-console feel

**Add:**
- more whitespace
- clearer section separation
- calmer typography hierarchy
- subtle semantic status colors
- progressive disclosure

---

### 1) Global Shell

**Keep:**
- shared product shell for Assure and Parsure
- consistent typography, spacing, borders, and icon style

**Change:**
- make the shell quieter and less "toolbox-like"
- reduce the number of always-visible controls
- hide advanced controls until needed

**Add:**
- one global command / overflow path for advanced actions
- one global status area for trust / quality / review state

---

### 2) Header

**Current issue:** The header is too busy and makes the product feel more like a control panel than a premium workbench.

**Keep:**
- brand mark
- current workspace / document name
- one concise trust/status chip

**Remove from default visibility:**
- role selector
- locale selector
- audit report button
- decision log button
- sign-off button
- version slider
- multiple export variants
- founder / legacy controls
- developer-level controls

**Add:**
- one primary action
- one overflow menu
- one subtle status line or chip
- optional compact "verified / review / conflict" indicator

**Rule:** If the user can see more than 2–3 actions in the header, it is too busy.

---

### 3) Sidebar / Navigation

**Keep:**
- Workspaces
- Sources
- Analytics
- Settings

**Change:**
- make inactive items quieter
- make the active item much clearer
- reduce icon noise
- collapse secondary navigation by default

**Add:**
- small count badges only when meaningful
- clearer grouping by function

**Goal:** The sidebar should feel like a spine, not a dashboard.

---

### 4) Assure Main Review Screen

**Purpose:** This is the "can I trust this?" workspace.

**Keep:**
- document / dossier as the hero
- source evidence visible
- confidence visible
- conflict visible
- correction / review workflow

**Remove:**
- too many technical controls in the main view
- routing / parser details unless explicitly expanded
- repeated status elements

**Add:**
- a calm summary strip at the top:
  - trust state
  - conflict count
  - review requirement
- a focused evidence panel
- a compact confidence breakdown
- a clear next-step action

**Layout guidance:**
- center: document / claim / evidence (dominant)
- right: provenance / conflict / version details (supporting)
- left: navigation / sources / workspace list (supporting)

**Tone:** Assure should feel like a **decision surface**, not a **toolchain surface**.

---

### 5) Assure Evidence / Inspector Panel

**Keep:**
- source spans
- verification details
- conflict details
- version history
- correction history
- replay / provenance metadata

**Change:**
- reduce visual clutter
- group technical fields into compact sections
- avoid making the inspector louder than the main document

**Add:**
- collapsible sections
- section titles that say what the data means
- "why this is low confidence" explanations

**Goal:** The inspector should be rich, but quiet.

---

### 6) Assure Review / Correction Flow

**Keep:**
- surgical correction
- source comparison
- claim / field review
- version history

**Change:**
- make the correction flow more guided
- show the reason for review before showing the tools

**Add:**
- "why this field needs attention"
- confidence + provenance explanation
- clear accept / correct / dispute actions

**Rule:** Correction should feel precise and controlled, not like editing a raw document dump.

---

### 7) Assure Export / Audit / Sign-Off

**Keep:**
- export
- audit report
- sign-off

**Change:**
- move these out of the top-level visual field
- expose them only when needed

**Add:**
- one primary export action
- advanced export options behind overflow
- audit / sign-off in a dedicated review drawer or modal

**Tone:** These should feel like formal enterprise actions, not utility buttons.

---

### 8) Parsure Intake Screen

**Purpose:** This is the "what came in, how good is it, what happens next?" workspace.

**Keep:**
- intake overview
- document list / queue
- quality state
- replay eligibility
- source/modality information

**Change:**
- avoid making it look like a developer dashboard
- make it feel like an evidence intake desk

**Add:**
- document quality score
- modality label
- issue summary
- routing / replay status
- concise next action

**Tone:** Parsure should feel like **calm triage**.

---

### 9) Parsure Document Card

**Keep:**
- filename / source
- document type
- status
- confidence / quality

**Change:**
- simplify the card hierarchy
- keep the title dominant
- make metadata secondary

**Add:**
- source type / modality (photo / scan / handwritten note / table / mixed bundle)
- quality score
- review requirement
- replay eligibility
- provenance readiness
- one short issue summary

**Example card structure:**
- phone photo · insurance policy
- Mar 15, 2026
- Quality: 0.42 · Review: 3 fields
- Signature: faint · Replay: available

**Rule:** Cards should be readable at a glance.

---

### 10) Parsure Document Detail

**Keep:**
- page-level quality
- field-level provenance
- correction history
- replay history
- parser / source information

**Change:**
- collapse advanced technical details by default
- group information by meaning, not by data structure

**Add:**
- page quality summary
- modality / type summary
- fields needing attention
- replay eligibility explanation
- provenance timeline

**Goal:** This screen should help the user answer:
- what is wrong?
- how bad is it?
- what should happen next?

---

### 11) Parsure Analytics / Corpus View

**Keep:**
- drift / quality trends
- error patterns
- replay outcomes
- correction outcomes

**Change:**
- make charts quieter and more editorial
- avoid dense KPI overload

**Add:**
- a few strong summary metrics
- trend lines
- issue distributions
- replay improvement view

**Rule:** Analytics should feel like a premium report, not a BI dashboard wall.

---

### 12) Status Chips and Confidence Labels

**Keep:**
- semantic labels
- confidence indicators
- quality indicators
- conflict indicators

**Change:**
- use fewer labels overall
- make them more meaningful
- don't repeat the same state in three places

**Add:**
- short labels:
  - Verified
  - Review needed
  - Conflict detected
  - Low quality
  - Replay available

**Rule:** Every status chip must answer a real question.

---

### 13) Buttons and Action Hierarchy

**Keep:**
- primary action
- secondary action
- overflow menu

**Remove:**
- too many outline buttons in one row
- duplicated action labels
- multiple "danger-looking" buttons

**Add:**
- one dominant primary button
- one subdued secondary button
- one "More" menu for advanced actions

**Rule:** If the user has to scan a row of 6 buttons, the layout is too noisy.

---

### 14) Typography

**Keep:**
- strong heading hierarchy
- compact labels
- readable body text

**Change:**
- use typography to create structure instead of borders everywhere
- keep copy short and direct

**Add:**
- larger headline / section titles
- lighter metadata text
- tighter label language

**Tone:** The typography should feel editorial, calm, and confident.

---

### 15) Copy / Microcopy

**Keep:**
- simple, direct language
- plain-English status labels

**Change:**
- reduce jargon in default views
- avoid long technical explanations unless expanded

**Add:**
- short explanatory phrases:
  - "Low page quality"
  - "Signature is faint"
  - "Review required"
  - "Replay available"
  - "Source conflict detected"

**Rule:** Say what the user needs to know, not everything the system knows.

---

### 16) Empty States

**Keep:**
- clean layout
- clear next step

**Add:**
- a single suggested action
- one short sentence
- optional secondary link

**Example:**
- "No documents yet. Upload evidence to begin."
- "No review items. New intake will appear here."

**Rule:** Empty states should feel calm and useful, not like an error.

---

### 17) Mobile / Narrow Screens

**Keep:**
- the same structure, simplified

**Change:**
- collapse side panels
- reduce visible controls
- prioritize the core action and status

**Add:**
- tap-to-open inspectors
- stacked sections
- compact status strip

**Rule:** On smaller screens, show less but say the same thing.

---

## Final Revision Priority

### Must do first
1. Simplify the header — even quieter, one status, one action, one overflow
2. Reduce visible controls everywhere
3. Make status and confidence the hero
4. Keep the document as the dominant hero
5. Make side panes quieter — frame the document, don't compete
6. Shift UI language from parser-first to evidence-first
7. Make Parsure look like calm triage, not a technical dashboard
8. Make quality warnings calm, not alarmist

### Next
9. Tighten card design — more multimodal, less PDF-first
10. Clean up sidebar
11. Make inspector less noisy
12. Normalize buttons
13. Tighten empty states — more premium
14. Improve copy — calmer, more precise

### Later
15. Refine micro-interactions
16. Polish hover states
17. Tune empty states
18. Improve analytics presentation

---

## Final Design Principle

> **Make the interface feel like a premium enterprise instrument: quiet, spacious, and precise, with trust and evidence doing the visual work.**
