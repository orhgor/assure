# Assure — Independent Red-Hat Assessment
## Post-Audit Implementation Review | 2026-08-31

---

## Attempt: What the Product Claims to Be

Assure has undergone a massive UX overhaul based on two prior Red-Hat audits. The new Compose is "one screen": question field, live compile preview, Auto-detected intent, one "Get my answer" button. The 3-step tour is gone — the live preview IS the tutorial. Workflow radios, Copy, Ground checkbox, language switcher, and token/cost lines are all hidden from first-run. Trust signals are binary (Verified / Review needed). Thumbs are replaced by "Refine this answer." Editions are simplified to Free + Pro. Team/Self-hosted are `--edition` flags, not store SKUs. A new `intent_detector.py` handles Auto intent. A new `/api/preview` endpoint powers the live preview. Unit tests are at 73 ok. Public HTTPS is live (`https://getassureai.com/` 200). Live HTML still lists canonical `getassure.com`.

---

## Critique: What the Verdict Got Wrong (And What It Missed)

### 1. The "For People Who Do Not Want the Engine Internals" Section Is Dangerously Stale

**Severity: P0 — This is the quick-reference section. It lies to the reader.**

| What It Says | What the Product Actually Does |
|--------------|-------------------------------|
| "3-step tour on the first Compose visit" | Tour is gone. Live preview is the tutorial. |
| "thumbs under the answer" | Replaced by "Refine this answer." |
| "Trust strip shows overlap after Send" | Binary badge: Verified / Review needed. No numbers. |
| "Running a first-visit tour, named intents, Send vs Copy radios" | All hidden or removed from first-run. |
| "Rate an answer — thumbs up / down" | Refine button posts rating 0 and re-runs red-hat. |

**This section is the first place a new developer or investor reads.** It describes the OLD product. If you ship PEM.md in its current state, you are documenting a product that no longer exists.

### 2. The Verdict Cherry-Picked the Swarm Score

The verdict claims: *"Swarm Verdict (Keep 0.98) — the automated architecture review gave this UX direction a near-perfect grade."*

**Reality from the swarm log:**
```
Compose one-screen (audits) | 9e7fa85604e22047 | Keep 0.98 in report, overall 0.398
```

The **overall score was 0.398**. The 0.98 was a sub-component. A score below 0.4 means the swarm had serious concerns. The verdict buried this. The swarm may have flagged issues the verdict ignored.

### 3. "What Is Assure?" Still Says "Pick a Shape (Intent)"

The intro paragraph: *"You type a question, pick a shape (intent), and usually attach a file."*

**No.** The user does NOT pick an intent. Intent is Auto-detected. The user CAN override with a chip, but the default path is zero configuration. The intro should say: *"You type a question and usually attach a file. Assure detects what you need and restructures the prompt."*

### 4. The Editions Table Contradicts the Text

The text says: *"Team remains `--edition team` on this machine, not a store SKU."*

The table still shows **4 columns**: Free | Pro | Team | Self-hosted.

If Team and Self-hosted are not SKUs, they should not be in the consumer-facing table. Keep them in the technical reference, but the product overview should show **2 columns only**.

### 5. The Capability Section Still Lists the Tour

*"Running a first-visit tour, named intents, Send vs Copy radios, and Advanced options so Compose stays small on first use."*

The tour is **cut**. This capability no longer exists. Listing it is false advertising.

### 6. Confidence Sentence Is Non-Deterministic

The spec says: *"Confidence sentence may include overlap % and flagged claims from real `quality` fields."*

**"May" is a bug, not a feature.** Every answer should have a confidence sentence. If `quality` fields are missing, the sentence should say *"Confidence data unavailable"* — not omit the sentence entirely. A missing confidence sentence breaks the trust UX.

### 7. Process Flows Still Show User Decisions That No Longer Exist

The Mermaid diagrams show:
- `Ground on?` as a decision diamond
- `Send / direct?` as a fork

These are **backend-accurate** but **user-misleading**. Ground is automatic when files are attached. Send is the default; Copy is a hidden shortcut. The user-facing documentation should clarify that these are automatic, not user choices.

### 8. Mobile-First Is Still Unconfirmed

The verdict flags this: *"Not explicitly shipped."*

The new PEM.md does not mention mobile responsiveness at all. The one-screen design is **perfect** for mobile (single column, stacked), but there's no evidence it's implemented. This is a P1 gap, not a blocker — but it should be on the roadmap.

### 9. Privacy Is Still Not a "Hero" Feature

The verdict says: *"Partial."*

