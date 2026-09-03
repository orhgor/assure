# Assure — Final Consolidated Recommendations
## Synthesis of Two Red-Hat Audits | 2026-08-31

---

## 1. The Core Problem (Both Audits Agree)

**Assure asks too many questions before giving one answer.**

A first-time user lands on Compose and must choose:
- Workflow (Quick / Compare & Validate / Refine & Verify)
- Intent (Research / Design / Comparison / Debug / Analysis)
- Models (6 pills, connected/not connected)
- Send vs Copy
- Advanced options (closed/open, cheap, class, notes)
- Ground checkbox

**The Steve Jobs test fails:** A user cannot get a trusted answer in under 30 seconds without reading anything.

---

## 2. What Both Audits Agree On (No Debate)

| # | Recommendation | Both Audits | Severity |
|---|----------------|-------------|----------|
| 1 | **Cut the 3-step tour** | ✅ Agree — replace with live preview | P0 |
| 2 | **Binary trust badge** (Verified / Review needed) | ✅ Agree — kill overlap % | P0 |
| 3 | **Live prompt preview** | ✅ Agree — this is the "aha" moment | P0 |
| 4 | **Privacy as headline** | ✅ Agree — move from footer to header | P0 |
| 5 | **Suggested follow-ups** | ✅ Agree — keep conversation going | P1 |
| 6 | **Confidence statement** | ✅ Agree — "Models agree on X%" | P0 |
| 7 | **Mobile-responsive layout** | ✅ Agree — single column, stacked | P1 |
| 8 | **Cut token/cost lines from main UI** | ✅ Agree — hover or CLI only | P1 |
| 9 | **Replace thumbs with "Refine"** | ✅ Agree — thumbs teach nothing | P1 |
| 10 | **Merge Team + Self-hosted into Pro** | ✅ Agree — 4 editions → 2 | P0 |
| 11 | **History as dropdown, not full tab** | ✅ Agree — storage anxiety | P1 |
| 12 | **Auto-ground when files attached** | ✅ Already implemented — just hide checkbox | P0 |

---

## 3. Where the Audits Disagree (Resolved)

### Disagreement 1: Cut vs Hide

| Audit 1 (Original) | Audit 2 (Combined) | Resolution |
|-------------------|---------------------|------------|
| "Cut 3 workflows to 1" | "Hide Quick/Refine behind Advanced" | **Hide, don't cut.** Compare & Validate is the default. Power users need Quick and Refine. The sin is *visibility*, not existence. |
| "Cut 5 intents" | "Auto-detect with dropdown override" | **Auto-detect with override.** Don't force a choice. Default to Auto. Show the dropdown only when the user clicks "Change intent." |
| "Cut Copy/Send radios" | "Default Send, Copy via Option+Click" | **Default Send, hide Copy.** Copy is a power-user escape hatch. It should exist but not be a primary decision. |
| "Cut Classes/Library/Learn entirely" | "Hide behind Prompt Library tab" | **Hide, don't cut.** These are genuinely useful for 1% of users. The sin is giving them equal billing with Compose. |
| "Cut 7 languages" | "Keep i18n, ship English only" | **Keep infrastructure, hide switcher.** Don't throw away working code. Remove the language switcher from v1 UI. Re-add when a market demands it. |

### Disagreement 2: Improve Loop

| Audit 1 (Original) | Audit 2 (Combined) | Resolution |
|-------------------|---------------------|------------|
| "Cut the improve loop entirely" | "Keep backend, make invisible" | **Keep backend, zero UI exposure.** The bandit works. It makes the product smarter. But no user should ever see "variation_id" or "performance_score." |

### Disagreement 3: Developer Surfaces (CLI, MCP, Swarm, Desktop)

| Audit 1 (Original) | Audit 2 (Combined) | Resolution |
|-------------------|---------------------|------------|
| "Cut Desktop, MCP, Swarm from v1" | "Keep in repo, don't market to mainstream" | **Keep in repo, remove from landing page and product docs.** These are developer surfaces. They don't hurt core UX. But they shouldn't be on the marketing site or in the first-run experience. |

---

## 4. The New Compose (One Screen Philosophy)

```
┌─────────────────────────────────────────────────────────────┐
│  ✓ Assure          "Answers you can trust."               │
│  🔒 Your data stays on your machine                         │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  Ask your question...                              [🎤]   │
│                                                             │
│  [📎 Attach file]                                           │
│                                                             │
│  ┌─────────────────────────────────────────────────────┐    │
│  │ 🔮 Live Preview (updates as you type)                │    │
│  │                                                     │    │
│  │ Rewriting for: Gemini + DeepSeek                    │    │
│  │ Intent detected: Research                           │    │
│  │ Ground: Checking against your file...               │    │
│  └─────────────────────────────────────────────────────┘    │
│                                                             │
│  [ ─────────── Get my answer ─────────── ]                  │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│                                                             │
│  ✅ Verified                                                │
│  Gemini and DeepSeek agree on 94% of this answer.           │
│  One claim about market share was flagged as unsupported.   │
│                                                             │
│  [Your answer appears here...]                              │
│                                                             │
│  [What are the risks?]  [Simplify]  [Compare alternatives]  │
│                                                             │
│  [↻ Refine this answer]                                     │
│                                                             │
├─────────────────────────────────────────────────────────────┤
│  Recent: "Compare AWS vs GCP" · "Summarize Q3 report"       │
└─────────────────────────────────────────────────────────────┘
```

### What's Gone (Hidden, Not Cut)

