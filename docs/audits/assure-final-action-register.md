# Assure — Final Action Register
## Independent Red-Hat Output | P0 / P1 / P2 | 2026-08-31

---

## How to Read This Register

| Priority | Meaning | Timeline |
|----------|---------|----------|
| **P0** | Launch blocker. Product or docs are wrong. Fix before any public mention. | This week |
| **P1** | Polish. Product works but feels unfinished. Fix before Product Hunt / HN. | Next 2 weeks |
| **P2** | Roadmap. Valid feature. Post-launch or when metrics justify it. | Month 2+ |

---

## P0 — Launch Blockers

### P0.1 Rewrite "For people who do not want the engine internals"
**File:** `PEM.md` (section near bottom, before "Editions")
**Problem:** This section describes the OLD product (tour, thumbs, trust strip %, Send vs Copy radios). It's the "quick reference" for non-technical readers.
**Current text:**
> "3-step tour on the first Compose visit. Step 1 is model pills. Advanced options stay closed until you open them."
> "See if models agree. Trust strip shows overlap after Send."
> "Rate an answer. After Send, thumbs under the answer."

**Replace with:**
```
| You need | What Assure does |
| Ask a question | Type it. Assure detects what you need (Auto intent). Attach a file if you have one. |
| Know it's trustworthy | Compare & Validate runs two models. The badge says Verified or Review needed. A confidence sentence tells you where models agree and what was flagged. |
| Catch invented stats | Citation scrub strips dates, percentages, and publication names not in your file. Research answers are forced into Thesis / Verified / Inferred / Open questions. |
| Stress-test a draft | Click Refine this answer. It runs attempt, critic, final automatically. |
| Keep it private | Copy the prompt with Option+Click (no API call). Or run closed to the internet with a local model. |
| Reuse a good prompt | Save it in the Prompt Library. Pro+ can export to .cursorrules or Fabric. |
| See what changed | Previous work keeps your last 7 days (Free) or forever (Pro). |
| Spend less | Save tokens (cheap route) picks the most efficient model. |
| Batch or CI | `pem eval --dataset` and `pem --ci` for developers. |
```
**Effort:** 1 hour
**Acceptance:** Section mentions zero features that were cut or hidden.

---

### P0.2 Fix "What is Assure?" Intro
**File:** `PEM.md` (second paragraph under "What is Assure?")
**Problem:** Says "pick a shape (intent)" but intent is Auto-detected.
**Current:**
> "You type a question, pick a shape (intent), and usually attach a file."

**Replace with:**
> "You type a question and usually attach a file. Assure detects what you need, restructures the question for the AI you chose, then checks the answer."

**Effort:** 5 minutes

---

### P0.3 Fix Editions Table in Product Overview
**File:** `PEM.md` (two editions tables)
**Problem:** Shows 4 columns. Text says Team is not a SKU.
**Fix:** In the product overview section, show ONLY:

| | Free | Pro |
|---|---|---|
| Sends per UTC day | 10 | 100 |
| Models per Compare | 2 | up to 8 |
| History | 7 days | Full, no prune |
| History export | Plain text | Markdown, HTML, Prompty, PDF |
| Class export | Blocked | cursorrules, mdc, fabric, dspy |
| Draft vs final diff | Hidden | Shown |
| Critic personas | redhat | All five |

**Footnote:** *"Team and Self-hosted are available via `--edition team` / `--edition self-hosted` for unlimited Sends on your machine. Not a store SKU."*

In the technical reference section, keep the 4-column table for developers.
**Effort:** 30 minutes

---

### P0.4 Remove Tour from Capability Section
**File:** `PEM.md` ("Capability" section, bullet list)
**Problem:** Lists "Running a first-visit tour" as a capability. Tour is cut.
**Fix:** Remove that bullet. Replace with:
> "Live prompt preview that teaches by doing — no tour needed."

**Effort:** 5 minutes

---

### P0.5 Make Confidence Sentence Deterministic
**Files:** `quality.py`, `templates/index.html`, JS
**Problem:** Spec says "may include overlap % and flagged claims." Non-deterministic = broken UX.
**Fix:** Every `/api/render` response MUST include a `confidence_text` field. Template:

