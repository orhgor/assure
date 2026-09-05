# Assure — Prioritized Launch Action Plan
## From Two Red-Hat Audits | Phased Execution

---

## Phase 0: Operational Blockers (Do First — No Exceptions)

| # | Task | Owner | Deadline | Blocker? |
|---|------|-------|----------|----------|
| 0.1 | Move `getassureai.com` nameservers to Cloudflare | DevOps | Day 1 | Done (`cloe` / `milan`, 2026-09-01) |
| 0.2 | Attach Cloudflare Worker custom domain to `getassureai.com` | DevOps | Day 2 | Done (apex HTTP 200) |
| 0.3 | Verify HTTPS on `getassureai.com` | DevOps | Day 3 | Done (`https://getassureai.com/` 200, 2026-09-01) |
| 0.4 | Update `landing/` canonical URLs to `getassureai.com` | Dev | Day 3 | Done in this tree; live HTML still `getassure.com` |
| 0.5 | Run `./scripts/sync-webpage.sh` and `npx wrangler deploy` | Dev | Day 3 | 🟡 Soft block (needed so live canonicals match) |

**Rule:** No public announcement until 0.1–0.3 are green.

---

## Phase 1: Compose One-Screen (Week 1)

**Goal:** A user can get a trusted answer in under 30 seconds with zero reading.

| # | Task | File(s) | Effort | Acceptance Criteria |
|---|------|---------|--------|---------------------|
| 1.1 | **Remove 3-step tour** | `templates/index.html`, JS tour logic | 2h | No `assure.tour.v1` localStorage check. No overlay. |
| 1.2 | **Add live prompt preview** | `templates/index.html`, JS, CSS | 2d | As user types, a panel shows: "Rewriting for [models]... Intent: [auto-detected]..." Updates in real-time (debounced 300ms). |
| 1.3 | **Auto-detect intent** | New: `intent_detector.py` | 1d | Input text + file extension → intent. 90% accuracy on test cases. Falls back to "Research" if uncertain. |
| 1.4 | **Hide intent dropdown by default** | `templates/index.html` | 2h | Intent shows as a chip (e.g., "Research ✎"). Clicking opens dropdown. Default is auto-detected chip. |
| 1.5 | **Hide workflow radios behind Advanced** | `templates/index.html` | 2h | Default: Compare & Validate. "⚙️ Advanced" toggle reveals Quick / Refine & Verify. |
| 1.6 | **Hide Copy behind keyboard shortcut** | `templates/index.html`, JS | 2h | Default button: "Get my answer". Option+Click (Mac) / Alt+Click (Win) shows "Copy prompt instead". Also Cmd/Ctrl+Shift+Enter. |
| 1.7 | **Remove Ground checkbox** | `templates/index.html` | 1h | Ground runs automatically when file is attached. No UI element. |
| 1.8 | **Collapse Advanced options to single toggle** | `templates/index.html`, CSS | 4h | One "⚙️ Advanced" button. Opens drawer with: closed/open, cheap route, saved class, route notes, add model. |
| 1.9 | **Remove language switcher from header** | `templates/index.html` | 1h | Comment out switcher. i18n code stays. All catalogs still load. |
| 1.10 | **Replace History tab with Recent dropdown** | `templates/index.html`, JS | 4h | Below answer area: "Recent: [chip] [chip] [chip] · View all →". Clicking "View all" goes to `/history`. |
| 1.11 | **Remove token/cost line from answer panel** | `templates/index.html` | 1h | Move to hover tooltip on "Get my answer" button. |
| 1.12 | **Replace thumbs with "Refine this answer"** | `templates/index.html`, JS | 3h | Remove 👍/👎. Add "↻ Refine this answer" button. Triggers red-hat workflow on same question. |

**Phase 1 Exit Criteria:**
- [ ] Compose is one screen, no scrolling required for first-run
- [ ] A user can type a question, attach a file, and click one button
- [ ] No decision required before first answer

---

## Phase 2: Trust & Confidence (Week 2)

**Goal:** The answer feels trustworthy, not technical.

| # | Task | File(s) | Effort | Acceptance Criteria |
|---|------|---------|--------|---------------------|
| 2.1 | **Binary trust badge** | `templates/index.html`, CSS, JS | 1d | Replace trust strip with: ✅ Verified (green) or ⚠️ Review needed (amber). No percentages. |
| 2.2 | **Confidence statement** | `quality.py`, `templates/index.html` | 1d | Sentence under badge: "[Model A] and [Model B] agree on X% of this answer. Y claims were flagged as unsupported." |
| 2.3 | **Privacy badge in header** | `templates/index.html`, CSS | 2h | Header shows: 🔒 "Your data stays on your machine." |
| 2.4 | **Default to local models** | `pipelines.py`, `templates/index.html` | 1d | If Ollama is detected, default to local. Show "Running locally" chip. Cloud models are fallback. |
| 2.5 | **"Closed to the internet" as hero setting** | `templates/index.html` | 2h | Advanced toggle defaults to "Closed". When open, show a lock icon. |
| 2.6 | **Suggested follow-ups** | New: `followup_generator.py`, `templates/index.html` | 2d | Post-answer, show 3 chips: "What are the risks?", "Simplify", "Compare alternatives". Generated from answer content + intent. |
| 2.7 | **Improve loop: zero UI exposure** | `templates/index.html`, JS | 2h | Remove any UI that shows variation_id, performance_score, or bandit status. Backend stays. |