| Element | Where It Went |
|---------|---------------|
| Workflow radios | Behind "⚙️ Advanced" toggle, collapsed by default |
| Intent dropdown | Auto-detected. Override appears only if user clicks "Change intent" |
| Copy the prompt | Option+Click on "Get my answer" or Cmd/Ctrl+Shift+Enter |
| Ground checkbox | Automatic when file is attached. No UI element. |
| Advanced options | Single "⚙️ Advanced" toggle. Opens a drawer below the form. |
| Token/cost line | Hover over "Get my answer" button shows estimated cost |
| Thumbs up/down | Replaced by "↻ Refine this answer" |
| History tab | "Recent" dropdown below the answer. Full history behind "View all" → /history |
| Language switcher | Removed from v1 UI. i18n code stays. |
| Classes / Library / Learn | Behind "Prompt Library" in header nav. Not on Compose page. |

---

## 5. Feature Matrix: Cut / Hide / Keep / Add

| Feature | Audit 1 | Audit 2 | Final Verdict | Rationale |
|---------|---------|---------|---------------|-----------|
| **3-step tour** | Cut | Cut | **CUT** | Replace with live preview |
| **Workflow radios** | Cut to 1 | Hide behind Advanced | **HIDE** | Default Compare & Validate. Power users can switch. |
| **Intent dropdown** | Cut | Auto-detect + override | **AUTO-DETECT** | Default to Auto. Show override on demand. |
| **Copy vs Send** | Cut Copy | Default Send, hide Copy | **HIDE COPY** | Option+Click or keyboard shortcut |
| **Live preview** | Add | Add | **ADD (P0)** | The "aha" moment. Shows the machine working. |
| **Binary trust badge** | Add | Add | **ADD (P0)** | Verified / Review needed. Kill the %. |
| **Confidence statement** | Add | Add | **ADD (P0)** | "Models agree on X%. Y claim flagged." |
| **Token/cost lines** | Cut from UI | Hover/CLI only | **HOVER ONLY** | Transparency without clutter |
| **Thumbs feedback** | Cut | Replace with Refine | **REPLACE** | "↻ Refine this answer" button |
| **Improve loop** | Cut entirely | Invisible backend | **INVISIBLE** | Works. Zero UI exposure. |
| **Classes / Library / Learn** | Cut entirely | Hide behind tab | **HIDE** | Prompt Library tab. Not on Compose. |
| **Class export formats** | Cut | Cut for v1 | **CUT FROM V1** | .cursorrules, .mdc, Fabric, DSPy — v2 |
| **4 editions** | Merge to 2 | Merge Team/Self-hosted | **2 EDITIONS** | Free (10/day) and Pro (unlimited) |
| **Desktop app** | Cut from v1 | Keep in repo, no marketing | **NO MARKETING** | Build scripts stay. Not on landing page. |
| **MCP server** | Cut from v1 | Keep, document for devs | **DEV DOCS ONLY** | Not on landing page |
| **Swarm** | Cut from v1 | Keep in repo, no docs | **NO DOCS** | Research project. Revisit at 10K users. |
| **7 languages** | Cut to English | Ship English only | **ENGLISH ONLY V1** | Keep i18n code. Remove UI switcher. |
| **Advanced options panel** | Cut entirely | Keep, smart defaults | **COLLAPSED DEFAULT** | Single toggle. Smart defaults inside. |
| **Trust strip (overlap %)** | Cut | Binary badge | **BINARY BADGE** | Verified / Review needed |
| **Ground checkbox** | Auto | Already auto | **HIDE CHECKBOX** | Automatic when files attached |
| **History tab** | Dropdown | Dropdown | **DROPDOWN** | Recent 3 on Compose. Full history behind link. |
| **Suggested follow-ups** | Add | Add | **ADD (P1)** | 3 contextual chips post-answer |
| **Mobile layout** | Add | Add | **ADD (P1)** | Single column, stacked |
| **Privacy header** | Add | Add | **ADD (P0)** | Badge in header. Default closed. |
| **Voice input** | Add (v2) | Add (v2) | **V2** | Microphone button |
| **Answer evolution (diff)** | Add (v2) | Add (v2) | **V2** | Diff same question over time |
| **CLI** | Keep | Keep | **KEEP** | Power user surface. Document well. |
| **Citation scrub** | Keep | Keep | **KEEP** | Core moat. Automatic. |
| **Multi-model merge** | Keep | Keep | **KEEP** | Core value. Default workflow. |
| **File upload** | Keep | Keep | **KEEP** | Core value proposition. |
| **Local-first** | Keep | Keep | **KEEP** | Privacy as brand. Default to Ollama if detected. |
| **Cost router** | Keep | Keep | **KEEP** | Backend. No UI exposure. |

---

## 6. The Operational Truth (Both Audits Missed)

**The remaining public-URL blocker is HTTPS, not nameservers.**

| Blocker | Status | Action |
|---------|--------|--------|
| `getassureai.com` nameservers | `cloe` / `milan` (2026-09-01) | Done |
| `getassureai.com` HTTP | 200, Worker landing | Done |
| `getassureai.com` HTTPS | 200 (2026-09-01) | Done |
| `www.getassureai.com` | Did not resolve | Optional attach |
| Live canonical | still `getassure.com` | **Sync `webpage`** |
| Public installer | None | **Source install only for now** |
| PyPI | Not published | **Post-launch** |

**Go/No-Go Rule:** Do not announce publicly until:
1. DNS resolves to Cloudflare
2. Compose is one-screen (this doc)
3. Trust badge is binary

---

## 7. Final Verdict

**The product is built. The moat is real (multi-model consensus + citation scrub). The problem is presentation.**

Assure doesn't need more features. It needs **fewer visible decisions**.

> "Ask one question. Get one answer." — This promise is broken by the current UI. Fix the UI, and the product sells itself.

---

*Synthesized from Red-Hat Audit #1 (Original) and Red-Hat Audit #2 (Combined Assessment) | 2026-08-31*