```
If ensemble (2+ models):
  "[Model A] and [Model B] agree on [consensus]%. [N] claims were flagged as unsupported."

If single model:
  "Answer from [Model]. [N] claims were checked against your files."

If quality fields missing:
  "Confidence check pending. Review claims against your files."
```

**Effort:** 4 hours
**Acceptance:** 100% of Send responses show confidence text. Never blank.

---

### P0.6 Move DNS to Cloudflare
**Owner:** DevOps
**Problem:** Live HTML still lists canonical `getassure.com`. `www` is not attached.
**Steps:**
1. Zone `getassureai.com` is on Cloudflare (NS `cloe.ns.cloudflare.com` / `milan.ns.cloudflare.com`).
2. HTTPS is live: `curl -I https://getassureai.com` returns 200 (2026-09-01).
3. Add `www.getassureai.com` only if you want the www host (it did not resolve in this lookup).
4. Canonical URLs in `landing/` already use `https://getassureai.com`.
5. Run `./scripts/sync-webpage.sh` and `npx wrangler deploy` so live canonicals match.

**Effort:** 2–4 hours (mostly propagation wait)
**Acceptance:** `curl -I https://getassureai.com` returns 200.

---

## P1 — Pre-Launch Polish

### P1.1 Mobile-Responsive Compose
**Files:** `static/style.css`, `templates/index.html`
**Problem:** No evidence of mobile layout. One-screen design is perfect for mobile but may not be implemented.
**Spec:**
- `< 768px`: Single column. Question → Preview → Button → Answer stack vertically.
- Touch targets: min 44px height.
- Font size: min 16px (prevents iOS zoom on input focus).
- Live preview panel: collapsible or below the fold.
- Model pills: horizontal scroll or wrap to 2 rows.

**Effort:** 1–2 days
**Acceptance:** Tested on iPhone Safari and Chrome Android. No horizontal scroll.

---

### P1.2 Privacy Hero Badge
**File:** `templates/index.html`
**Problem:** Privacy is in the header text but not visually prominent. "Closed to the internet" is in Advanced.
**Spec:**
- Always-visible chip next to the edition chip:
  - 🔒 "Running locally" (if Ollama is default)
  - 🌐 "Connected to Gemini" (if cloud model is active)
  - 🌐 "Connected to DeepSeek" etc.
- Clicking the chip opens a tooltip: "Your data stays on this machine. Send goes only to [provider]."
- If user switches to "Open to the internet" in Advanced, chip changes to ⚠️ "Web search enabled."

**Effort:** 4 hours
**Acceptance:** Badge is visible on every screen. State changes when model/setting changes.

---

### P1.3 Add User-Facing Notes to Process Flows
**File:** `PEM.md` (Mermaid diagrams)
**Problem:** Flows show `Ground on?` and `Send / direct?` as user decisions. They're automatic now.
**Fix:** Add callout boxes above each diagram:

```
> **User note:** Ground runs automatically when you attach a file. No checkbox needed.
> **User note:** Send is the default. Copy the prompt with Option+Click or Cmd+Shift+Enter.
```

**Effort:** 30 minutes

---

### P1.4 Unify CSS Cache Busters
**Files:** `templates/index.html`, `landing/assets/site.css`, `landing/index.html`
**Problem:** `?v=assure-29`, `?v=assure-28`, `?v=21`, `?v=20` referenced inconsistently.
**Fix:** Define one variable per environment:
- App: `ASSURE_CSS_VERSION = "assure-29"`
- Landing: `LANDING_CSS_VERSION = "21"`
Reference only the variable. Update in one place.

**Effort:** 1 hour

---

### P1.5 Document Follow-Up Chip Generation
**Files:** `templates/index.html`, backend
**Problem:** "Follow-up chips after a reply" is mentioned but not specified.
**Spec (choose one and document):**

**Option A — Intent-based hardcoded:**
- Research: "What are the risks?", "Simplify", "Compare with alternatives"
- Design: "What are the tradeoffs?", "Next step?", "Budget estimate?"
- Comparison: "Which is cheaper?", "Switching cost?", "Long-term viability?"
- Debug: "How do I test this?", "Root cause?", "Similar issues?"
- Analysis: "What does this prove?", "What doesn't this prove?", "Confidence interval?"