**Phase 2 Exit Criteria:**
- [ ] User sees "Verified" or "Review needed" — never a percentage
- [ ] User understands *why* the answer is trusted (confidence statement)
- [ ] Privacy is visible, not buried

---

## Phase 3: Polish & Mobile (Week 3)

**Goal:** The product feels finished on any device.

| # | Task | File(s) | Effort | Acceptance Criteria |
|---|------|---------|--------|---------------------|
| 3.1 | **Mobile-responsive Compose** | `static/style.css`, `templates/index.html` | 2d | Single column on <768px. Question → Preview → Button → Answer stack vertically. Touch-friendly. |
| 3.2 | **Merge editions: Free + Pro only** | `editions.py`, `templates/pricing.html`, `landing/pricing.html` | 1d | Free (10 Sends/day). Pro ($5/mo, unlimited). Team and Self-hosted become "Pro (Team)" and "Pro (Self-hosted)" — same SKU, different install. |
| 3.3 | **Hide Classes/Library/Learn from main nav** | `templates/index.html` | 2h | Move to "Prompt Library" in header. Not visible on Compose. |
| 3.4 | **Remove class export buttons from Free** | `templates/index.html`, `exporters.py` | 2h | Free shows "Export available on Pro" with upgrade CTA. Pro shows export buttons. |
| 3.5 | **Landing page: remove Desktop/MCP/Swarm from hero** | `landing/index.html` | 2h | Landing sells: Compose (web), CLI (power users). No mention of desktop download, MCP, or swarm. |
| 3.6 | **Landing page: English only** | `landing/index.html` | 1h | Remove language switcher from landing. Keep i18n infrastructure. |
| 3.7 | **Update CSS cache buster** | `templates/index.html`, `landing/assets/site.css` | 30m | App: `?v=assure-29`. Landing: `?v=21`. |
| 3.8 | **Final QA: 30-second test** | Manual | 4h | 5 fresh users. Time to first trusted answer. Target: <30 seconds. |

**Phase 3 Exit Criteria:**
- [ ] Product works on phone, tablet, and desktop
- [ ] Landing page sells one thing: ask a question, get a verified answer
- [ ] 5/5 fresh users get an answer in under 30 seconds

---

## Phase 4: Post-Launch (Month 2+)

| # | Task | Priority | Rationale |
|---|------|----------|-----------|
| 4.1 | Voice input (microphone button) | P2 | "Ask one question" screams voice. But text works for v1. |
| 4.2 | Answer evolution / diff | P2 | `history.sqlite` already has the data. Build the UI. |
| 4.3 | Re-add language switcher | P2 | When a non-English market shows traction. |
| 4.4 | Class export formats (.cursorrules, .mdc, etc.) | P2 | When prompt engineers ask for it. |
| 4.5 | Desktop app marketing | P2 | When `dist/` has a real download URL. |
| 4.6 | MCP documentation | P2 | When Cursor users discover it organically. |
| 4.7 | Swarm v2 | P3 | When you have 10K users and a team to maintain it. |
| 4.8 | PyPI publish | P2 | When install friction becomes the #1 support ticket. |

---

## Risk Register

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| DNS propagation takes >48h | Medium | High | Initiate NS move immediately. Have `assure.orhangorenn.workers.dev` as fallback. |
| Auto-detect intent is <90% accurate | Medium | Medium | Fallback to "Research" (safest default). Monitor and iterate. |
| Power users revolt at hidden features | Low | Medium | All features still exist. They're just not in the first-run path. Document in /advanced or blog. |
| Live preview is slow | Medium | High | Debounce at 300ms. Compile on frontend with cached templates. Don't hit backend on every keystroke. |
| "Refine" button confuses users | Low | Low | Label clearly: "↻ Refine this answer (runs critic + rewrite)". Tooltip explains. |

---

## Success Metrics

| Metric | Baseline | Target (30 days post-launch) |
|--------|----------|------------------------------|
| Time to first answer | ~90 seconds (current) | <30 seconds |
| Compose abandonment rate | ~40% (estimated) | <15% |
| Free → Pro conversion | Unknown | >3% |
| Daily active Sends (Free) | Unknown | >50/day |
| Support tickets: "How do I..." | Unknown | <5% of total tickets |
| NPS score | Unknown | >40 |

---

*Action plan synthesized from Red-Hat Audit #1 and Red-Hat Audit #2 | 2026-08-31*