I agree. The privacy line moved to the header, which is good. But "Closed to the internet" is still buried in Advanced options. The default behavior (local-first if Ollama is detected) is smart, but **invisible**. A hero feature is **visible**. There should be a prominent badge: "🔒 Running locally" or "🌐 Connected to Gemini" — always visible, never hidden.

### 10. CSS Cache Buster Inconsistencies

- Header claims: `?v=assure-29`
- Process flows section references: `?v=assure-28`
- Landing claims: `?v=21` and `?v=20` in different places

These are minor but sloppy. A single source of truth for cache busters prevents stale assets.

### 11. The Verdict Misunderstands the Deployment Model

The verdict says: *"Ship this UX immediately to the Worker."*

**The Worker serves the landing page, not the app.** The app is local (`assure --web`). You cannot "ship" the Compose UX to a Cloudflare Worker — it requires the local Flask server, SQLite, and API keys. The landing page can and should be updated, but the app UX is local-only until there's a hosted version.

---

## What the Verdict Got Right

| Claim | Assessment |
|-------|------------|
| "Massive Progress" | ✅ True. The UX transformation is real. |
| "Almost every major complaint has been cut, hidden, or replaced" | ✅ True. The one-screen Compose is a genuine improvement. |
| "DNS is the only operational blocker" | ✅ True. But product docs have blockers too (see above). |
| "Can a user get a trusted answer in under 30 seconds?" | ✅ True, IF they don't read the stale docs first. |

---

## Final: Recommendations

### P0 — Launch Blockers (Fix Before Public)

| # | Issue | File | Fix |
|---|-------|------|-----|
| P0.1 | **Stale "For people who do not want..." section** | `PEM.md` | Rewrite entirely. Remove all references to tour, thumbs, trust strip %, Send vs Copy radios. Match the new Compose reality. |
| P0.2 | **"What is Assure?" intro says "pick a shape"** | `PEM.md` | Change to: "You type a question and usually attach a file. Assure detects what you need." |
| P0.3 | **Editions table shows 4 SKUs** | `PEM.md` | Product overview: 2 columns (Free, Pro). Technical reference: keep 4 columns with footnote. |
| P0.4 | **Capability section lists tour** | `PEM.md` | Remove "Running a first-visit tour" from capabilities. Replace with "Live prompt preview that teaches by doing." |
| P0.5 | **Confidence sentence is "may"** | `quality.py`, `templates/index.html` | Make deterministic. Every answer gets: "[Models] agree on X%. Y claims flagged." If data missing: "Confidence check pending." |
| P0.6 | **DNS/SSL** | Namecheap / Cloudflare | Move nameservers. Attach Worker domain. Verify HTTPS. |

### P1 — Pre-Launch Polish (Fix Before Announcing)

| # | Issue | File | Fix |
|---|-------|------|-----|
| P1.1 | **Mobile-responsive layout** | `static/style.css`, `templates/index.html` | Single column <768px. Touch-friendly buttons. Test on iPhone + Android. |
| P1.2 | **Privacy hero badge** | `templates/index.html` | Visible badge: "🔒 Running locally" (Ollama) or "🌐 Connected to [provider]". Always visible, not in Advanced. |
| P1.3 | **Process flows need user-facing notes** | `PEM.md` | Add callouts: "Ground is automatic when files are attached." "Send is default; Copy is Option+Click." |
| P1.4 | **CSS cache buster consistency** | `templates/index.html`, `landing/` | Single source of truth. One variable, one version. |
| P1.5 | **Follow-up chips: how are they generated?** | `templates/index.html`, backend | Document the generation logic. Hardcoded per intent? AI-extracted from answer? |
| P1.6 | **Landing page matches new product** | `landing/index.html` | Ensure landing copy reflects one-screen Compose, not the old 3-step flow. |

### P2 — Post-Launch

| # | Issue | File | Fix |
|---|-------|------|-----|
| P2.1 | **Investigate swarm 0.398 overall score** | `logs/swarm.patch` | Review what the swarm flagged. There may be hidden issues. |
| P2.2 | **Voice input** | `templates/index.html` | Microphone button on question field. |
| P2.3 | **Answer evolution (diff over time)** | `history.py`, UI | Diff same question across multiple Sends. |
| P2.4 | **Re-add languages when markets demand** | `i18n.py` | English-only v1. Spanish or Chinese next based on traffic. |

---

## The One-Sentence Verdict

**The product UX is 90% there. The documentation is 60% there. Fix the docs before you fix the DNS, or you'll launch a great product that nobody understands.**

---

*Independent Red-Hat Assessment | Synthesized from updated PEM.md and prior audit verdict | 2026-08-31*