**Option B — AI-extracted from answer:**
- Parse answer for unanswered questions, hedged claims, or comparative statements.
- Generate 3 chips from those.
- Higher effort, more dynamic.

**Recommendation:** Start with Option A (hardcoded per intent). Ship fast. Move to Option B when you have usage data.

**Effort:** 2 hours (Option A)
**Acceptance:** 3 chips appear after every answer. Clicking a chip pre-fills the question box and triggers a new Send.

---

### P1.6 Verify Landing Page Matches New Product
**File:** `landing/index.html`
**Problem:** Landing may still reference old UX patterns.
**Checklist:**
- [ ] No mention of "3-step setup" or "configure your workflow"
- [ ] Hero describes one-screen Compose
- [ ] Screenshot/mockup shows new UI (question + preview + button)
- [ ] Pricing shows Free + Pro only
- [ ] No "Download" buttons enabled (dist/ is still empty)
- [ ] FAQ does not contradict new UX

**Effort:** 2 hours

---

## P2 — Post-Launch Roadmap

### P2.1 Investigate Swarm 0.398 Score
**File:** `logs/swarm.patch` (if exists)
**Problem:** The "Compose one-screen" swarm had an overall score of 0.398. The verdict ignored this.
**Action:** Review swarm logs. Identify what dragged the score down. It may flag architectural debt or edge cases in the new UX.

**Effort:** 2 hours

---

### P2.2 Voice Input
**File:** `templates/index.html`
**Spec:** Microphone button next to question field. Uses Web Speech API or Whisper via backend. Transcribe → populate question field → trigger compile preview.

**Effort:** 1–2 days
**Rationale:** "Ask one question" is naturally voice-first. But text works for v1.

---

### P2.3 Answer Evolution (Diff)
**Files:** `history.py`, `templates/index.html`
**Spec:** On `/history`, group by question text. Show a "See changes" link that renders a unified diff between two answers to the same question. Useful for tracking how answers change when you attach different files or refine.

**Effort:** 2 days
**Rationale:** `history.sqlite` already stores the data. The UI is the gap.

**Assigned (2026-08-31):** Next `swarm_develop` task. Server-side unified diff in `history.py` first. Compose `/history` "See changes" after that. Do not start until Cursor MCP `pem` lists tools. Not a CLI swarm.

---

### P2.4 Re-Enable Language Switcher
**File:** `templates/index.html`
**Trigger:** When non-English traffic exceeds 15% of total.
**Action:** Uncomment the language switcher. Ensure all 7 catalogs are up to date with new strings (live preview, Refine, confidence, etc.).

**Effort:** 1 day

---

## Summary Table

| Priority | Count | Total Effort | Owner |
|----------|-------|--------------|-------|
| P0 | 6 items | ~8 hours + DNS propagation | Dev + DevOps |
| P1 | 6 items | ~4 days | Dev + Designer |
| P2 | 4 items | ~1 week | Dev |

**Critical path:** P0.1 → P0.6 → P1.1 → P1.6 → Launch

---

## Go / No-Go Checklist (Use Before Any Public Announcement)

- [ ] P0.1: "For people who do not want..." section rewritten
- [ ] P0.2: "What is Assure?" intro fixed
- [ ] P0.3: Editions table shows 2 SKUs in overview
- [ ] P0.4: Capability section updated
- [ ] P0.5: Confidence sentence is deterministic
- [x] P0.6: `curl -I https://getassureai.com` returns 200
- [ ] P1.1: Mobile layout tested on real devices
- [ ] P1.2: Privacy badge visible on all screens
- [ ] P1.6: Landing page matches product reality
- [ ] Final: `python -m unittest` still passes (currently 73 ok)

**If any P0 is unchecked → NO-GO.**
**If any P1 is unchecked → Soft no-go. Fix before Product Hunt / Hacker News.**

---

*Final Action Register | Independent Red-Hat Output | 2026-08-31*
