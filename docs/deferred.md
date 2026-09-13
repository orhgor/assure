# Deferred — Not v1.0

Everything that is **not** on the golden path goes here. No exceptions during Execution Mode (Days 1–26).

**v1.0 scope cut (2026-09-10):** Ship gate is **Golden Path Steps 1–4 only** (⌘K orchestrator → Difference Engine side-by-side diff → click-to-merge). Steps 5–10 move to **v1.1 backlog** below — do not block Day 25 ICP demo or Day 26 tag on audit/benchmark/export work.

## v1.1 backlog — Golden Path Steps 5–10 (deferred from v1.0)

Source: [user-experience.md](./user-experience.md) steps 5–10. Code may exist on `staging` (e.g. PR #46 scan routes); **not in v1.0 validation or ship gate**.

| Step | User story (summary) | v1.0 status | v1.1 target |
| --- | --- | --- | --- |
| **5** | Red-Hat analysis ≥2 passes on compiled responses | Deferred | Sprint A — `.redhat-audit-btn`, pass-2 telemetry, drawer |
| **6** | Grammar/flow polish on Main without losing verified locks | Deferred | Sprint A — `.main-polish-btn` (route exists; not v1.0 gate) |
| **7** | Full-context scan for verifiable issues, numbers, citations | Deferred | Sprint B — `POST /api/projects/<id>/scan`, `.full-context-scan-btn` |
| **8** | Benchmark vs standard works; targeted enhancement | Deferred | Sprint B — `.benchmark-compare-btn`, results panel |
| **9** | Local fix — rewrite/reconfigure selected Main regions | Deferred | Sprint B — `.local-fix-btn`, Fix Locally panel |
| **10** | Export verified dossier (final ship artifact) | Deferred | Sprint C — `.export-complete`, dossier PDF/JDF bundle |

**Validation rule:** `tests/e2e/golden_path.spec.js` — v1.0 runs Steps 1–4 only; full 10-step spec tagged `@v1.1` and skipped until backlog cleared.

Visual audit logged **2026-09-10** against [visual-checklist.md](./visual-checklist.md) Executive-Grade Standard (local founder workbench @ `127.0.0.1:8801/app`, 1920×1080).

| Date | Item | Reason deferred |
| --- | --- | --- |
| 2026-09-10 | **Typography — heading scale drift** — computed h1=28px, h2≈15px, h3/founder title≈11px; spec requires h1=32, h2=24, h3=18 | Convergence (Days 22–24); mid-sprint visual fix |
| 2026-09-10 | **Typography — body size drift** — workbench body/runs stack/staging≈13–16px; Main ProseMirror≈13px; operator input≈14px; spec body=15px | Convergence |
| 2026-09-10 | **Typography — second font in code blocks** — `.ProseMirror pre` uses JetBrains Mono / Fira Code; spec is Inter only | Convergence |
| 2026-09-10 | **Typography — export CTA too small** — `#btn-export-dossier` computed font-size≈11px; reads subordinate vs primary ship action | Convergence |
| 2026-09-10 | **Spacing — non-scale padding** — `.founder-draft-header` horizontal padding 20px; `.full-context-results` padding 14px / margin 20px (allowed scale: 4,8,12,16,24,32,48,64) | Convergence |
| 2026-09-10 | **Spacing — command deck density** — top bar packs Saving spinner, ⚡ Investigate (⌘K), and Export with tight vertical rhythm | Convergence |
| 2026-09-10 | **Color — staging mock Tailwind literals** — `.diff-highlight` / `.push-to-main-btn` in `index.html` use `bg-yellow-500/20`, `text-yellow-200`, `bg-blue-600` instead of CSS accent variables | Convergence |
| 2026-09-10 | **Color — diff highlight contrast** — yellow diff text (`rgb(253,230,138)`) on `#1e293b` staging rail is low-contrast at executive viewing distance | Convergence |
| 2026-09-10 | **Color — export button near-black** — Export Verified Dossier uses `rgb(10,10,10)` fill rather than trust-blue (`#3b82f6`) accent standard | Convergence |
| 2026-09-10 | **Motion — transitions >200ms** — drawers/gutters/status use 220–400ms (`.provenance-panel-drawer`, `.workbench-right-drawer`, `.verification-gutter`, `.founder-sync-status`, etc.) | Convergence |
| 2026-09-10 | **Motion — operator prompt glow loop** — `operator-prompt-glow` 2.4s infinite animation; spec forbids playful/bouncy easing | Convergence |
| 2026-09-10 | **Copy — informal command deck** — ⚡ emoji + “Investigate…” ellipsis on primary ⌘K affordance; executive copy prefers verb-only (“Investigate”, “Run scan”) | Convergence |
| 2026-09-10 | **Copy — export drawer labels** — “Download Working File (.jdf)” / “Generate Verified Dossier (.pdf)” are noun-heavy; checklist prefers verb CTAs (“Export draft”, “Export dossier”) | Convergence |
| 2026-09-10 | **Surface — Main document title** — “Untitled Document” renders at ~11px tracked caps; title hierarchy weaker than adjacent action buttons (Polish / Scan / Attach) | Convergence |
| 2026-09-10 | **Surface — staging side-by-side clarity** — at 1920px only Claude column is fully in view; DeepSeek pane and second diff column require scroll — Difference Engine reads single-column | Convergence |
| 2026-09-10 | **Surface — static staging mock vs live Main** — hardcoded Boston liability demo copy in staging while Main is empty/different; visual disorientation during triage | Convergence |
| 2026-09-10 | **Surface — layered zero states** — runs stack (“No runs yet…”), workspace init card, and draft placeholder visible concurrently in left/center | Convergence |
| 2026-09-10 | **Surface — operator prompt occlusion** — ⌘K prompt floats over Main canvas and can cover the paragraph under edit (Fix Locally flow) | Convergence |
| 2026-09-10 | **Surface — code block styling in Main** — TipTap `pre` blocks use dark rail background inside white canvas; pasted/plain text can appear as executive “dark card” | Convergence |
| 2026-09-10 | **Surface — state rail active affordance** — gear icon highlighted while document view is active; active nav state ambiguous | Convergence |
| 2026-09-10 | **Surface — evidence drawer unaudited** — `#drawer-evidence` hidden at audit time; needs Convergence pass when lock-pill inspect path is wired in UI | Convergence |
| 2026-09-10 | **Compare-pane overlay (`compare_pane.js`)** — prompt spec overlay; golden path uses existing `.staging-canvas` + `orchestrator.js` | Use frozen shell; `/api/runs/compare` wired for future overlay |

### Automated Codebase Audit Findings (Pending Human Review)

Strict automated scan of `prompt_matrix/static/`, `prompt_matrix/templates/`, and CSS — **2026-09-10**. Standard: [visual-checklist.md](./visual-checklist.md). Human triage required before Convergence fixes (SVG brand marks, chart defaults, and `:root` token definitions may be intentional).

**Totals:** Color **1035** · Spacing **369** · Typography **48** · Copy **102**

#### 1. Color violations (hardcoded hex / rgb / Tailwind accent classes)

Allowed accents: `#3b82f6`, `#10b981`, `#f59e0b`. Allowed backgrounds: white, `#f8fafc`, `#1e293b`. Checklist rule: no hardcoded hex outside CSS variables.

| File | Line | Type | Value | Snippet |
| --- | ---: | --- | --- | --- |
| `prompt_matrix/static/analytics.js` | 52 | hardcoded-hex | `#6b7280` | ticks: { font: { size: 11 }, color: "#6b7280", maxRotation: 0 }, |
| `prompt_matrix/static/analytics.js` | 57 | hardcoded-rgb/rgba | `rgba(17, 24, 39, 0.08)` | grid: { color: "rgba(17, 24, 39, 0.08)" }, |
| `prompt_matrix/static/analytics.js` | 58 | hardcoded-hex | `#6b7280` | ticks: { stepSize: 25, font: { size: 11 }, color: "#6b7280" }, |
| `prompt_matrix/static/analytics.js` | 116 | hardcoded-hex | `#0a0a0a` | backgroundColor: "#0a0a0a", |
| `prompt_matrix/static/analytics.js` | 201 | hardcoded-hex | `#0a0a0a` | backgroundColor: ["#0a0a0a", "#6b7280", "#9ca3af", "#d1d5db"], |
| `prompt_matrix/static/analytics.js` | 201 | hardcoded-hex | `#6b7280` | backgroundColor: ["#0a0a0a", "#6b7280", "#9ca3af", "#d1d5db"], |
| `prompt_matrix/static/analytics.js` | 201 | hardcoded-hex | `#9ca3af` | backgroundColor: ["#0a0a0a", "#6b7280", "#9ca3af", "#d1d5db"], |
| `prompt_matrix/static/analytics.js` | 201 | hardcoded-hex | `#d1d5db` | backgroundColor: ["#0a0a0a", "#6b7280", "#9ca3af", "#d1d5db"], |
| `prompt_matrix/static/confidence_highlighter.js` | 3 | tailwind-accent-class | `bg-green-200` | * Classes: bg-green-200 (>0.8), bg-yellow-200 (0.4–0.8), bg-red-200 (<0.4). |
| `prompt_matrix/static/confidence_highlighter.js` | 3 | tailwind-accent-class | `bg-yellow-200` | * Classes: bg-green-200 (>0.8), bg-yellow-200 (0.4–0.8), bg-red-200 (<0.4). |
| `prompt_matrix/static/confidence_highlighter.js` | 3 | tailwind-accent-class | `bg-red-200` | * Classes: bg-green-200 (>0.8), bg-yellow-200 (0.4–0.8), bg-red-200 (<0.4). |
| `prompt_matrix/static/confidence_highlighter.js` | 11 | tailwind-accent-class | `bg-yellow-200` | if (!Number.isFinite(n)) return "bg-yellow-200"; |
| `prompt_matrix/static/confidence_highlighter.js` | 12 | tailwind-accent-class | `bg-green-200` | if (n > 0.8) return "bg-green-200"; |
| `prompt_matrix/static/confidence_highlighter.js` | 13 | tailwind-accent-class | `bg-yellow-200` | if (n >= 0.4) return "bg-yellow-200"; |
| `prompt_matrix/static/confidence_highlighter.js` | 14 | tailwind-accent-class | `bg-red-200` | return "bg-red-200"; |
| `prompt_matrix/static/jdf.bundle.js` | 1 | hardcoded-hex | `#e2e8f0` | (()=>{var te=(t=>typeof require<"u"?require:typeof Proxy<"u"?new Proxy(t,{get:(n,r)=>(typeof require<"u"?requi |
| `prompt_matrix/static/jdf.bundle.js` | 1 | hardcoded-hex | `#f8fafc` | (()=>{var te=(t=>typeof require<"u"?require:typeof Proxy<"u"?new Proxy(t,{get:(n,r)=>(typeof require<"u"?requi |
| `prompt_matrix/static/jdf.bundle.js` | 1 | hardcoded-hex | `#ffffff` | (()=>{var te=(t=>typeof require<"u"?require:typeof Proxy<"u"?new Proxy(t,{get:(n,r)=>(typeof require<"u"?requi |
| `prompt_matrix/static/jdf.bundle.js` | 1 | hardcoded-hex | `#334155` | (()=>{var te=(t=>typeof require<"u"?require:typeof Proxy<"u"?new Proxy(t,{get:(n,r)=>(typeof require<"u"?requi |
| `prompt_matrix/static/jdf.bundle.js` | 1 | hardcoded-hex | `#cbd5e1` | (()=>{var te=(t=>typeof require<"u"?require:typeof Proxy<"u"?new Proxy(t,{get:(n,r)=>(typeof require<"u"?requi |
| `prompt_matrix/static/jdf.bundle.js` | 1 | hardcoded-hex | `#94a3b8` | (()=>{var te=(t=>typeof require<"u"?require:typeof Proxy<"u"?new Proxy(t,{get:(n,r)=>(typeof require<"u"?requi |
| `prompt_matrix/static/jdf.bundle.js` | 1 | hardcoded-hex | `#0f172a` | (()=>{var te=(t=>typeof require<"u"?require:typeof Proxy<"u"?new Proxy(t,{get:(n,r)=>(typeof require<"u"?requi |
| `prompt_matrix/static/jdf_canvas.js` | 2090 | tailwind-accent-class | `text-yellow-600` | "inline-flex items-center text-xs text-yellow-600 bg-yellow-50 px-1.5 py-0.5 rounded-full ml-2 assure-cache-ba |
| `prompt_matrix/static/jdf_canvas.js` | 2090 | tailwind-accent-class | `bg-yellow-50` | "inline-flex items-center text-xs text-yellow-600 bg-yellow-50 px-1.5 py-0.5 rounded-full ml-2 assure-cache-ba |
| `prompt_matrix/static/jdf_tiptap.js` | 321 | tailwind-accent-class | `text-yellow-600` | "inline-flex items-center text-xs text-yellow-600 bg-yellow-50 px-1.5 py-0.5 rounded-full ml-2 assure-cache-ba |
| `prompt_matrix/static/jdf_tiptap.js` | 321 | tailwind-accent-class | `bg-yellow-50` | "inline-flex items-center text-xs text-yellow-600 bg-yellow-50 px-1.5 py-0.5 rounded-full ml-2 assure-cache-ba |
| `prompt_matrix/static/jdf_tiptap.js` | 375 | tailwind-accent-class | `text-yellow-600` | "inline-flex items-center text-xs text-yellow-600 bg-yellow-50 px-1.5 py-0.5 rounded-full ml-2 assure-cache-ba |
| `prompt_matrix/static/jdf_tiptap.js` | 375 | tailwind-accent-class | `bg-yellow-50` | "inline-flex items-center text-xs text-yellow-600 bg-yellow-50 px-1.5 py-0.5 rounded-full ml-2 assure-cache-ba |
| `prompt_matrix/static/jdf_tiptap.js` | 905 | tailwind-accent-class | `bg-yellow-200` | if (!Number.isFinite(n)) return "bg-yellow-200"; |
| `prompt_matrix/static/jdf_tiptap.js` | 906 | tailwind-accent-class | `bg-green-200` | if (n > 0.8) return "bg-green-200"; |
| `prompt_matrix/static/jdf_tiptap.js` | 907 | tailwind-accent-class | `bg-yellow-200` | if (n >= 0.4) return "bg-yellow-200"; |
| `prompt_matrix/static/jdf_tiptap.js` | 908 | tailwind-accent-class | `bg-red-200` | return "bg-red-200"; |
| `prompt_matrix/static/landing.css` | 2 | hardcoded-hex | `#1a4b8c` | --assure-trust-blue: #1A4B8C; |
| `prompt_matrix/static/landing.css` | 3 | hardcoded-hex | `#2e7d32` | --assure-confidence-green: #2E7D32; |
| `prompt_matrix/static/landing.css` | 4 | hardcoded-hex | `#d4a843` | --assure-accent-gold: #D4A843; |
| `prompt_matrix/static/landing.css` | 5 | hardcoded-hex | `#0a0e14` | --bg-deep: #0a0e14; |
| `prompt_matrix/static/landing.css` | 6 | hardcoded-hex | `#121820` | --bg-card: #121820; |
| `prompt_matrix/static/landing.css` | 7 | hardcoded-hex | `#2a3344` | --border-color: #2a3344; |
| `prompt_matrix/static/landing.css` | 8 | hardcoded-hex | `#f0f2f5` | --text-main: #f0f2f5; |
| `prompt_matrix/static/landing.css` | 9 | hardcoded-hex | `#a8b0bd` | --text-muted: #a8b0bd; |
| `prompt_matrix/static/landing.css` | 49 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 321 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 333 | hardcoded-hex | `#153d73` | background-color: #153d73; |
| `prompt_matrix/static/landing.css` | 334 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 363 | hardcoded-rgb/rgba | `rgba(248, 113, 113, 0.45)` | border: 1px solid rgba(248, 113, 113, 0.45); |
| `prompt_matrix/static/landing.css` | 364 | hardcoded-rgb/rgba | `rgba(248, 113, 113, 0.12)` | background: rgba(248, 113, 113, 0.12); |
| `prompt_matrix/static/landing.css` | 365 | hardcoded-hex | `#fecaca` | color: #fecaca; |
| `prompt_matrix/static/landing.css` | 437 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.4)` | border: 1px solid rgba(59, 130, 246, 0.4); |
| `prompt_matrix/static/landing.css` | 444 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.12)` | background: rgba(59, 130, 246, 0.12); |
| `prompt_matrix/static/landing.css` | 445 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 471 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.08)` | background: rgba(255, 255, 255, 0.08); |
| `prompt_matrix/static/landing.css` | 537 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.02)` | background: rgba(255, 255, 255, 0.02); |
| `prompt_matrix/static/landing.css` | 562 | hardcoded-rgb/rgba | `rgba(16, 185, 129, 0.15)` | background: rgba(16, 185, 129, 0.15); |
| `prompt_matrix/static/landing.css` | 577 | hardcoded-hex | `#6366f1` | border-left-color: #6366f1; |
| `prompt_matrix/static/landing.css` | 581 | allowed-accent-but-not-css-var | `#f59e0b` | border-left-color: #f59e0b; |
| `prompt_matrix/static/landing.css` | 618 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.12)` | border-left: 3px solid rgba(255, 255, 255, 0.12); |
| `prompt_matrix/static/landing.css` | 738 | hardcoded-rgb/rgba | `rgba(18, 20, 24, 0.5)` | background: rgba(18, 20, 24, 0.5); |
| `prompt_matrix/static/landing.css` | 779 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.04)` | background: var(--surface-elevated, rgba(255, 255, 255, 0.04)); |
| `prompt_matrix/static/landing.css` | 812 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.04)` | background: var(--surface-elevated, rgba(255, 255, 255, 0.04)); |
| `prompt_matrix/static/landing.css` | 882 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.06)` | background: rgba(255, 255, 255, 0.06); |
| `prompt_matrix/static/landing.css` | 985 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.02)` | background: rgba(255, 255, 255, 0.02); |
| `prompt_matrix/static/landing.css` | 1087 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.08)` | background: rgba(59, 130, 246, 0.08); |
| `prompt_matrix/static/landing.css` | 1096 | hardcoded-rgb/rgba | `rgba(16, 185, 129, 0.1)` | background: rgba(16, 185, 129, 0.1); |
| `prompt_matrix/static/landing.css` | 1098 | hardcoded-rgb/rgba | `rgba(16, 185, 129, 0.35)` | border-color: rgba(16, 185, 129, 0.35); |
| `prompt_matrix/static/landing.css` | 1102 | hardcoded-rgb/rgba | `rgba(239, 68, 68, 0.1)` | background: rgba(239, 68, 68, 0.1); |
| `prompt_matrix/static/landing.css` | 1103 | hardcoded-hex | `#f87171` | color: #f87171; |
| `prompt_matrix/static/landing.css` | 1104 | hardcoded-rgb/rgba | `rgba(239, 68, 68, 0.35)` | border-color: rgba(239, 68, 68, 0.35); |
| `prompt_matrix/static/landing.css` | 1108 | hardcoded-rgb/rgba | `rgba(245, 158, 11, 0.12)` | background: rgba(245, 158, 11, 0.12); |
| `prompt_matrix/static/landing.css` | 1109 | hardcoded-hex | `#d97706` | color: #d97706; |
| `prompt_matrix/static/landing.css` | 1110 | hardcoded-rgb/rgba | `rgba(245, 158, 11, 0.35)` | border-color: rgba(245, 158, 11, 0.35); |
| `prompt_matrix/static/landing.css` | 1114 | hardcoded-rgb/rgba | `rgba(239, 68, 68, 0.15)` | background: rgba(239, 68, 68, 0.15); |
| `prompt_matrix/static/landing.css` | 1115 | hardcoded-hex | `#f87171` | color: #f87171; |
| `prompt_matrix/static/landing.css` | 1133 | hardcoded-rgb/rgba | `rgba(16, 185, 129, 0.15)` | background: rgba(16, 185, 129, 0.15); |
| `prompt_matrix/static/landing.css` | 1138 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.15)` | background: rgba(59, 130, 246, 0.15); |
| `prompt_matrix/static/landing.css` | 1149 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.02)` | background: rgba(255, 255, 255, 0.02); |
| `prompt_matrix/static/landing.css` | 1387 | hardcoded-hex | `#ef4444` | color: #ef4444; |
| `prompt_matrix/static/landing.css` | 1398 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.02)` | background: rgba(255, 255, 255, 0.02); |
| `prompt_matrix/static/landing.css` | 1414 | hardcoded-hex | `#a78bfa` | border-left: 3px solid #a78bfa; |
| `prompt_matrix/static/landing.css` | 1469 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.15)` | background: rgba(59, 130, 246, 0.15); |
| `prompt_matrix/static/landing.css` | 1474 | hardcoded-rgb/rgba | `rgba(16, 185, 129, 0.15)` | background: rgba(16, 185, 129, 0.15); |
| `prompt_matrix/static/landing.css` | 1663 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 1665 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.35)` | box-shadow: 0 4px 12px rgba(26, 75, 140, 0.35); |
| `prompt_matrix/static/landing.css` | 1673 | hardcoded-hex | `#79c0ff` | outline: 2px solid #79C0FF; |
| `prompt_matrix/static/landing.css` | 1683 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.45)` | box-shadow: 0 30px 80px rgba(0, 0, 0, 0.45); |
| `prompt_matrix/static/landing.css` | 1721 | hardcoded-hex | `#f85149` | .lw-dot.red { background: #F85149; } |
| `prompt_matrix/static/landing.css` | 1722 | hardcoded-hex | `#d29922` | .lw-dot.yellow { background: #D29922; } |
| `prompt_matrix/static/landing.css` | 1723 | hardcoded-hex | `#3fb950` | .lw-dot.green { background: #3FB950; } |
| `prompt_matrix/static/landing.css` | 1730 | hardcoded-rgb/rgba | `rgba(46, 125, 50, 0.6)` | box-shadow: 0 0 6px rgba(46, 125, 50, 0.6); |
| `prompt_matrix/static/landing.css` | 1776 | hardcoded-rgb/rgba | `rgba(248, 81, 73, 0.55)` | text-decoration-color: rgba(248, 81, 73, 0.55); |
| `prompt_matrix/static/landing.css` | 1783 | hardcoded-rgb/rgba | `rgba(248, 81, 73, 0.25)` | 0%, 100% { text-decoration-color: rgba(248, 81, 73, 0.25); } |
| `prompt_matrix/static/landing.css` | 1784 | hardcoded-rgb/rgba | `rgba(248, 81, 73, 0.75)` | 50% { text-decoration-color: rgba(248, 81, 73, 0.75); } |
| `prompt_matrix/static/landing.css` | 1793 | hardcoded-hex | `#79c0ff` | background: linear-gradient(90deg, transparent, #79C0FF, transparent); |
| `prompt_matrix/static/landing.css` | 1839 | hardcoded-rgb/rgba | `rgba(248, 81, 73, 0.14)` | background: rgba(248, 81, 73, 0.14); |
| `prompt_matrix/static/landing.css` | 1840 | hardcoded-rgb/rgba | `rgba(248, 81, 73, 0.4)` | border-color: rgba(248, 81, 73, 0.4); |
| `prompt_matrix/static/landing.css` | 1856 | hardcoded-rgb/rgba | `rgba(46, 125, 50, 0.12)` | background: rgba(46, 125, 50, 0.12); |
| `prompt_matrix/static/landing.css` | 1873 | hardcoded-hex | `#f85149` | border: 1px solid #F85149; |
| `prompt_matrix/static/landing.css` | 1875 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.5)` | box-shadow: 0 10px 30px rgba(0, 0, 0, 0.5); |
| `prompt_matrix/static/landing.css` | 1882 | hardcoded-hex | `#f85149` | color: #F85149; |
| `prompt_matrix/static/landing.css` | 1909 | hardcoded-hex | `#f85149` | color: #F85149; |
| `prompt_matrix/static/landing.css` | 1921 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 1932 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 1933 | hardcoded-hex | `#4a8fd4` | background: linear-gradient(90deg, var(--trust-blue), #4a8fd4); |
| `prompt_matrix/static/landing.css` | 1967 | hardcoded-hex | `#79c0ff` | color: #79C0FF; |
| `prompt_matrix/static/landing.css` | 1991 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 2000 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.25)` | box-shadow: 0 2px 8px rgba(26, 75, 140, 0.25); |
| `prompt_matrix/static/landing.css` | 2062 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/landing.css` | 2063 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2068 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/landing.css` | 2069 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2070 | hardcoded-hex | `#e2e8f0` | border-bottom: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2075 | hardcoded-hex | `#475569` | color: #475569; |
| `prompt_matrix/static/landing.css` | 2079 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2084 | hardcoded-hex | `#0a0a0a` | background: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2085 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 2091 | hardcoded-hex | `#1e1e1e` | background: #1e1e1e; |
| `prompt_matrix/static/landing.css` | 2092 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 2096 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/landing.css` | 2097 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2107 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/landing.css` | 2115 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/landing.css` | 2122 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2132 | hardcoded-hex | `#475569` | color: #475569; |
| `prompt_matrix/static/landing.css` | 2169 | hardcoded-rgb/rgba | `rgba(10, 10, 10, 0.12)` | box-shadow: 0 6px 20px rgba(10, 10, 10, 0.12); |
| `prompt_matrix/static/landing.css` | 2180 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2181 | hardcoded-hex | `#0a0a0a` | border: 1px solid #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2188 | hardcoded-hex | `#0a0a0a` | background: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2189 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 2190 | hardcoded-hex | `#0a0a0a` | border-color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2195 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2216 | hardcoded-hex | `#0a0a0a` | background: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2229 | hardcoded-hex | `#475569` | color: #475569; |
| `prompt_matrix/static/landing.css` | 2243 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2244 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/landing.css` | 2245 | hardcoded-hex | `#475569` | color: #475569; |
| `prompt_matrix/static/landing.css` | 2255 | hardcoded-hex | `#0a0a0a` | background: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2256 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 2257 | hardcoded-hex | `#0a0a0a` | border-color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2268 | hardcoded-hex | `#0a0a0a` | background: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2269 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 2302 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/landing.css` | 2303 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2317 | hardcoded-hex | `#e8f5ee` | background: #e8f5ee; |
| `prompt_matrix/static/landing.css` | 2318 | hardcoded-hex | `#0d6832` | color: #0d6832; |
| `prompt_matrix/static/landing.css` | 2327 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2352 | hardcoded-hex | `#f8fafc` | background: #f8fafc; |
| `prompt_matrix/static/landing.css` | 2353 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2362 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/landing.css` | 2371 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2377 | hardcoded-hex | `#0a0a0a` | background: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2378 | hardcoded-hex | `#0a0a0a` | border-color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2383 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 2390 | hardcoded-hex | `#475569` | color: #475569; |
| `prompt_matrix/static/landing.css` | 2411 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2412 | hardcoded-hex | `#0a0a0a` | border-top: 3px solid #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2414 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/landing.css` | 2422 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.08)` | box-shadow: 0 8px 24px rgba(15, 23, 42, 0.08); |
| `prompt_matrix/static/landing.css` | 2430 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2437 | hardcoded-hex | `#475569` | color: #475569; |
| `prompt_matrix/static/landing.css` | 2443 | hardcoded-hex | `#475569` | color: #475569; |
| `prompt_matrix/static/landing.css` | 2456 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/landing.css` | 2457 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2466 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/landing.css` | 2480 | hardcoded-hex | `#475569` | color: #475569; |
| `prompt_matrix/static/landing.css` | 2488 | hardcoded-hex | `#0a0a0a` | background: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2489 | hardcoded-hex | `#0a0a0a` | border-color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2490 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 2495 | hardcoded-hex | `#cbd5e1` | color: #cbd5e1; |
| `prompt_matrix/static/landing.css` | 2499 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 2503 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/landing.css` | 2504 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2523 | hardcoded-rgb/rgba | `rgba(10, 10, 10, 0.45)` | background: rgba(10, 10, 10, 0.45); |
| `prompt_matrix/static/landing.css` | 2529 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/landing.css` | 2530 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2534 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.16)` | box-shadow: 0 16px 48px rgba(15, 23, 42, 0.16); |
| `prompt_matrix/static/landing.css` | 2551 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/landing.css` | 2557 | hardcoded-hex | `#f1f5f9` | background: #f1f5f9; |
| `prompt_matrix/static/landing.css` | 2558 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2577 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2589 | hardcoded-hex | `#0a0a0a` | background: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2590 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/landing.css` | 2597 | hardcoded-hex | `#9a3b12` | color: #9a3b12; |
| `prompt_matrix/static/landing.css` | 2621 | hardcoded-hex | `#dcfce7` | background: #dcfce7; |
| `prompt_matrix/static/landing.css` | 2622 | hardcoded-hex | `#166534` | color: #166534; |
| `prompt_matrix/static/landing.css` | 2651 | hardcoded-hex | `#f8fafc` | background: #f8fafc; |
| `prompt_matrix/static/landing.css` | 2652 | hardcoded-hex | `#475569` | color: #475569; |
| `prompt_matrix/static/landing.css` | 2653 | hardcoded-hex | `#e2e8f0` | border-top: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2662 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2668 | hardcoded-hex | `#334155` | color: #334155; |
| `prompt_matrix/static/landing.css` | 2679 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/landing.css` | 2686 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2695 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2696 | hardcoded-hex | `#f8fafc` | background: #f8fafc; |
| `prompt_matrix/static/landing.css` | 2702 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/landing.css` | 2710 | hardcoded-hex | `#0a0a0a` | color: #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2719 | hardcoded-hex | `#0a0a0a` | outline: 2px solid #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2743 | hardcoded-hex | `#0f172a` | color: #0f172a; |
| `prompt_matrix/static/landing.css` | 2754 | hardcoded-hex | `#475569` | color: #475569; |
| `prompt_matrix/static/landing.css` | 2760 | hardcoded-hex | `#f8fafc` | background: #f8fafc; |
| `prompt_matrix/static/landing.css` | 2761 | hardcoded-hex | `#2563eb` | border-left: 4px solid #2563eb; |
| `prompt_matrix/static/landing.css` | 2765 | hardcoded-hex | `#334155` | color: #334155; |
| `prompt_matrix/static/landing.css` | 2769 | hardcoded-hex | `#f8fafc` | background: #f8fafc; |
| `prompt_matrix/static/landing.css` | 2770 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2789 | hardcoded-hex | `#2563eb` | color: #2563eb; |
| `prompt_matrix/static/landing.css` | 2813 | hardcoded-hex | `#e2e8f0` | border-top: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2827 | hardcoded-hex | `#f8fafc` | background: #f8fafc; |
| `prompt_matrix/static/landing.css` | 2829 | hardcoded-hex | `#e2e8f0` | border-bottom: 2px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2834 | hardcoded-hex | `#e2e8f0` | border-bottom: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2838 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/landing.css` | 2847 | hardcoded-hex | `#334155` | color: #334155; |
| `prompt_matrix/static/landing.css` | 2852 | hardcoded-hex | `#f8fafc` | background: #f8fafc; |
| `prompt_matrix/static/landing.css` | 2853 | hardcoded-hex | `#16a34a` | border-left: 3px solid #16a34a; |
| `prompt_matrix/static/landing.css` | 2862 | hardcoded-hex | `#f1f5f9` | background: #f1f5f9; |
| `prompt_matrix/static/orchestrator.js` | 9 | tailwind-accent-class | `bg-yellow-500/20` | "diff-highlight bg-yellow-500/20 text-yellow-200 border-l-2 border-yellow-500 p-2 my-2 relative group"; |
| `prompt_matrix/static/orchestrator.js` | 9 | tailwind-accent-class | `text-yellow-200` | "diff-highlight bg-yellow-500/20 text-yellow-200 border-l-2 border-yellow-500 p-2 my-2 relative group"; |
| `prompt_matrix/static/orchestrator.js` | 9 | tailwind-accent-class | `border-yellow-500` | "diff-highlight bg-yellow-500/20 text-yellow-200 border-l-2 border-yellow-500 p-2 my-2 relative group"; |
| `prompt_matrix/static/orchestrator.js` | 11 | tailwind-accent-class | `bg-blue-600` | "push-to-main-btn absolute top-1 right-1 opacity-0 group-hover:opacity-100 bg-blue-600 text-white text-xs px-2 |
| `prompt_matrix/static/orchestrator.js` | 15 | allowed-accent-but-not-css-var | `#10b981` | background: "#10b981", |
| `prompt_matrix/static/orchestrator.js` | 16 | hardcoded-hex | `#ffffff` | color: "#ffffff", |
| `prompt_matrix/static/style.css` | 2 | hardcoded-hex | `#1a4b8c` | --trust-blue: #1A4B8C; |
| `prompt_matrix/static/style.css` | 3 | hardcoded-hex | `#2e7d32` | --confidence-green: #2E7D32; |
| `prompt_matrix/static/style.css` | 4 | hardcoded-hex | `#f7f8fa` | --warm-gray: #F7F8FA; |
| `prompt_matrix/static/style.css` | 5 | hardcoded-hex | `#e2e8f0` | --light-gray: #E2E8F0; |
| `prompt_matrix/static/style.css` | 6 | hardcoded-hex | `#2d3748` | --dark-gray: #2D3748; |
| `prompt_matrix/static/style.css` | 7 | hardcoded-hex | `#0d2b45` | --deep-navy: #0D2B45; |
| `prompt_matrix/static/style.css` | 8 | hardcoded-hex | `#d4a843` | --accent-gold: #D4A843; |
| `prompt_matrix/static/style.css` | 9 | hardcoded-hex | `#ffffff` | --white: #FFFFFF; |
| `prompt_matrix/static/style.css` | 16 | hardcoded-hex | `#e8a838` | --assure-amber: #E8A838; |
| `prompt_matrix/static/style.css` | 51 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.06)` | --shadow-sm: 0 1px 3px rgba(0, 0, 0, 0.06); |
| `prompt_matrix/static/style.css` | 52 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.08)` | --shadow-md: 0 4px 16px rgba(0, 0, 0, 0.08); |
| `prompt_matrix/static/style.css` | 53 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.10)` | --shadow-lg: 0 8px 32px rgba(0, 0, 0, 0.10); |
| `prompt_matrix/static/style.css` | 55 | hardcoded-hex | `#f9fafb` | --bg-color: #f9fafb; |
| `prompt_matrix/static/style.css` | 56 | hardcoded-hex | `#ffffff` | --surface: #ffffff; |
| `prompt_matrix/static/style.css` | 57 | hardcoded-hex | `#e5e7eb` | --border: #e5e7eb; |
| `prompt_matrix/static/style.css` | 58 | hardcoded-hex | `#0a0a0a` | --primary: #0a0a0a; |
| `prompt_matrix/static/style.css` | 59 | hardcoded-hex | `#f0fdf4` | --success-bg: #f0fdf4; |
| `prompt_matrix/static/style.css` | 60 | hardcoded-hex | `#166534` | --success-text: #166534; |
| `prompt_matrix/static/style.css` | 64 | hardcoded-hex | `#16a34a` | --color-success: #16A34A; |
| `prompt_matrix/static/style.css` | 68 | hardcoded-hex | `#111827` | --text-color: #111827; |
| `prompt_matrix/static/style.css` | 75 | hardcoded-hex | `#6b7280` | --muted: #6b7280; |
| `prompt_matrix/static/style.css` | 79 | hardcoded-hex | `#b48a2e` | --brass-dim: #b48a2e; |
| `prompt_matrix/static/style.css` | 82 | hardcoded-hex | `#9a6b12` | --bad: #9a6b12; |
| `prompt_matrix/static/style.css` | 84 | hardcoded-hex | `#f3f4f6` | --select-tint: #f3f4f6; |
| `prompt_matrix/static/style.css` | 87 | hardcoded-hex | `#111827` | --color-primary-hover: #111827; |
| `prompt_matrix/static/style.css` | 88 | hardcoded-hex | `#f3f4f6` | --color-primary-light: #f3f4f6; |
| `prompt_matrix/static/style.css` | 93 | hardcoded-hex | `#6b7280` | --color-text-muted: #6b7280; |
| `prompt_matrix/static/style.css` | 95 | hardcoded-hex | `#dc2626` | --color-danger: #DC2626; |
| `prompt_matrix/static/style.css` | 96 | hardcoded-hex | `#fee2e2` | --color-danger-bg: #FEE2E2; |
| `prompt_matrix/static/style.css` | 97 | allowed-accent-but-not-css-var | `#f59e0b` | --color-warning: #f59e0b; |
| `prompt_matrix/static/style.css` | 98 | hardcoded-hex | `#fef3c7` | --color-warning-bg: #FEF3C7; |
| `prompt_matrix/static/style.css` | 100 | hardcoded-hex | `#ef4444` | --color-destructive: #ef4444; |
| `prompt_matrix/static/style.css` | 131 | hardcoded-rgb/rgba | `rgba(10, 10, 10, 0.18)` | --wb-focus-ring: 0 0 0 3px rgba(10, 10, 10, 0.18); |
| `prompt_matrix/static/style.css` | 132 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.04)` | --wb-elevate: 0 1px 2px rgba(0, 0, 0, 0.04), 0 2px 8px rgba(0, 0, 0, 0.04); |
| `prompt_matrix/static/style.css` | 133 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.06)` | --wb-elevate-hover: 0 2px 4px rgba(0, 0, 0, 0.06), 0 6px 18px rgba(0, 0, 0, 0.06); |
| `prompt_matrix/static/style.css` | 188 | hardcoded-hex | `#ffffff` | .text-white { color: #fff; } |
| `prompt_matrix/static/style.css` | 253 | hardcoded-hex | `#0a0a0a` | background: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 254 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 260 | hardcoded-hex | `#111827` | background: var(--color-primary-hover, #111827); |
| `prompt_matrix/static/style.css` | 268 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 273 | hardcoded-hex | `#1e5a22` | background: #1e5a22; |
| `prompt_matrix/static/style.css` | 291 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.35)` | border: 2px solid rgba(255, 255, 255, 0.35); |
| `prompt_matrix/static/style.css` | 292 | hardcoded-hex | `#ffffff` | border-top-color: #fff; |
| `prompt_matrix/static/style.css` | 306 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 307 | hardcoded-hex | `#e5e7eb` | border-color: var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 349 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 378 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 380 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 387 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.12)` | box-shadow: 0 2px 8px rgba(26, 75, 140, 0.12); |
| `prompt_matrix/static/style.css` | 390 | hardcoded-hex | `#f7f8fa` | background: var(--assure-warm-gray, #f7f8fa); |
| `prompt_matrix/static/style.css` | 397 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 405 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.25)` | box-shadow: 0 4px 12px rgba(26, 75, 140, 0.25); |
| `prompt_matrix/static/style.css` | 430 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.45)` | background: rgba(13, 43, 69, 0.45); |
| `prompt_matrix/static/style.css` | 458 | hardcoded-hex | `#718096` | color: var(--text-muted, #718096); |
| `prompt_matrix/static/style.css` | 500 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.15)` | box-shadow: 0 0 0 3px rgba(26, 75, 140, 0.15); |
| `prompt_matrix/static/style.css` | 661 | hardcoded-hex | `#c5d0db` | color: #c5d0db; |
| `prompt_matrix/static/style.css` | 762 | hardcoded-hex | `#fbf6ea` | background: #fbf6ea; |
| `prompt_matrix/static/style.css` | 772 | hardcoded-hex | `#f4f7f2` | background: #f4f7f2; |
| `prompt_matrix/static/style.css` | 781 | hardcoded-hex | `#eef4fb` | background: #eef4fb; |
| `prompt_matrix/static/style.css` | 813 | hardcoded-hex | `#c5d4e6` | border-color: #c5d4e6; |
| `prompt_matrix/static/style.css` | 827 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 916 | hardcoded-hex | `#eef6ee` | background: #eef6ee; |
| `prompt_matrix/static/style.css` | 973 | hardcoded-hex | `#f4faf4` | background: #f4faf4; |
| `prompt_matrix/static/style.css` | 1099 | hardcoded-hex | `#a5d6a7` | border-color: #a5d6a7; |
| `prompt_matrix/static/style.css` | 1103 | hardcoded-hex | `#eef6ee` | background: #eef6ee; |
| `prompt_matrix/static/style.css` | 1169 | hardcoded-hex | `#c5e1c6` | border: 1px solid #c5e1c6; |
| `prompt_matrix/static/style.css` | 1221 | hardcoded-hex | `#e8f5e9` | background: #e8f5e9; |
| `prompt_matrix/static/style.css` | 1223 | hardcoded-hex | `#a5d6a7` | border: 1px solid #a5d6a7; |
| `prompt_matrix/static/style.css` | 1228 | hardcoded-hex | `#eef2f6` | background: #eef2f6; |
| `prompt_matrix/static/style.css` | 1291 | hardcoded-hex | `#a5d6a7` | .pill.on { color: var(--ok); border-color: #a5d6a7; background: #e8f5e9; } |
| `prompt_matrix/static/style.css` | 1291 | hardcoded-hex | `#e8f5e9` | .pill.on { color: var(--ok); border-color: #a5d6a7; background: #e8f5e9; } |
| `prompt_matrix/static/style.css` | 1292 | hardcoded-hex | `#fdf6e3` | .pill.off { color: var(--bad); border-color: var(--warning); background: #fdf6e3; } |
| `prompt_matrix/static/style.css` | 1345 | hardcoded-hex | `#f9fafb` | background: var(--color-bg, #f9fafb); |
| `prompt_matrix/static/style.css` | 1349 | hardcoded-hex | `#ffffff` | background: var(--surface, #ffffff); |
| `prompt_matrix/static/style.css` | 1350 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 1358 | hardcoded-hex | `#e5e7eb` | border-bottom: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 1479 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 1480 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 1528 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 1531 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 1537 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 1544 | hardcoded-hex | `#ffffff` | background: var(--surface, #ffffff); |
| `prompt_matrix/static/style.css` | 1545 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 1546 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 1560 | hardcoded-hex | `#0a0a0a` | border-color: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 1573 | hardcoded-hex | `#94a3b8` | background: #94a3b8; |
| `prompt_matrix/static/style.css` | 1574 | hardcoded-hex | `#e5e7eb` | box-shadow: 0 0 0 2px var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 1579 | hardcoded-hex | `#22c55e` | background: #22c55e; |
| `prompt_matrix/static/style.css` | 1584 | hardcoded-hex | `#ef4444` | background: #ef4444; |
| `prompt_matrix/static/style.css` | 1588 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.25)` | 0%, 100% { box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.25), 0 0 0 0 rgba(34, 197, 94, 0.5); } |
| `prompt_matrix/static/style.css` | 1588 | hardcoded-rgb/rgba | `rgba(34, 197, 94, 0.5)` | 0%, 100% { box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.25), 0 0 0 0 rgba(34, 197, 94, 0.5); } |
| `prompt_matrix/static/style.css` | 1589 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.25)` | 50% { box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.25), 0 0 0 6px rgba(34, 197, 94, 0); } |
| `prompt_matrix/static/style.css` | 1589 | hardcoded-rgb/rgba | `rgba(34, 197, 94, 0)` | 50% { box-shadow: 0 0 0 2px rgba(255, 255, 255, 0.25), 0 0 0 6px rgba(34, 197, 94, 0); } |
| `prompt_matrix/static/style.css` | 1608 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #ffffff); |
| `prompt_matrix/static/style.css` | 1609 | hardcoded-hex | `#e2e8f0` | border-right: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 1624 | hardcoded-hex | `#1e293b` | color: var(--color-text, #1e293b); |
| `prompt_matrix/static/style.css` | 1634 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 1638 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 1639 | hardcoded-hex | `#0a0a0a` | color: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 1640 | hardcoded-hex | `#0a0a0a` | box-shadow: inset 3px 0 0 var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 1681 | hardcoded-hex | `#0a0a0a` | background: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 1682 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 1697 | hardcoded-hex | `#f0fdf4` | background: var(--success-bg, #f0fdf4); |
| `prompt_matrix/static/style.css` | 1698 | hardcoded-hex | `#166534` | color: var(--success-text, #166534); |
| `prompt_matrix/static/style.css` | 1699 | hardcoded-hex | `#bbf7d0` | border-bottom: 1px solid #bbf7d0; |
| `prompt_matrix/static/style.css` | 1706 | hardcoded-hex | `#ecfdf5` | background: #ecfdf5; |
| `prompt_matrix/static/style.css` | 1707 | hardcoded-hex | `#22c55e` | box-shadow: inset 0 -2px 0 #22c55e; |
| `prompt_matrix/static/style.css` | 1711 | hardcoded-hex | `#fffbeb` | background: #fffbeb; |
| `prompt_matrix/static/style.css` | 1712 | allowed-accent-but-not-css-var | `#f59e0b` | box-shadow: inset 0 -2px 0 #f59e0b; |
| `prompt_matrix/static/style.css` | 1717 | hardcoded-hex | `#166534` | color: var(--success-text, #166534); |
| `prompt_matrix/static/style.css` | 1722 | hardcoded-hex | `#166534` | color: var(--success-text, #166534); |
| `prompt_matrix/static/style.css` | 1738 | hardcoded-hex | `#f4f6f9` | background: var(--color-bg, #f4f6f9); |
| `prompt_matrix/static/style.css` | 1815 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.1)` | background: rgba(59, 130, 246, 0.1); |
| `prompt_matrix/static/style.css` | 1816 | allowed-accent-but-not-css-var | `#3b82f6` | color: var(--accent-blue, #3b82f6); |
| `prompt_matrix/static/style.css` | 1825 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #ffffff); |
| `prompt_matrix/static/style.css` | 1865 | hardcoded-hex | `#f8fafc` | background: var(--color-bg, #f8fafc); |
| `prompt_matrix/static/style.css` | 1887 | hardcoded-rgb/rgba | `rgba(46, 125, 50, 0.12)` | background: var(--color-success-bg, rgba(46, 125, 50, 0.12)); |
| `prompt_matrix/static/style.css` | 1888 | hardcoded-hex | `#2e7d32` | color: var(--color-success, #2e7d32); |
| `prompt_matrix/static/style.css` | 1892 | hardcoded-rgb/rgba | `rgba(185, 28, 28, 0.12)` | background: var(--color-danger-bg, rgba(185, 28, 28, 0.12)); |
| `prompt_matrix/static/style.css` | 1893 | hardcoded-hex | `#b91c1c` | color: var(--color-danger, #b91c1c); |
| `prompt_matrix/static/style.css` | 1914 | hardcoded-hex | `#e8f0fe` | background: var(--color-primary-light, #E8F0FE); |
| `prompt_matrix/static/style.css` | 1936 | allowed-accent-but-not-css-var | `#3b82f6` | background: var(--accent-blue, #3b82f6); |
| `prompt_matrix/static/style.css` | 2023 | hardcoded-hex | `#f8fafc` | background: var(--color-bg, var(--bg, #f8fafc)); |
| `prompt_matrix/static/style.css` | 2044 | allowed-accent-but-not-css-var | `#3b82f6` | color: var(--accent-blue, #3b82f6); |
| `prompt_matrix/static/style.css` | 2062 | hardcoded-hex | `#ef4444` | border-left: 3px solid #ef4444; |
| `prompt_matrix/static/style.css` | 2063 | hardcoded-rgb/rgba | `rgba(239, 68, 68, 0.08)` | background: rgba(239, 68, 68, 0.08); |
| `prompt_matrix/static/style.css` | 2091 | hardcoded-hex | `#f1f5f9` | background: var(--color-bg, #f1f5f9); |
| `prompt_matrix/static/style.css` | 2092 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 2101 | hardcoded-hex | `#334155` | color: var(--color-text, #334155); |
| `prompt_matrix/static/style.css` | 2113 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.08)` | background: rgba(26, 75, 140, 0.08); |
| `prompt_matrix/static/style.css` | 2117 | hardcoded-hex | `#1a4b8c` | background: var(--color-primary, #1A4B8C); |
| `prompt_matrix/static/style.css` | 2118 | hardcoded-hex | `#ffffff` | color: #ffffff; |
| `prompt_matrix/static/style.css` | 2119 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.12)` | box-shadow: var(--shadow-sm, 0 1px 3px rgba(0, 0, 0, 0.12)); |
| `prompt_matrix/static/style.css` | 2141 | hardcoded-hex | `#e8f0fe` | background: var(--color-primary-light, #E8F0FE); |
| `prompt_matrix/static/style.css` | 2153 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 2199 | hardcoded-hex | `#1a4b8c` | color: var(--color-primary, #1A4B8C); |
| `prompt_matrix/static/style.css` | 2223 | hardcoded-hex | `#1a4b8c` | background: var(--color-primary, #1A4B8C); |
| `prompt_matrix/static/style.css` | 2224 | hardcoded-hex | `#1a4b8c` | border-color: var(--color-primary, #1A4B8C); |
| `prompt_matrix/static/style.css` | 2317 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #fff); |
| `prompt_matrix/static/style.css` | 2320 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.12)` | box-shadow: var(--shadow-md, 0 4px 12px rgba(0, 0, 0, 0.12)); |
| `prompt_matrix/static/style.css` | 2334 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.08)` | background: rgba(26, 75, 140, 0.08); |
| `prompt_matrix/static/style.css` | 2420 | hardcoded-hex | `#64748b` | color: var(--color-muted, #64748b); |
| `prompt_matrix/static/style.css` | 2439 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #ffffff); |
| `prompt_matrix/static/style.css` | 2440 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 2441 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.12)` | box-shadow: var(--shadow-md, 0 4px 12px rgba(0, 0, 0, 0.12)); |
| `prompt_matrix/static/style.css` | 2455 | hardcoded-hex | `#22c55e` | border-left: 4px solid #22c55e; |
| `prompt_matrix/static/style.css` | 2459 | hardcoded-hex | `#ef4444` | border-left: 4px solid #ef4444; |
| `prompt_matrix/static/style.css` | 2463 | hardcoded-hex | `#1a4b8c` | border-left: 4px solid var(--color-primary, #1A4B8C); |
| `prompt_matrix/static/style.css` | 2519 | hardcoded-hex | `#ffffff` | color: #ffffff; |
| `prompt_matrix/static/style.css` | 2572 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.18)` | box-shadow: 0 0 0 4px rgba(26, 75, 140, 0.18); |
| `prompt_matrix/static/style.css` | 2583 | hardcoded-hex | `#ffffff` | color: #ffffff; |
| `prompt_matrix/static/style.css` | 2604 | hardcoded-hex | `#ffffff` | color: #ffffff; |
| `prompt_matrix/static/style.css` | 2626 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.45)` | background: rgba(13, 43, 69, 0.45); |
| `prompt_matrix/static/style.css` | 2687 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.12)` | box-shadow: var(--shadow-md, 0 4px 12px rgba(0, 0, 0, 0.12)); |
| `prompt_matrix/static/style.css` | 2772 | hardcoded-hex | `#ffffff` | background: var(--surface, #ffffff); |
| `prompt_matrix/static/style.css` | 2773 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 2781 | hardcoded-hex | `#e5e7eb` | border-bottom: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 2811 | hardcoded-hex | `#ffffff` | background: var(--surface, #ffffff); |
| `prompt_matrix/static/style.css` | 2812 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 2813 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 2825 | hardcoded-hex | `#0a0a0a` | border-color: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 2837 | hardcoded-hex | `#f8fbff` | background: linear-gradient(180deg, #f8fbff 0%, var(--color-surface) 100%); |
| `prompt_matrix/static/style.css` | 2884 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 2885 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--border-subtle, #e2e8f0); |
| `prompt_matrix/static/style.css` | 2887 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.04)` | box-shadow: 0 1px 2px rgba(0, 0, 0, 0.04); |
| `prompt_matrix/static/style.css` | 2906 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 2917 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 2925 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 2939 | hardcoded-hex | `#ffffff` | background: var(--surface, #ffffff); |
| `prompt_matrix/static/style.css` | 2940 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 2946 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 2950 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 2954 | hardcoded-hex | `#0a0a0a` | outline: 2px solid var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 2975 | hardcoded-hex | `#9ca3af` | color: #9ca3af; |
| `prompt_matrix/static/style.css` | 3000 | hardcoded-hex | `#fffbeb` | background: #fffbeb; |
| `prompt_matrix/static/style.css` | 3001 | allowed-accent-but-not-css-var | `#f59e0b` | border-bottom: 1px solid #f59e0b; |
| `prompt_matrix/static/style.css` | 3002 | hardcoded-hex | `#92400e` | color: #92400e; |
| `prompt_matrix/static/style.css` | 3040 | hardcoded-hex | `#92400e` | color: #92400e; |
| `prompt_matrix/static/style.css` | 3058 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 3062 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #ffffff); |
| `prompt_matrix/static/style.css` | 3069 | hardcoded-hex | `#1a4b8c` | color: #1a4b8c; |
| `prompt_matrix/static/style.css` | 3082 | hardcoded-hex | `#1a4b8c` | color: #1a4b8c; |
| `prompt_matrix/static/style.css` | 3094 | hardcoded-hex | `#2d3748` | color: #2d3748; |
| `prompt_matrix/static/style.css` | 3109 | hardcoded-hex | `#e2e8f0` | border-bottom: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 3110 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #ffffff); |
| `prompt_matrix/static/style.css` | 3119 | hardcoded-hex | `#1a4b8c` | color: #1a4b8c; |
| `prompt_matrix/static/style.css` | 3128 | hardcoded-hex | `#2d3748` | color: #2d3748; |
| `prompt_matrix/static/style.css` | 3136 | tailwind-accent-class | `bg-green-200` | .bg-green-200 { |
| `prompt_matrix/static/style.css` | 3137 | hardcoded-hex | `#bbf7d0` | background-color: #bbf7d0; |
| `prompt_matrix/static/style.css` | 3140 | tailwind-accent-class | `bg-yellow-200` | .bg-yellow-200 { |
| `prompt_matrix/static/style.css` | 3141 | hardcoded-hex | `#fefcbf` | background-color: #fefcbf; |
| `prompt_matrix/static/style.css` | 3144 | tailwind-accent-class | `bg-red-200` | .bg-red-200 { |
| `prompt_matrix/static/style.css` | 3145 | hardcoded-hex | `#fecaca` | background-color: #fecaca; |
| `prompt_matrix/static/style.css` | 3148 | tailwind-accent-class | `bg-green-200` | .jdf-tree.confidence-overlay-off .bg-green-200, |
| `prompt_matrix/static/style.css` | 3149 | tailwind-accent-class | `bg-yellow-200` | .jdf-tree.confidence-overlay-off .bg-yellow-200, |
| `prompt_matrix/static/style.css` | 3150 | tailwind-accent-class | `bg-red-200` | .jdf-tree.confidence-overlay-off .bg-red-200 { |
| `prompt_matrix/static/style.css` | 3170 | hardcoded-hex | `#9ca3af` | background: #9CA3AF; |
| `prompt_matrix/static/style.css` | 3175 | hardcoded-hex | `#58a6ff` | background: #58A6FF; |
| `prompt_matrix/static/style.css` | 3180 | hardcoded-hex | `#3fb950` | background: #3FB950; |
| `prompt_matrix/static/style.css` | 3184 | hardcoded-hex | `#d29922` | background: #D29922; |
| `prompt_matrix/static/style.css` | 3188 | hardcoded-hex | `#f85149` | background: #F85149; |
| `prompt_matrix/static/style.css` | 3193 | hardcoded-hex | `#58a6ff88` | 50%       { opacity: 1;    box-shadow: 0 0 7px #58A6FF88; } |
| `prompt_matrix/static/style.css` | 3217 | hardcoded-rgb/rgba | `rgba(47, 129, 247, 0.55)` | outline: 2px solid rgba(47, 129, 247, 0.55); |
| `prompt_matrix/static/style.css` | 3218 | hardcoded-rgb/rgba | `rgba(47, 129, 247, 0.1)` | box-shadow: 0 0 0 5px rgba(47, 129, 247, 0.1); |
| `prompt_matrix/static/style.css` | 3222 | hardcoded-rgb/rgba | `rgba(47, 129, 247, 0.85)` | 0%   { outline: 2px solid rgba(47, 129, 247, 0.85); box-shadow: 0 0 0 7px rgba(47, 129, 247, 0.22); } |
| `prompt_matrix/static/style.css` | 3222 | hardcoded-rgb/rgba | `rgba(47, 129, 247, 0.22)` | 0%   { outline: 2px solid rgba(47, 129, 247, 0.85); box-shadow: 0 0 0 7px rgba(47, 129, 247, 0.22); } |
| `prompt_matrix/static/style.css` | 3239 | hardcoded-rgb/rgba | `rgba(47, 129, 247, 0.07)` | background: rgba(47, 129, 247, 0.07); |
| `prompt_matrix/static/style.css` | 3243 | hardcoded-rgb/rgba | `rgba(47, 129, 247, 0.13)` | background: rgba(47, 129, 247, 0.13); |
| `prompt_matrix/static/style.css` | 3254 | hardcoded-hex | `#2f81f7` | color: var(--color-primary, #2F81F7); |
| `prompt_matrix/static/style.css` | 3268 | hardcoded-hex | `#92400e` | color: #92400e; |
| `prompt_matrix/static/style.css` | 3308 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.06)` | background: rgba(0, 0, 0, 0.06); |
| `prompt_matrix/static/style.css` | 3343 | hardcoded-hex | `#e2e8f0` | border-top: 1px solid var(--color-border, #E2E8F0); |
| `prompt_matrix/static/style.css` | 3350 | hardcoded-hex | `#2d3748` | color: var(--color-text, #2D3748); |
| `prompt_matrix/static/style.css` | 3364 | hardcoded-hex | `#e2e8f0` | border-bottom: 1px solid var(--color-border, #E2E8F0); |
| `prompt_matrix/static/style.css` | 3369 | hardcoded-hex | `#64748b` | color: var(--muted, #64748b); |
| `prompt_matrix/static/style.css` | 3374 | hardcoded-hex | `#64748b` | color: var(--muted, #64748b); |
| `prompt_matrix/static/style.css` | 3500 | hardcoded-hex | `#0d2b45` | color: #0D2B45; |
| `prompt_matrix/static/style.css` | 3540 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--color-border, #E2E8F0); |
| `prompt_matrix/static/style.css` | 3542 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #fff); |
| `prompt_matrix/static/style.css` | 3582 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--color-border, #E2E8F0); |
| `prompt_matrix/static/style.css` | 3584 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #fff); |
| `prompt_matrix/static/style.css` | 3609 | hardcoded-hex | `#c2410c` | border: 1px solid #c2410c; |
| `prompt_matrix/static/style.css` | 3610 | hardcoded-hex | `#ffedd5` | background: #ffedd5; |
| `prompt_matrix/static/style.css` | 3611 | hardcoded-hex | `#9a3412` | color: #9a3412; |
| `prompt_matrix/static/style.css` | 3674 | allowed-accent-but-not-css-var | `#3b82f6` | outline: 2px solid var(--accent-blue, #3b82f6); |
| `prompt_matrix/static/style.css` | 3675 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.25)` | box-shadow: 0 0 0 3px rgba(59, 130, 246, 0.25); |
| `prompt_matrix/static/style.css` | 3693 | hardcoded-hex | `#cbd5e1` | border-color: #CBD5E1; |
| `prompt_matrix/static/style.css` | 3699 | hardcoded-hex | `#fffdf5` | background: #FFFDF5 !important; |
| `prompt_matrix/static/style.css` | 3721 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.15)` | box-shadow: 0 0 0 1px rgba(26, 75, 140, 0.15); |
| `prompt_matrix/static/style.css` | 3788 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.45)` | 0% { transform: scale(1); box-shadow: 0 0 0 0 rgba(26, 75, 140, 0.45); } |
| `prompt_matrix/static/style.css` | 3789 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0)` | 45% { transform: scale(1.04); box-shadow: 0 0 0 8px rgba(26, 75, 140, 0); } |
| `prompt_matrix/static/style.css` | 3790 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0)` | 100% { transform: scale(1); box-shadow: 0 0 0 0 rgba(26, 75, 140, 0); } |
| `prompt_matrix/static/style.css` | 3794 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.15)` | box-shadow: 0 0 0 3px rgba(26, 75, 140, 0.15); |
| `prompt_matrix/static/style.css` | 3907 | hardcoded-hex | `#ffffff` | color: #FFFFFF; |
| `prompt_matrix/static/style.css` | 3922 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.15)` | box-shadow: 0 0 0 4px rgba(26, 75, 140, 0.15); |
| `prompt_matrix/static/style.css` | 3931 | hardcoded-hex | `#ffffff` | color: #FFFFFF; |
| `prompt_matrix/static/style.css` | 3942 | hardcoded-hex | `#f8fafc` | background-color: #F8FAFC !important; |
| `prompt_matrix/static/style.css` | 4029 | hardcoded-hex | `#ffffff` | color: #FFFFFF; |
| `prompt_matrix/static/style.css` | 4110 | hardcoded-hex | `#fff8e1` | background: #fff8e1; |
| `prompt_matrix/static/style.css` | 4111 | allowed-accent-but-not-css-var | `#f59e0b` | color: #f59e0b; |
| `prompt_matrix/static/style.css` | 4112 | hardcoded-hex | `#ffe082` | border-color: #ffe082; |
| `prompt_matrix/static/style.css` | 4121 | hardcoded-hex | `#166534` | color: var(--success-text, #166534); |
| `prompt_matrix/static/style.css` | 4122 | hardcoded-hex | `#f0fdf4` | background: var(--success-bg, #f0fdf4); |
| `prompt_matrix/static/style.css` | 4123 | hardcoded-hex | `#bbf7d0` | border-color: #bbf7d0; |
| `prompt_matrix/static/style.css` | 4125 | hardcoded-hex | `#fcd34d` | .pill-saving { background: var(--color-warning-bg); color: var(--color-warning); border-color: #fcd34d; } |
| `prompt_matrix/static/style.css` | 4126 | hardcoded-hex | `#86efac` | .pill-saved { background: var(--color-success-bg); color: var(--color-success); border-color: #86efac; } |
| `prompt_matrix/static/style.css` | 4127 | hardcoded-hex | `#fca5a5` | .pill-error { background: var(--color-danger-bg); color: var(--color-danger); border-color: #fca5a5; } |
| `prompt_matrix/static/style.css` | 4146 | hardcoded-hex | `#fff8e1` | background: #fff8e1; |
| `prompt_matrix/static/style.css` | 4147 | allowed-accent-but-not-css-var | `#f59e0b` | color: #f59e0b; |
| `prompt_matrix/static/style.css` | 4148 | hardcoded-hex | `#ffe082` | border-color: #ffe082; |
| `prompt_matrix/static/style.css` | 4236 | hardcoded-hex | `#1a4b8c` | border: 1px solid var(--color-primary, #1a4b8c); |
| `prompt_matrix/static/style.css` | 4237 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.08)` | background: rgba(26, 75, 140, 0.08); |
| `prompt_matrix/static/style.css` | 4238 | hardcoded-hex | `#1a4b8c` | color: var(--color-primary, #1a4b8c); |
| `prompt_matrix/static/style.css` | 4244 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.16)` | background: rgba(26, 75, 140, 0.16); |
| `prompt_matrix/static/style.css` | 4249 | hardcoded-hex | `#1a4b8c` | border-left: 3px solid var(--color-primary, #1a4b8c); |
| `prompt_matrix/static/style.css` | 4272 | hardcoded-hex | `#1a4b8c` | border: 1px solid var(--color-primary, #1a4b8c); |
| `prompt_matrix/static/style.css` | 4273 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.08)` | background: rgba(26, 75, 140, 0.08); |
| `prompt_matrix/static/style.css` | 4274 | hardcoded-hex | `#1a4b8c` | color: var(--color-primary, #1a4b8c); |
| `prompt_matrix/static/style.css` | 4282 | hardcoded-hex | `#86efac` | border-color: #86efac; |
| `prompt_matrix/static/style.css` | 4288 | hardcoded-hex | `#fca5a5` | border-color: #fca5a5; |
| `prompt_matrix/static/style.css` | 4292 | hardcoded-rgb/rgba | `rgba(245, 158, 11, 0.12)` | background: rgba(245, 158, 11, 0.12); |
| `prompt_matrix/static/style.css` | 4293 | hardcoded-hex | `#d97706` | color: #d97706; |
| `prompt_matrix/static/style.css` | 4294 | hardcoded-rgb/rgba | `rgba(245, 158, 11, 0.35)` | border-color: rgba(245, 158, 11, 0.35); |
| `prompt_matrix/static/style.css` | 4306 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.08)` | background: rgba(59, 130, 246, 0.08); |
| `prompt_matrix/static/style.css` | 4307 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.25)` | border: 1px solid rgba(59, 130, 246, 0.25); |
| `prompt_matrix/static/style.css` | 4328 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.35)` | background: rgba(13, 43, 69, 0.35); |
| `prompt_matrix/static/style.css` | 4337 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #fff); |
| `prompt_matrix/static/style.css` | 4342 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.08)` | box-shadow: -8px 0 24px rgba(0, 0, 0, 0.08); |
| `prompt_matrix/static/style.css` | 4432 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.6)` | background: linear-gradient(90deg, transparent, rgba(255, 255, 255, 0.6), transparent); |
| `prompt_matrix/static/style.css` | 4440 | hardcoded-hex | `#ffffff` | .btn-primary { background: var(--color-primary); color: #FFFFFF; } |
| `prompt_matrix/static/style.css` | 4444 | hardcoded-hex | `#ffffff` | .btn-success { background: var(--color-success); color: #FFFFFF; } |
| `prompt_matrix/static/style.css` | 4445 | hardcoded-hex | `#ffffff` | .btn-danger { background: var(--color-danger); color: #FFFFFF; } |
| `prompt_matrix/static/style.css` | 4472 | hardcoded-hex | `#fdf3e0` | .diff-del { background: #fdf3e0; color: #8a5a10; } |
| `prompt_matrix/static/style.css` | 4472 | hardcoded-hex | `#8a5a10` | .diff-del { background: #fdf3e0; color: #8a5a10; } |
| `prompt_matrix/static/style.css` | 4473 | hardcoded-hex | `#e8f5e9` | .diff-add { background: #e8f5e9; color: #1b5e20; } |
| `prompt_matrix/static/style.css` | 4473 | hardcoded-hex | `#1b5e20` | .diff-add { background: #e8f5e9; color: #1b5e20; } |
| `prompt_matrix/static/style.css` | 4481 | hardcoded-hex | `#c5d0db` | color: #c5d0db; |
| `prompt_matrix/static/style.css` | 4491 | hardcoded-hex | `#e8eef4` | color: #e8eef4; |
| `prompt_matrix/static/style.css` | 4496 | hardcoded-hex | `#ffffff` | .site-footer strong { color: #fff; } |
| `prompt_matrix/static/style.css` | 4534 | hardcoded-hex | `#a5d6a7` | .pick.connected { border-color: #a5d6a7; } |
| `prompt_matrix/static/style.css` | 4560 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 4563 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #ffffff); |
| `prompt_matrix/static/style.css` | 4572 | hardcoded-hex | `#1a4b8c` | color: #1a4b8c; |
| `prompt_matrix/static/style.css` | 4579 | hardcoded-hex | `#2d3748` | color: #2d3748; |
| `prompt_matrix/static/style.css` | 4586 | hardcoded-hex | `#e8f0fe` | background: #e8f0fe; |
| `prompt_matrix/static/style.css` | 4587 | hardcoded-hex | `#1a4b8c` | color: #1a4b8c; |
| `prompt_matrix/static/style.css` | 4605 | hardcoded-hex | `#1a4b8c` | color: #1a4b8c; |
| `prompt_matrix/static/style.css` | 4690 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.35)` | .work-dialog::backdrop { background: rgba(13, 43, 69, 0.35); } |
| `prompt_matrix/static/style.css` | 4722 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.1)` | background: rgba(59, 130, 246, 0.1); |
| `prompt_matrix/static/style.css` | 4723 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.3)` | border: 1px solid rgba(59, 130, 246, 0.3); |
| `prompt_matrix/static/style.css` | 4730 | hardcoded-hex | `#60a5fa` | color: var(--accent, #60a5fa); |
| `prompt_matrix/static/style.css` | 4808 | hardcoded-hex | `#fbf6ea` | background: #fbf6ea; |
| `prompt_matrix/static/style.css` | 4938 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.45)` | background: rgba(13, 43, 69, 0.45); |
| `prompt_matrix/static/style.css` | 4947 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.2)` | box-shadow: 0 12px 40px rgba(13, 43, 69, 0.2); |
| `prompt_matrix/static/style.css` | 5180 | hardcoded-hex | `#fdf3e0` | background: #fdf3e0; |
| `prompt_matrix/static/style.css` | 5181 | hardcoded-hex | `#8a5a10` | color: #8a5a10; |
| `prompt_matrix/static/style.css` | 5182 | hardcoded-hex | `#e8c48a` | border-color: #e8c48a; |
| `prompt_matrix/static/style.css` | 5312 | hardcoded-hex | `#e8f5e9` | background-color: #E8F5E9; |
| `prompt_matrix/static/style.css` | 5315 | hardcoded-hex | `#a5d6a7` | border-bottom: 1px solid #A5D6A7; |
| `prompt_matrix/static/style.css` | 5319 | hardcoded-hex | `#fff9c4` | background-color: #FFF9C4; |
| `prompt_matrix/static/style.css` | 5322 | hardcoded-hex | `#fdd835` | border-bottom: 1px solid #FDD835; |
| `prompt_matrix/static/style.css` | 5351 | hardcoded-hex | `#e8f5e9` | background: #E8F5E9; |
| `prompt_matrix/static/style.css` | 5352 | hardcoded-hex | `#a5d6a7` | border: 1px solid #A5D6A7; |
| `prompt_matrix/static/style.css` | 5356 | hardcoded-hex | `#fff9c4` | background: #FFF9C4; |
| `prompt_matrix/static/style.css` | 5357 | hardcoded-hex | `#fdd835` | border: 1px solid #FDD835; |
| `prompt_matrix/static/style.css` | 5624 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 5625 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 5626 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.04)` | box-shadow: 0 1px 2px rgba(15, 23, 42, 0.04); |
| `prompt_matrix/static/style.css` | 5631 | hardcoded-hex | `#93c5fd` | border-color: #93c5fd; |
| `prompt_matrix/static/style.css` | 5632 | hardcoded-rgb/rgba | `rgba(29, 78, 216, 0.08)` | box-shadow: 0 4px 12px rgba(29, 78, 216, 0.08); |
| `prompt_matrix/static/style.css` | 5636 | hardcoded-hex | `#1d4ed8` | border-color: #1d4ed8; |
| `prompt_matrix/static/style.css` | 5637 | hardcoded-rgb/rgba | `rgba(29, 78, 216, 0.15)` | box-shadow: 0 0 0 1px rgba(29, 78, 216, 0.15); |
| `prompt_matrix/static/style.css` | 5660 | hardcoded-hex | `#f1f5f9` | background: #f1f5f9; |
| `prompt_matrix/static/style.css` | 5661 | hardcoded-hex | `#475569` | color: #475569; |
| `prompt_matrix/static/style.css` | 5666 | hardcoded-hex | `#fef3c7` | background: #fef3c7; |
| `prompt_matrix/static/style.css` | 5667 | hardcoded-hex | `#b45309` | color: #b45309; |
| `prompt_matrix/static/style.css` | 5672 | hardcoded-hex | `#ffedd5` | background: #ffedd5; |
| `prompt_matrix/static/style.css` | 5673 | hardcoded-hex | `#c2410c` | color: #c2410c; |
| `prompt_matrix/static/style.css` | 5678 | hardcoded-hex | `#dcfce7` | background: #dcfce7; |
| `prompt_matrix/static/style.css` | 5679 | hardcoded-hex | `#15803d` | color: #15803d; |
| `prompt_matrix/static/style.css` | 5685 | hardcoded-hex | `#1d4ed8` | color: #1d4ed8; |
| `prompt_matrix/static/style.css` | 5702 | hardcoded-hex | `#0f172a` | color: #0f172a; |
| `prompt_matrix/static/style.css` | 5714 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/style.css` | 5718 | hardcoded-hex | `#1d4ed8` | color: #1d4ed8; |
| `prompt_matrix/static/style.css` | 5722 | hardcoded-hex | `#c2410c` | color: #c2410c; |
| `prompt_matrix/static/style.css` | 5726 | hardcoded-hex | `#15803d` | color: #15803d; |
| `prompt_matrix/static/style.css` | 5730 | hardcoded-hex | `#94a3b8` | color: #94a3b8; |
| `prompt_matrix/static/style.css` | 5739 | hardcoded-hex | `#f1f5f9` | border-top: 1px solid #f1f5f9; |
| `prompt_matrix/static/style.css` | 5837 | hardcoded-rgb/rgba | `rgba(0,0,0,0.06)` | background: var(--color-surface-hover, rgba(0,0,0,0.06)); |
| `prompt_matrix/static/style.css` | 5856 | hardcoded-hex | `#f5c6cb` | border: 1px solid #f5c6cb; |
| `prompt_matrix/static/style.css` | 5857 | hardcoded-hex | `#fff5f5` | background: #fff5f5; |
| `prompt_matrix/static/style.css` | 5858 | hardcoded-hex | `#7f1d1d` | color: #7f1d1d; |
| `prompt_matrix/static/style.css` | 5869 | hardcoded-hex | `#ffb74d` | border-color: #ffb74d; |
| `prompt_matrix/static/style.css` | 5870 | hardcoded-hex | `#e65100` | color: #e65100; |
| `prompt_matrix/static/style.css` | 5871 | hardcoded-hex | `#fff3e0` | background: #fff3e0; |
| `prompt_matrix/static/style.css` | 5888 | hardcoded-hex | `#90caf9` | border-color: var(--color-primary-light, #90caf9); |
| `prompt_matrix/static/style.css` | 5893 | hardcoded-hex | `#ffe082` | border-color: #ffe082; |
| `prompt_matrix/static/style.css` | 5894 | hardcoded-hex | `#8d6e00` | color: #8d6e00; |
| `prompt_matrix/static/style.css` | 5898 | hardcoded-hex | `#a5d6a7` | border-color: #a5d6a7; |
| `prompt_matrix/static/style.css` | 5899 | hardcoded-hex | `#2e7d32` | color: #2e7d32; |
| `prompt_matrix/static/style.css` | 5968 | hardcoded-hex | `#eff6ff` | background: #eff6ff; |
| `prompt_matrix/static/style.css` | 5969 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.16)` | border-bottom: 1px solid rgba(26, 75, 140, 0.16); |
| `prompt_matrix/static/style.css` | 6005 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.72)` | background: rgba(13, 43, 69, 0.72); |
| `prompt_matrix/static/style.css` | 6049 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.55)` | background: rgba(13, 43, 69, 0.55); |
| `prompt_matrix/static/style.css` | 6059 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 6060 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.18)` | box-shadow: 0 16px 48px rgba(13, 43, 69, 0.18); |
| `prompt_matrix/static/style.css` | 6072 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.1)` | background: rgba(26, 75, 140, 0.1); |
| `prompt_matrix/static/style.css` | 6080 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.18)` | border: 1px solid rgba(26, 75, 140, 0.18); |
| `prompt_matrix/static/style.css` | 6164 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.22)` | border-color: rgba(26, 75, 140, 0.22); |
| `prompt_matrix/static/style.css` | 6173 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 6174 | hardcoded-rgb/rgba | `rgba(46, 125, 50, 0.5)` | box-shadow: 0 0 0 0 rgba(46, 125, 50, 0.5); |
| `prompt_matrix/static/style.css` | 6180 | hardcoded-hex | `#256c29` | background: #256c29; |
| `prompt_matrix/static/style.css` | 6181 | hardcoded-hex | `#256c29` | border-color: #256c29; |
| `prompt_matrix/static/style.css` | 6182 | hardcoded-rgb/rgba | `rgba(46, 125, 50, 0.18)` | box-shadow: 0 0 0 4px rgba(46, 125, 50, 0.18); |
| `prompt_matrix/static/style.css` | 6186 | hardcoded-rgb/rgba | `rgba(46, 125, 50, 0.55)` | from { box-shadow: 0 0 0 0 rgba(46, 125, 50, 0.55); transform: scale(0.97); } |
| `prompt_matrix/static/style.css` | 6187 | hardcoded-rgb/rgba | `rgba(46, 125, 50, 0)` | to   { box-shadow: 0 0 0 8px rgba(46, 125, 50, 0); transform: scale(1); } |
| `prompt_matrix/static/style.css` | 6194 | hardcoded-hex | `#991b1b` | color: #991b1b; |
| `prompt_matrix/static/style.css` | 6485 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.05)` | box-shadow: inset 0 -1px 0 rgba(13, 43, 69, 0.05); |
| `prompt_matrix/static/style.css` | 6515 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.5)` | background: rgba(13, 43, 69, 0.5); |
| `prompt_matrix/static/style.css` | 6593 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 6595 | hardcoded-hex | `#ffffff` | background: var(--surface, #fff); |
| `prompt_matrix/static/style.css` | 6596 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 6598 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.05)` | box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05); |
| `prompt_matrix/static/style.css` | 6606 | hardcoded-hex | `#0a0a0a` | color: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 6607 | hardcoded-hex | `#e5e7eb` | border-color: var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 6608 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 6789 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.35)` | 0% { box-shadow: 0 0 0 0 rgba(26, 75, 140, 0.35); } |
| `prompt_matrix/static/style.css` | 6832 | hardcoded-hex | `#e2e8f0` | border-top: 1px solid var(--border-subtle, #e2e8f0); |
| `prompt_matrix/static/style.css` | 6907 | hardcoded-hex | `#94a3b8` | .spine-dot-unverified { background: #94a3b8; } |
| `prompt_matrix/static/style.css` | 6934 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--border-subtle, #e2e8f0); |
| `prompt_matrix/static/style.css` | 6936 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 6959 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 6960 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 6968 | allowed-accent-but-not-css-var | `#3b82f6` | border-color: #3b82f6; |
| `prompt_matrix/static/style.css` | 6969 | hardcoded-hex | `#eff6ff` | background: #eff6ff; |
| `prompt_matrix/static/style.css` | 6973 | hardcoded-hex | `#1d4ed8` | border-color: #1d4ed8; |
| `prompt_matrix/static/style.css` | 6974 | hardcoded-hex | `#eff6ff` | background: #eff6ff; |
| `prompt_matrix/static/style.css` | 6979 | hardcoded-hex | `#0369a1` | border-color: #0369a1; |
| `prompt_matrix/static/style.css` | 6980 | hardcoded-rgb/rgba | `rgba(3, 105, 161, 0.2)` | box-shadow: 0 0 0 2px rgba(3, 105, 161, 0.2); |
| `prompt_matrix/static/style.css` | 6985 | hardcoded-hex | `#94a3b8` | color: #94a3b8; |
| `prompt_matrix/static/style.css` | 7001 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/style.css` | 7018 | hardcoded-hex | `#e2e8f0` | border-top: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 7022 | hardcoded-hex | `#1d4ed8` | background: #1d4ed8; |
| `prompt_matrix/static/style.css` | 7023 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 7037 | hardcoded-hex | `#1e40af` | background: #1e40af; |
| `prompt_matrix/static/style.css` | 7041 | hardcoded-hex | `#cbd5e1` | background: #cbd5e1; |
| `prompt_matrix/static/style.css` | 7046 | hardcoded-hex | `#ef4444` | background: #ef4444; |
| `prompt_matrix/static/style.css` | 7050 | hardcoded-hex | `#dc2626` | background: #dc2626; |
| `prompt_matrix/static/style.css` | 7056 | hardcoded-hex | `#e0f2fe` | background: #e0f2fe; |
| `prompt_matrix/static/style.css` | 7057 | hardcoded-hex | `#0369a1` | color: #0369a1; |
| `prompt_matrix/static/style.css` | 7065 | hardcoded-hex | `#dcfce7` | background: #dcfce7; |
| `prompt_matrix/static/style.css` | 7066 | hardcoded-hex | `#15803d` | color: #15803d; |
| `prompt_matrix/static/style.css` | 7085 | hardcoded-hex | `#1d4ed8` | border-left-color: #1d4ed8 !important; |
| `prompt_matrix/static/style.css` | 7086 | hardcoded-hex | `#eff6ff` | background: #eff6ff; |
| `prompt_matrix/static/style.css` | 7114 | hardcoded-hex | `#b45309` | color: var(--color-warning, #b45309); |
| `prompt_matrix/static/style.css` | 7115 | hardcoded-rgb/rgba | `rgba(251, 191, 36, 0.12)` | background: rgba(251, 191, 36, 0.12); |
| `prompt_matrix/static/style.css` | 7127 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #fff); |
| `prompt_matrix/static/style.css` | 7128 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7131 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.12)` | box-shadow: 0 8px 24px rgba(0, 0, 0, 0.12); |
| `prompt_matrix/static/style.css` | 7151 | hardcoded-hex | `#64748b` | color: var(--color-muted, #64748b); |
| `prompt_matrix/static/style.css` | 7157 | hardcoded-hex | `#2563eb` | accent-color: var(--color-primary, #2563eb); |
| `prompt_matrix/static/style.css` | 7163 | hardcoded-hex | `#0f172a` | color: var(--color-text, #0f172a); |
| `prompt_matrix/static/style.css` | 7180 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7186 | hardcoded-hex | `#64748b` | color: var(--color-muted, #64748b); |
| `prompt_matrix/static/style.css` | 7205 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.45)` | background: rgba(15, 23, 42, 0.45); |
| `prompt_matrix/static/style.css` | 7214 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #fff); |
| `prompt_matrix/static/style.css` | 7216 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.18)` | box-shadow: 0 12px 40px rgba(15, 23, 42, 0.18); |
| `prompt_matrix/static/style.css` | 7241 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7248 | hardcoded-hex | `#2563eb` | border-color: var(--color-primary, #2563eb); |
| `prompt_matrix/static/style.css` | 7249 | hardcoded-rgb/rgba | `rgba(37, 99, 235, 0.06)` | background: rgba(37, 99, 235, 0.06); |
| `prompt_matrix/static/style.css` | 7255 | hardcoded-hex | `#64748b` | color: var(--color-muted, #64748b); |
| `prompt_matrix/static/style.css` | 7260 | hardcoded-hex | `#64748b` | color: var(--color-muted, #64748b); |
| `prompt_matrix/static/style.css` | 7292 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.45)` | background: rgba(15, 23, 42, 0.45); |
| `prompt_matrix/static/style.css` | 7311 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7314 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #fff); |
| `prompt_matrix/static/style.css` | 7321 | hardcoded-hex | `#2563eb` | border-color: var(--color-primary, #2563eb); |
| `prompt_matrix/static/style.css` | 7322 | hardcoded-rgb/rgba | `rgba(37, 99, 235, 0.12)` | box-shadow: 0 4px 14px rgba(37, 99, 235, 0.12); |
| `prompt_matrix/static/style.css` | 7327 | hardcoded-hex | `#cbd5e1` | border: 2px dashed var(--color-border, #cbd5e1); |
| `prompt_matrix/static/style.css` | 7335 | hardcoded-hex | `#2563eb` | border-color: var(--color-primary, #2563eb); |
| `prompt_matrix/static/style.css` | 7336 | hardcoded-rgb/rgba | `rgba(37, 99, 235, 0.05)` | background: rgba(37, 99, 235, 0.05); |
| `prompt_matrix/static/style.css` | 7366 | hardcoded-hex | `#ffffff` | background: var(--color-surface, #fff); |
| `prompt_matrix/static/style.css` | 7367 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7368 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.12)` | box-shadow: 0 8px 24px rgba(15, 23, 42, 0.12); |
| `prompt_matrix/static/style.css` | 7416 | hardcoded-hex | `#ffffff` | background: var(--surface, #fff); |
| `prompt_matrix/static/style.css` | 7417 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 7418 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 7419 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.05)` | box-shadow: 0 2px 4px rgba(0, 0, 0, 0.05); |
| `prompt_matrix/static/style.css` | 7440 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 7456 | hardcoded-hex | `#0f172a` | background: #0f172a; |
| `prompt_matrix/static/style.css` | 7457 | hardcoded-hex | `#f8fafc` | color: #f8fafc; |
| `prompt_matrix/static/style.css` | 7513 | hardcoded-rgb/rgba | `rgba(34, 197, 94, 0.18)` | background: rgba(34, 197, 94, 0.18); |
| `prompt_matrix/static/style.css` | 7523 | hardcoded-rgb/rgba | `rgba(234, 179, 8, 0.2)` | background: rgba(234, 179, 8, 0.2); |
| `prompt_matrix/static/style.css` | 7549 | hardcoded-rgb/rgba | `rgba(46, 125, 50, 0.15)` | rgba(46, 125, 50, 0.15) 8%, |
| `prompt_matrix/static/style.css` | 7550 | hardcoded-rgb/rgba | `rgba(46, 125, 50, 0.95)` | rgba(46, 125, 50, 0.95) 55%, |
| `prompt_matrix/static/style.css` | 7551 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.95)` | rgba(26, 75, 140, 0.95) 92%, |
| `prompt_matrix/static/style.css` | 7555 | hardcoded-rgb/rgba | `rgba(46, 125, 50, 0.65)` | 0 0 12px rgba(46, 125, 50, 0.65), |
| `prompt_matrix/static/style.css` | 7556 | hardcoded-rgb/rgba | `rgba(26, 75, 140, 0.35)` | 0 0 28px rgba(26, 75, 140, 0.35); |
| `prompt_matrix/static/style.css` | 7604 | hardcoded-hex | `#2e7d32` | background: var(--confidence-green, #2e7d32); |
| `prompt_matrix/static/style.css` | 7605 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 7645 | hardcoded-hex | `#1a4b8c` | border: 2px solid var(--trust-blue, #1a4b8c); |
| `prompt_matrix/static/style.css` | 7647 | hardcoded-hex | `#1a4b8c` | color: var(--trust-blue, #1a4b8c); |
| `prompt_matrix/static/style.css` | 7653 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.92)` | background: rgba(255, 255, 255, 0.92); |
| `prompt_matrix/static/style.css` | 7659 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.12)` | box-shadow: 0 2px 8px rgba(13, 43, 69, 0.12); |
| `prompt_matrix/static/style.css` | 7663 | hardcoded-hex | `#2e7d32` | border-color: var(--confidence-green, #2e7d32); |
| `prompt_matrix/static/style.css` | 7664 | hardcoded-hex | `#2e7d32` | color: var(--confidence-green, #2e7d32); |
| `prompt_matrix/static/style.css` | 7682 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.92)` | background: rgba(13, 43, 69, 0.92); |
| `prompt_matrix/static/style.css` | 7685 | hardcoded-hex | `#f7f8fa` | color: #f7f8fa; |
| `prompt_matrix/static/style.css` | 7701 | hardcoded-rgb/rgba | `rgba(226, 232, 240, 0.25)` | border: 1px solid rgba(226, 232, 240, 0.25); |
| `prompt_matrix/static/style.css` | 7704 | hardcoded-hex | `#0d2b45` | background: #0d2b45; |
| `prompt_matrix/static/style.css` | 7719 | hardcoded-rgb/rgba | `rgba(220, 38, 38, 0.08)` | background: rgba(220, 38, 38, 0.08); |
| `prompt_matrix/static/style.css` | 7720 | hardcoded-hex | `#fecaca` | color: #fecaca; |
| `prompt_matrix/static/style.css` | 7725 | hardcoded-rgb/rgba | `rgba(46, 125, 50, 0.08)` | background: rgba(46, 125, 50, 0.08); |
| `prompt_matrix/static/style.css` | 7726 | hardcoded-hex | `#bbf7d0` | color: #bbf7d0; |
| `prompt_matrix/static/style.css` | 7746 | hardcoded-hex | `#d4a843` | accent-color: var(--accent-gold, #d4a843); |
| `prompt_matrix/static/style.css` | 7754 | hardcoded-hex | `#f7f8fa` | color: #f7f8fa; |
| `prompt_matrix/static/style.css` | 7764 | hardcoded-hex | `#f7f8fa` | background: var(--light-gray, #f7f8fa); |
| `prompt_matrix/static/style.css` | 7765 | hardcoded-hex | `#e2e8f0` | border-left: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7766 | hardcoded-rgb/rgba | `rgba(13, 43, 69, 0.12)` | box-shadow: -8px 0 32px rgba(13, 43, 69, 0.12); |
| `prompt_matrix/static/style.css` | 7776 | hardcoded-hex | `#e2e8f0` | border-bottom: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7777 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 7788 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 7801 | hardcoded-hex | `#e2e8f0` | border-bottom: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7805 | hardcoded-hex | `#1a4b8c` | color: var(--trust-blue, #1a4b8c); |
| `prompt_matrix/static/style.css` | 7809 | hardcoded-hex | `#1a4b8c` | border-color: var(--trust-blue, #1a4b8c); |
| `prompt_matrix/static/style.css` | 7810 | hardcoded-hex | `#1a4b8c` | color: var(--trust-blue, #1a4b8c); |
| `prompt_matrix/static/style.css` | 7821 | hardcoded-hex | `#ffffff` | background: var(--surface-card, #ffffff); |
| `prompt_matrix/static/style.css` | 7822 | hardcoded-hex | `#e2e8f0` | border-left: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7823 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.1)` | box-shadow: -4px 0 25px rgba(0, 0, 0, 0.1); |
| `prompt_matrix/static/style.css` | 7841 | hardcoded-hex | `#e2e8f0` | border-bottom: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7842 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 7863 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7865 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 7866 | hardcoded-hex | `#1a4b8c` | color: var(--trust-blue, #1a4b8c); |
| `prompt_matrix/static/style.css` | 7880 | hardcoded-hex | `#f9fafb` | background: #f9fafb; |
| `prompt_matrix/static/style.css` | 7881 | hardcoded-hex | `#0a0a0a` | border-left: 3px solid var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 7888 | hardcoded-hex | `#0f172a` | background: #0f172a; |
| `prompt_matrix/static/style.css` | 7889 | hardcoded-hex | `#e2e8f0` | color: #e2e8f0; |
| `prompt_matrix/static/style.css` | 7898 | hardcoded-hex | `#e2e8f0` | background: #e2e8f0; |
| `prompt_matrix/static/style.css` | 7907 | hardcoded-hex | `#1a4b8c` | background: var(--trust-blue, #1a4b8c); |
| `prompt_matrix/static/style.css` | 7911 | hardcoded-hex | `#ca8a04` | background: #ca8a04; |
| `prompt_matrix/static/style.css` | 7915 | hardcoded-hex | `#dc2626` | background: #dc2626; |
| `prompt_matrix/static/style.css` | 7931 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/style.css` | 7944 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7956 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7958 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 7969 | hardcoded-hex | `#ffffff` | background: var(--surface-card, #ffffff); |
| `prompt_matrix/static/style.css` | 7970 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--border-color, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7974 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.02)` | box-shadow: 0 1px 3px rgba(0, 0, 0, 0.02); |
| `prompt_matrix/static/style.css` | 7989 | hardcoded-hex | `#64748b` | color: var(--text-muted, #64748b); |
| `prompt_matrix/static/style.css` | 7995 | hardcoded-hex | `#64748b` | color: var(--text-muted, #64748b); |
| `prompt_matrix/static/style.css` | 8003 | hardcoded-hex | `#0a0a0a` | color: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 8015 | hardcoded-hex | `#ffffff` | background: var(--surface, #ffffff); |
| `prompt_matrix/static/style.css` | 8016 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8034 | hardcoded-hex | `#e5e7eb` | background: var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8039 | hardcoded-hex | `#0a0a0a` | background: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 8043 | hardcoded-hex | `#0a0a0a` | background: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 8080 | hardcoded-hex | `#0a0a0a` | color: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 8089 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 8094 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 8101 | hardcoded-hex | `#ffffff` | background: #ffffff; |
| `prompt_matrix/static/style.css` | 8102 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 8103 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8114 | hardcoded-hex | `#0a0a0a` | background: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 8115 | hardcoded-hex | `#ffffff` | color: #ffffff; |
| `prompt_matrix/static/style.css` | 8116 | hardcoded-hex | `#0a0a0a` | border-color: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 8120 | hardcoded-hex | `#0a0a0a` | background: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 8121 | hardcoded-hex | `#ffffff` | color: #ffffff; |
| `prompt_matrix/static/style.css` | 8122 | hardcoded-hex | `#0a0a0a` | border-color: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 8177 | hardcoded-hex | `#0a0a0a` | color: var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 8197 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/style.css` | 8232 | hardcoded-hex | `#ffffff` | background: var(--surface, #ffffff); |
| `prompt_matrix/static/style.css` | 8233 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8245 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 8254 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 8278 | hardcoded-hex | `#ffffff` | background: var(--surface, #ffffff); |
| `prompt_matrix/static/style.css` | 8279 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8287 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.03)` | box-shadow: 0 1px 2px rgba(0, 0, 0, 0.03); |
| `prompt_matrix/static/style.css` | 8303 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 8309 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8311 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 8312 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 8337 | hardcoded-hex | `#f9fafb` | background: #f9fafb; |
| `prompt_matrix/static/style.css` | 8338 | hardcoded-hex | `#e5e7eb` | border-bottom: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8339 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 8348 | hardcoded-hex | `#e5e7eb` | border-bottom: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8349 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 8357 | hardcoded-hex | `#fafafa` | background: #fafafa; |
| `prompt_matrix/static/style.css` | 8361 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 8385 | hardcoded-hex | `#e2e8f0` | border-bottom: 1px solid var(--border-color, #e2e8f0); |
| `prompt_matrix/static/style.css` | 8435 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--border-subtle, #e2e8f0); |
| `prompt_matrix/static/style.css` | 8437 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 8444 | hardcoded-hex | `#e2e8f0` | border-top: 1px solid var(--border-subtle, #e2e8f0); |
| `prompt_matrix/static/style.css` | 8456 | hardcoded-hex | `#e2e8f0` | border-bottom: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 8472 | hardcoded-hex | `#e2e8f0` | border-bottom: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 8476 | hardcoded-hex | `#e8f0fe` | background: var(--color-primary-light, #e8f0fe); |
| `prompt_matrix/static/style.css` | 8501 | hardcoded-hex | `#5c6b7a` | color: var(--muted, #5c6b7a); |
| `prompt_matrix/static/style.css` | 8530 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.4)` | background: rgba(0, 0, 0, 0.4); |
| `prompt_matrix/static/style.css` | 8542 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 8543 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8547 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.1)` | box-shadow: 0 10px 30px rgba(0, 0, 0, 0.1); |
| `prompt_matrix/static/style.css` | 8555 | hardcoded-hex | `#e5e7eb` | border-bottom: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8558 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 8559 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 8579 | hardcoded-hex | `#111827` | color: var(--text-main, #111827); |
| `prompt_matrix/static/style.css` | 8585 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 8594 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 8601 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 8623 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8624 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 8629 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 8633 | hardcoded-hex | `#111827` | background: #111827; |
| `prompt_matrix/static/style.css` | 8634 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 8635 | hardcoded-hex | `#111827` | border-color: #111827; |
| `prompt_matrix/static/style.css` | 8649 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 8650 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8652 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.08)` | box-shadow: 0 8px 24px rgba(0, 0, 0, 0.08); |
| `prompt_matrix/static/style.css` | 8668 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 8672 | hardcoded-hex | `#b91c1c` | color: #b91c1c; |
| `prompt_matrix/static/style.css` | 8720 | hardcoded-hex | `#64748b` | color: var(--text-muted, #64748b); |
| `prompt_matrix/static/style.css` | 8735 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--border-subtle, #e2e8f0); |
| `prompt_matrix/static/style.css` | 8736 | hardcoded-hex | `#ffffff` | background: var(--surface-elevated, #fff); |
| `prompt_matrix/static/style.css` | 8762 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.4)` | background: rgba(0, 0, 0, 0.4); |
| `prompt_matrix/static/style.css` | 8767 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 8772 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8783 | hardcoded-hex | `#b91c1c` | background: #b91c1c; |
| `prompt_matrix/static/style.css` | 8784 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 8789 | hardcoded-hex | `#111827` | outline: 2px solid #111827; |
| `prompt_matrix/static/style.css` | 8808 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border-subtle, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8811 | hardcoded-hex | `#ffffff` | background: var(--surface-raised, #fff); |
| `prompt_matrix/static/style.css` | 8855 | hardcoded-hex | `#e5e7eb` | border-bottom: 1px solid var(--border-color, var(--border-subtle, #e5e7eb)); |
| `prompt_matrix/static/style.css` | 8862 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 8876 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border-subtle, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8879 | hardcoded-hex | `#ffffff` | background: var(--surface-raised, #fff); |
| `prompt_matrix/static/style.css` | 8897 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 8898 | hardcoded-hex | `#374151` | color: #374151; |
| `prompt_matrix/static/style.css` | 8902 | hardcoded-hex | `#ecfdf5` | background: #ecfdf5; |
| `prompt_matrix/static/style.css` | 8903 | hardcoded-hex | `#047857` | color: #047857; |
| `prompt_matrix/static/style.css` | 8907 | hardcoded-hex | `#fef2f2` | background: #fef2f2; |
| `prompt_matrix/static/style.css` | 8908 | hardcoded-hex | `#b91c1c` | color: #b91c1c; |
| `prompt_matrix/static/style.css` | 8920 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 8931 | hardcoded-hex | `#0f172a` | background: #0f172a; |
| `prompt_matrix/static/style.css` | 8932 | hardcoded-hex | `#0f172a` | border-color: #0f172a; |
| `prompt_matrix/static/style.css` | 8933 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 8937 | hardcoded-hex | `#1e293b` | background: #1e293b; |
| `prompt_matrix/static/style.css` | 8938 | hardcoded-hex | `#1e293b` | border-color: #1e293b; |
| `prompt_matrix/static/style.css` | 8943 | hardcoded-hex | `#cbd5e1` | border-color: var(--border-subtle, #cbd5e1); |
| `prompt_matrix/static/style.css` | 8944 | hardcoded-hex | `#334155` | color: var(--text-primary, #334155); |
| `prompt_matrix/static/style.css` | 8948 | hardcoded-hex | `#f87171` | border-color: #f87171; |
| `prompt_matrix/static/style.css` | 8949 | hardcoded-hex | `#b91c1c` | color: #b91c1c; |
| `prompt_matrix/static/style.css` | 8954 | hardcoded-hex | `#94a3b8` | color: var(--text-muted, #94a3b8); |
| `prompt_matrix/static/style.css` | 8959 | hardcoded-hex | `#b91c1c` | color: #b91c1c; |
| `prompt_matrix/static/style.css` | 8970 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.35)` | background: rgba(15, 23, 42, 0.35); |
| `prompt_matrix/static/style.css` | 8981 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.95)` | background: rgba(255, 255, 255, 0.95); |
| `prompt_matrix/static/style.css` | 8983 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.25)` | box-shadow: 0 25px 50px -12px rgba(0, 0, 0, 0.25); |
| `prompt_matrix/static/style.css` | 8985 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 9011 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 9017 | allowed-accent-but-not-css-var | `#3b82f6` | outline: 2px solid #3b82f6; |
| `prompt_matrix/static/style.css` | 9023 | hardcoded-hex | `#cbd5e1` | border: 1px dashed var(--border-subtle, #cbd5e1); |
| `prompt_matrix/static/style.css` | 9027 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 9033 | hardcoded-hex | `#2563eb` | border-color: #2563eb; |
| `prompt_matrix/static/style.css` | 9034 | hardcoded-hex | `#eff6ff` | background: #eff6ff; |
| `prompt_matrix/static/style.css` | 9051 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.35)` | background: rgba(0, 0, 0, 0.35); |
| `prompt_matrix/static/style.css` | 9064 | hardcoded-hex | `#1e293b` | background: #1e293b; |
| `prompt_matrix/static/style.css` | 9066 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.5)` | box-shadow: 0 20px 60px rgba(0, 0, 0, 0.5); |
| `prompt_matrix/static/style.css` | 9067 | hardcoded-hex | `#334155` | border: 1px solid #334155; |
| `prompt_matrix/static/style.css` | 9095 | hardcoded-rgb/rgba | `rgba(56, 189, 248, 0.65)` | border-color: rgba(56, 189, 248, 0.65); |
| `prompt_matrix/static/style.css` | 9097 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.5)` | 0 20px 60px rgba(0, 0, 0, 0.5), |
| `prompt_matrix/static/style.css` | 9098 | hardcoded-rgb/rgba | `rgba(56, 189, 248, 0.35)` | 0 0 0 1px rgba(56, 189, 248, 0.35), |
| `prompt_matrix/static/style.css` | 9099 | hardcoded-rgb/rgba | `rgba(56, 189, 248, 0.18)` | 0 0 18px rgba(56, 189, 248, 0.18); |
| `prompt_matrix/static/style.css` | 9108 | hardcoded-rgb/rgba | `rgba(51, 65, 85, 0.5)` | border-bottom: 1px solid rgba(51, 65, 85, 0.5); |
| `prompt_matrix/static/style.css` | 9115 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.2)` | background: rgba(59, 130, 246, 0.2); |
| `prompt_matrix/static/style.css` | 9116 | hardcoded-hex | `#60a5fa` | color: #60a5fa; |
| `prompt_matrix/static/style.css` | 9126 | hardcoded-hex | `#f8fafc` | color: #f8fafc; |
| `prompt_matrix/static/style.css` | 9133 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/style.css` | 9137 | allowed-accent-but-not-css-var | `#3b82f6` | background: #3b82f6; |
| `prompt_matrix/static/style.css` | 9138 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 9142 | hardcoded-hex | `#cbd5e1` | color: #cbd5e1; |
| `prompt_matrix/static/style.css` | 9150 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/style.css` | 9151 | hardcoded-rgb/rgba | `rgba(30, 41, 59, 0.5)` | background: rgba(30, 41, 59, 0.5); |
| `prompt_matrix/static/style.css` | 9154 | hardcoded-hex | `#334155` | border: 1px solid #334155; |
| `prompt_matrix/static/style.css` | 9166 | hardcoded-rgb/rgba | `rgba(30, 41, 59, 0.5)` | background: rgba(30, 41, 59, 0.5); |
| `prompt_matrix/static/style.css` | 9176 | hardcoded-hex | `#93c5fd` | color: #93c5fd; |
| `prompt_matrix/static/style.css` | 9182 | hardcoded-hex | `#60a5fa` | color: #60a5fa; |
| `prompt_matrix/static/style.css` | 9190 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.5)` | 0 20px 60px rgba(0, 0, 0, 0.5), |
| `prompt_matrix/static/style.css` | 9191 | hardcoded-rgb/rgba | `rgba(56, 189, 248, 0.35)` | 0 0 0 1px rgba(56, 189, 248, 0.35), |
| `prompt_matrix/static/style.css` | 9192 | hardcoded-rgb/rgba | `rgba(56, 189, 248, 0.12)` | 0 0 14px rgba(56, 189, 248, 0.12); |
| `prompt_matrix/static/style.css` | 9196 | hardcoded-rgb/rgba | `rgba(0, 0, 0, 0.5)` | 0 20px 60px rgba(0, 0, 0, 0.5), |
| `prompt_matrix/static/style.css` | 9197 | hardcoded-rgb/rgba | `rgba(129, 140, 248, 0.55)` | 0 0 0 1px rgba(129, 140, 248, 0.55), |
| `prompt_matrix/static/style.css` | 9198 | hardcoded-rgb/rgba | `rgba(129, 140, 248, 0.28)` | 0 0 22px rgba(129, 140, 248, 0.28); |
| `prompt_matrix/static/style.css` | 9227 | hardcoded-hex | `#ecfdf5` | background: #ecfdf5; |
| `prompt_matrix/static/style.css` | 9228 | hardcoded-hex | `#047857` | color: #047857; |
| `prompt_matrix/static/style.css` | 9229 | hardcoded-hex | `#a7f3d0` | border: 1px solid #a7f3d0; |
| `prompt_matrix/static/style.css` | 9239 | hardcoded-hex | `#64748b` | color: var(--muted, #64748b); |
| `prompt_matrix/static/style.css` | 9250 | hardcoded-hex | `#059669` | color: #059669; |
| `prompt_matrix/static/style.css` | 9256 | hardcoded-rgb/rgba | `rgba(100, 116, 139, 0.25)` | border: 2px solid rgba(100, 116, 139, 0.25); |
| `prompt_matrix/static/style.css` | 9257 | hardcoded-hex | `#2563eb` | border-top-color: var(--accent, #2563eb); |
| `prompt_matrix/static/style.css` | 9294 | hardcoded-hex | `#e2e8f0` | background: linear-gradient(90deg, #e2e8f0 25%, #f1f5f9 50%, #e2e8f0 75%); |
| `prompt_matrix/static/style.css` | 9294 | hardcoded-hex | `#f1f5f9` | background: linear-gradient(90deg, #e2e8f0 25%, #f1f5f9 50%, #e2e8f0 75%); |
| `prompt_matrix/static/style.css` | 9329 | hardcoded-hex | `#64748b` | color: var(--muted, #64748b); |
| `prompt_matrix/static/style.css` | 9334 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 9337 | hardcoded-hex | `#f8fafc` | background: var(--surface, #f8fafc); |
| `prompt_matrix/static/style.css` | 9355 | hardcoded-hex | `#dbeafe` | background: #dbeafe; |
| `prompt_matrix/static/style.css` | 9356 | hardcoded-hex | `#1d4ed8` | color: #1d4ed8; |
| `prompt_matrix/static/style.css` | 9363 | hardcoded-hex | `#334155` | color: var(--text, #334155); |
| `prompt_matrix/static/style.css` | 9380 | hardcoded-hex | `#059669` | background: #059669; |
| `prompt_matrix/static/style.css` | 9381 | hardcoded-hex | `#ffffff` | color: #fff; |
| `prompt_matrix/static/style.css` | 9385 | hardcoded-hex | `#dc2626` | background: #dc2626; |
| `prompt_matrix/static/style.css` | 9393 | hardcoded-hex | `#0f172a` | background: #0f172a; |
| `prompt_matrix/static/style.css` | 9394 | hardcoded-hex | `#e2e8f0` | color: #e2e8f0; |
| `prompt_matrix/static/style.css` | 9408 | hardcoded-hex | `#374151` | color: var(--text-muted, #374151); |
| `prompt_matrix/static/style.css` | 9412 | hardcoded-hex | `#fef3c7` | background: #fef3c7; |
| `prompt_matrix/static/style.css` | 9413 | hardcoded-hex | `#92400e` | color: #92400e; |
| `prompt_matrix/static/style.css` | 9431 | hardcoded-hex | `#fffbeb` | background: #fffbeb; |
| `prompt_matrix/static/style.css` | 9432 | hardcoded-hex | `#fde68a` | border: 1px solid #fde68a; |
| `prompt_matrix/static/style.css` | 9448 | hardcoded-hex | `#ffffff` | background: var(--surface-raised, #fff); |
| `prompt_matrix/static/style.css` | 9449 | hardcoded-hex | `#e5e7eb` | border-left: 1px solid var(--border-subtle, #e5e7eb); |
| `prompt_matrix/static/style.css` | 9450 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.12)` | box-shadow: -8px 0 24px rgba(15, 23, 42, 0.12); |
| `prompt_matrix/static/style.css` | 9471 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border-subtle, #e5e7eb); |
| `prompt_matrix/static/style.css` | 9480 | hardcoded-hex | `#f9fafb` | background: #f9fafb; |
| `prompt_matrix/static/style.css` | 9487 | hardcoded-hex | `#fef08a` | background: #fef08a; |
| `prompt_matrix/static/style.css` | 9548 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--border-subtle, #e2e8f0); |
| `prompt_matrix/static/style.css` | 9551 | hardcoded-hex | `#ffffff` | background: var(--surface-raised, #fff); |
| `prompt_matrix/static/style.css` | 9565 | hardcoded-hex | `#0f172a` | color: var(--text-primary, #0f172a); |
| `prompt_matrix/static/style.css` | 9570 | hardcoded-hex | `#64748b` | color: var(--text-muted, #64748b); |
| `prompt_matrix/static/style.css` | 9577 | hardcoded-hex | `#475569` | color: var(--text-secondary, #475569); |
| `prompt_matrix/static/style.css` | 9598 | hardcoded-hex | `#ffffff` | background: var(--surface-raised, #fff); |
| `prompt_matrix/static/style.css` | 9599 | hardcoded-hex | `#e5e7eb` | border-left: 1px solid var(--border-subtle, #e5e7eb); |
| `prompt_matrix/static/style.css` | 9600 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.12)` | box-shadow: -12px 0 32px rgba(15, 23, 42, 0.12); |
| `prompt_matrix/static/style.css` | 9621 | hardcoded-hex | `#e5e7eb` | border-bottom: 1px solid var(--border-color, var(--border-subtle, #e5e7eb)); |
| `prompt_matrix/static/style.css` | 9634 | hardcoded-hex | `#e5e7eb` | border: 1px solid var(--border-color, var(--border-subtle, #e5e7eb)); |
| `prompt_matrix/static/style.css` | 9636 | hardcoded-hex | `#f8fafc` | background: var(--surface-muted, #f8fafc); |
| `prompt_matrix/static/style.css` | 9660 | hardcoded-hex | `#64748b` | color: var(--text-muted, #64748b); |
| `prompt_matrix/static/style.css` | 9675 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--border-subtle, #e2e8f0); |
| `prompt_matrix/static/style.css` | 9676 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 9692 | hardcoded-hex | `#64748b` | color: var(--text-muted, #64748b); |
| `prompt_matrix/static/style.css` | 9702 | hardcoded-hex | `#fee2e2` | background: #fee2e2; |
| `prompt_matrix/static/style.css` | 9703 | hardcoded-hex | `#991b1b` | color: #991b1b; |
| `prompt_matrix/static/style.css` | 9707 | hardcoded-hex | `#fef3c7` | background: #fef3c7; |
| `prompt_matrix/static/style.css` | 9708 | hardcoded-hex | `#92400e` | color: #92400e; |
| `prompt_matrix/static/style.css` | 9712 | hardcoded-hex | `#dbeafe` | background: #dbeafe; |
| `prompt_matrix/static/style.css` | 9713 | hardcoded-hex | `#1e40af` | color: #1e40af; |
| `prompt_matrix/static/style.css` | 9717 | hardcoded-hex | `#64748b` | color: var(--text-muted, #64748b); |
| `prompt_matrix/static/style.css` | 9724 | hardcoded-hex | `#0f172a` | color: var(--text-primary, #0f172a); |
| `prompt_matrix/static/style.css` | 9736 | hardcoded-hex | `#2563eb` | outline: 2px dashed #2563eb; |
| `prompt_matrix/static/style.css` | 9744 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.35)` | background: rgba(15, 23, 42, 0.35); |
| `prompt_matrix/static/style.css` | 9843 | hardcoded-hex | `#e5e7eb` | border-right: 1px solid var(--border-subtle, #e5e7eb); |
| `prompt_matrix/static/style.css` | 9875 | hardcoded-hex | `#ffffff` | background: #ffffff; |
| `prompt_matrix/static/style.css` | 9876 | hardcoded-hex | `#e2e8f0` | border-left: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 9898 | hardcoded-hex | `#cbd5e1` | border-left: 3px solid var(--border-subtle, #cbd5e1); |
| `prompt_matrix/static/style.css` | 9899 | hardcoded-rgb/rgba | `rgba(241, 245, 249, 0.9)` | background: rgba(241, 245, 249, 0.9); |
| `prompt_matrix/static/style.css` | 9908 | hardcoded-hex | `#0f172a` | background: #0f172a; |
| `prompt_matrix/static/style.css` | 9909 | hardcoded-hex | `#e2e8f0` | color: #e2e8f0; |
| `prompt_matrix/static/style.css` | 9918 | hardcoded-hex | `#e2e8f0` | border: 1px solid var(--border-subtle, #e2e8f0); |
| `prompt_matrix/static/style.css` | 9921 | hardcoded-hex | `#ffffff` | background: var(--surface-raised, #fff); |
| `prompt_matrix/static/style.css` | 9925 | hardcoded-hex | `#fecaca` | border-color: #fecaca; |
| `prompt_matrix/static/style.css` | 9926 | hardcoded-hex | `#fef2f2` | background: #fef2f2; |
| `prompt_matrix/static/style.css` | 9930 | hardcoded-hex | `#fde68a` | border-color: #fde68a; |
| `prompt_matrix/static/style.css` | 9931 | hardcoded-hex | `#fffbeb` | background: #fffbeb; |
| `prompt_matrix/static/style.css` | 9966 | allowed-accent-but-not-css-var | `#3b82f6` | color: #3b82f6; |
| `prompt_matrix/static/style.css` | 9967 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.12)` | background: rgba(59, 130, 246, 0.12); |
| `prompt_matrix/static/style.css` | 9968 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.25)` | border-color: rgba(59, 130, 246, 0.25); |
| `prompt_matrix/static/style.css` | 9972 | hardcoded-hex | `#ef4444` | color: #ef4444; |
| `prompt_matrix/static/style.css` | 9973 | hardcoded-rgb/rgba | `rgba(239, 68, 68, 0.12)` | background: rgba(239, 68, 68, 0.12); |
| `prompt_matrix/static/style.css` | 9974 | hardcoded-rgb/rgba | `rgba(239, 68, 68, 0.25)` | border-color: rgba(239, 68, 68, 0.25); |
| `prompt_matrix/static/style.css` | 9983 | hardcoded-hex | `#22c55e` | color: #22c55e; |
| `prompt_matrix/static/style.css` | 9987 | hardcoded-rgb/rgba | `rgba(239, 68, 68, 0.35)` | border-color: rgba(239, 68, 68, 0.35); |
| `prompt_matrix/static/style.css` | 9988 | hardcoded-rgb/rgba | `rgba(239, 68, 68, 0.06)` | background: rgba(239, 68, 68, 0.06); |
| `prompt_matrix/static/style.css` | 9992 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.25)` | border-color: rgba(59, 130, 246, 0.25); |
| `prompt_matrix/static/style.css` | 9993 | hardcoded-rgb/rgba | `rgba(59, 130, 246, 0.05)` | background: rgba(59, 130, 246, 0.05); |
| `prompt_matrix/static/style.css` | 10003 | hardcoded-hex | `#22c55e` | border-color: #22c55e; |
| `prompt_matrix/static/style.css` | 10004 | hardcoded-hex | `#15803d` | color: #15803d; |
| `prompt_matrix/static/style.css` | 10005 | hardcoded-hex | `#f0fdf4` | background: #f0fdf4; |
| `prompt_matrix/static/style.css` | 10015 | hardcoded-rgb/rgba | `rgba(245, 158, 11, 0.35)` | border: 1px solid rgba(245, 158, 11, 0.35); |
| `prompt_matrix/static/style.css` | 10016 | hardcoded-rgb/rgba | `rgba(245, 158, 11, 0.08)` | background: rgba(245, 158, 11, 0.08); |
| `prompt_matrix/static/style.css` | 10027 | hardcoded-hex | `#d97706` | color: #d97706; |
| `prompt_matrix/static/style.css` | 10034 | allowed-accent-but-not-css-var | `#f59e0b` | background: #f59e0b; |
| `prompt_matrix/static/style.css` | 10049 | hardcoded-rgb/rgba | `rgba(34, 197, 94, 0.2)` | background-color: rgba(34, 197, 94, 0.2); |
| `prompt_matrix/static/style.css` | 10050 | hardcoded-hex | `#15803d` | color: #15803d; |
| `prompt_matrix/static/style.css` | 10052 | hardcoded-hex | `#22c55e` | text-decoration-color: #22c55e; |
| `prompt_matrix/static/style.css` | 10056 | hardcoded-rgb/rgba | `rgba(239, 68, 68, 0.15)` | background-color: rgba(239, 68, 68, 0.15); |
| `prompt_matrix/static/style.css` | 10057 | hardcoded-hex | `#b91c1c` | color: #b91c1c; |
| `prompt_matrix/static/style.css` | 10083 | hardcoded-hex | `#e2e8f0` | border-bottom: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 10094 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 10108 | hardcoded-hex | `#475569` | color: #475569; |
| `prompt_matrix/static/style.css` | 10116 | hardcoded-hex | `#e2e8f0` | border-bottom: 1px solid var(--border-color, var(--border-subtle, #e2e8f0)); |
| `prompt_matrix/static/style.css` | 10146 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/style.css` | 10154 | hardcoded-hex | `#f1f5f9` | background: #f1f5f9; |
| `prompt_matrix/static/style.css` | 10158 | hardcoded-hex | `#eff6ff` | background: #eff6ff; |
| `prompt_matrix/static/style.css` | 10159 | hardcoded-hex | `#1d4ed8` | color: #1d4ed8; |
| `prompt_matrix/static/style.css` | 10160 | hardcoded-hex | `#bfdbfe` | border-color: #bfdbfe; |
| `prompt_matrix/static/style.css` | 10173 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 10175 | hardcoded-hex | `#f8fafc` | background: #f8fafc; |
| `prompt_matrix/static/style.css` | 10181 | allowed-accent-but-not-css-var | `#3b82f6` | box-shadow: 0 0 0 2px #3b82f6; |
| `prompt_matrix/static/style.css` | 10197 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 10198 | hardcoded-hex | `#ffffff` | background: #fff; |
| `prompt_matrix/static/style.css` | 10202 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.12)` | box-shadow: 0 2px 6px rgba(15, 23, 42, 0.12); |
| `prompt_matrix/static/style.css` | 10206 | hardcoded-hex | `#dcfce7` | background: #dcfce7; |
| `prompt_matrix/static/style.css` | 10207 | hardcoded-hex | `#86efac` | border-color: #86efac; |
| `prompt_matrix/static/style.css` | 10208 | hardcoded-hex | `#16a34a` | color: #16a34a; |
| `prompt_matrix/static/style.css` | 10212 | hardcoded-hex | `#fee2e2` | background: #fee2e2; |
| `prompt_matrix/static/style.css` | 10213 | hardcoded-hex | `#fca5a5` | border-color: #fca5a5; |
| `prompt_matrix/static/style.css` | 10214 | hardcoded-hex | `#dc2626` | color: #dc2626; |
| `prompt_matrix/static/style.css` | 10218 | hardcoded-hex | `#fecaca` | background: #fecaca; |
| `prompt_matrix/static/style.css` | 10219 | hardcoded-hex | `#dc2626` | color: #dc2626; |
| `prompt_matrix/static/style.css` | 10229 | hardcoded-hex | `#bbf7d0` | background: #bbf7d0; |
| `prompt_matrix/static/style.css` | 10230 | hardcoded-hex | `#16a34a` | color: #16a34a; |
| `prompt_matrix/static/style.css` | 10300 | hardcoded-hex | `#f8fafc` | background: #f8fafc; |
| `prompt_matrix/static/style.css` | 10301 | hardcoded-hex | `#e2e8f0` | border-left: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 10308 | hardcoded-hex | `#1e293b` | background: #1e293b; |
| `prompt_matrix/static/style.css` | 10323 | hardcoded-hex | `#e2e8f0` | color: #e2e8f0; |
| `prompt_matrix/static/style.css` | 10326 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.08)` | border-right: 1px solid rgba(255, 255, 255, 0.08); |
| `prompt_matrix/static/style.css` | 10339 | hardcoded-hex | `#94a3b8` | color: #94a3b8; |
| `prompt_matrix/static/style.css` | 10344 | hardcoded-hex | `#cbd5e1` | color: #cbd5e1; |
| `prompt_matrix/static/style.css` | 10381 | tailwind-accent-class | `border-yellow-500` | body.founder-workbench:not(.legacy-workbench) .model-pane .border-yellow-500 { |
| `prompt_matrix/static/style.css` | 10382 | allowed-accent-but-not-css-var | `#f59e0b` | border-left-color: #f59e0b; |
| `prompt_matrix/static/style.css` | 10385 | tailwind-accent-class | `bg-yellow-500` | body.founder-workbench:not(.legacy-workbench) .model-pane .bg-yellow-500\/20, |
| `prompt_matrix/static/style.css` | 10386 | tailwind-accent-class | `bg-yellow-500/20` | body.founder-workbench:not(.legacy-workbench) .model-pane .bg-yellow-500/20 { |
| `prompt_matrix/static/style.css` | 10387 | hardcoded-rgb/rgba | `rgba(245, 158, 11, 0.2)` | background: rgba(245, 158, 11, 0.2); |
| `prompt_matrix/static/style.css` | 10390 | tailwind-accent-class | `text-yellow-200` | body.founder-workbench:not(.legacy-workbench) .model-pane .text-yellow-200 { |
| `prompt_matrix/static/style.css` | 10391 | hardcoded-hex | `#fde68a` | color: #fde68a; |
| `prompt_matrix/static/style.css` | 10395 | hardcoded-hex | `#ffffff` | color: #ffffff; |
| `prompt_matrix/static/style.css` | 10417 | tailwind-accent-class | `bg-blue-600` | body.founder-workbench:not(.legacy-workbench) .model-pane .bg-blue-600 { |
| `prompt_matrix/static/style.css` | 10418 | hardcoded-hex | `#2563eb` | background: #2563eb; |
| `prompt_matrix/static/style.css` | 10442 | allowed-accent-but-not-css-var | `#3b82f6` | background: #3b82f6; |
| `prompt_matrix/static/style.css` | 10523 | hardcoded-hex | `#e2e8f0` | border-right: 1px solid var(--border-color, var(--color-border, #e2e8f0)); |
| `prompt_matrix/static/style.css` | 10544 | hardcoded-hex | `#1e293b` | background: #1e293b; |
| `prompt_matrix/static/style.css` | 10545 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.06)` | border-right: 1px solid rgba(255, 255, 255, 0.06); |
| `prompt_matrix/static/style.css` | 10551 | hardcoded-hex | `#1e293b` | background: #1e293b; |
| `prompt_matrix/static/style.css` | 10569 | hardcoded-hex | `#94a3b8` | color: var(--text-muted, #94a3b8); |
| `prompt_matrix/static/style.css` | 10576 | hardcoded-rgb/rgba | `rgba(255, 255, 255, 0.08)` | background: rgba(255, 255, 255, 0.08); |
| `prompt_matrix/static/style.css` | 10577 | hardcoded-hex | `#e2e8f0` | color: var(--text-primary, #e2e8f0); |
| `prompt_matrix/static/style.css` | 10585 | hardcoded-hex | `#334155` | background: #334155; |
| `prompt_matrix/static/style.css` | 10586 | hardcoded-hex | `#f8fafc` | color: #f8fafc; |
| `prompt_matrix/static/style.css` | 10590 | allowed-accent-but-not-css-var | `#3b82f6` | background: #3b82f6; |
| `prompt_matrix/static/style.css` | 10591 | hardcoded-hex | `#ffffff` | color: #ffffff; |
| `prompt_matrix/static/style.css` | 10606 | hardcoded-hex | `#64748b` | background: #64748b; |
| `prompt_matrix/static/style.css` | 10610 | hardcoded-hex | `#22c55e` | background: #22c55e; |
| `prompt_matrix/static/style.css` | 10614 | allowed-accent-but-not-css-var | `#f59e0b` | background: #f59e0b; |
| `prompt_matrix/static/style.css` | 10618 | allowed-accent-but-not-css-var | `#3b82f6` | background: #3b82f6; |
| `prompt_matrix/static/style.css` | 10642 | hardcoded-hex | `#0f172a` | background: #0f172a; |
| `prompt_matrix/static/style.css` | 10643 | hardcoded-hex | `#f8fafc` | color: #f8fafc; |
| `prompt_matrix/static/style.css` | 10651 | hardcoded-rgb/rgba | `rgba(15, 23, 42, 0.25)` | box-shadow: 0 8px 20px rgba(15, 23, 42, 0.25); |
| `prompt_matrix/static/style.css` | 10661 | hardcoded-hex | `#0f172a` | border-right-color: #0f172a; |
| `prompt_matrix/static/style.css` | 10690 | hardcoded-hex | `#6b7280` | color: var(--text-muted, #6b7280); |
| `prompt_matrix/static/style.css` | 10712 | hardcoded-hex | `#64748b` | color: #64748b; |
| `prompt_matrix/static/style.css` | 10765 | hardcoded-hex | `#d1d5db` | border-color: #d1d5db; |
| `prompt_matrix/static/style.css` | 10770 | hardcoded-hex | `#f3f4f6` | background: #f3f4f6; |
| `prompt_matrix/static/style.css` | 10771 | hardcoded-hex | `#4b5563` | color: #4b5563; |
| `prompt_matrix/static/style.css` | 10772 | hardcoded-hex | `#d1d5db` | border: 1px solid #d1d5db; |
| `prompt_matrix/static/style.css` | 10796 | hardcoded-hex | `#1e293b` | background: #1e293b !important; |
| `prompt_matrix/static/style.css` | 10797 | hardcoded-hex | `#f8fafc` | color: #f8fafc; |
| `prompt_matrix/static/style.css` | 10798 | hardcoded-hex | `#e2e8f0` | border: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 10819 | hardcoded-hex | `#94a3b8` | color: #94a3b8; |
| `prompt_matrix/static/style.css` | 10820 | hardcoded-hex | `#334155` | background: #334155; |
| `prompt_matrix/static/wow_effects.js` | 467 | hardcoded-hex | `#1a4b8c` | "background-color": "#1A4B8C", |
| `prompt_matrix/static/wow_effects.js` | 468 | hardcoded-hex | `#ffffff` | color: "#fff", |
| `prompt_matrix/static/wow_effects.js` | 477 | hardcoded-hex | `#2e7d32` | style: { "background-color": "#2E7D32", shape: "diamond", width: 40, height: 40 }, |
| `prompt_matrix/static/wow_effects.js` | 483 | hardcoded-hex | `#94a3b8` | "line-color": "#94A3B8", |
| `prompt_matrix/static/wow_effects.js` | 484 | hardcoded-hex | `#94a3b8` | "target-arrow-color": "#94A3B8", |
| `prompt_matrix/templates/base.html` | 39 | hardcoded-hex | `#1a4b8c` | <rect width="36" height="36" rx="8" fill="#1A4B8C"/> |
| `prompt_matrix/templates/base.html` | 40 | hardcoded-hex | `#ffffff` | <path d="M10 18.5 15.2 23.5 26 12.5" fill="none" stroke="#ffffff" stroke-width="2.6" stroke-linecap="round" st |
| `prompt_matrix/templates/includes/landing_header.html` | 7 | hardcoded-hex | `#1a4b8c` | <rect width="36" height="36" rx="8" fill="#1A4B8C"/> |
| `prompt_matrix/templates/includes/landing_header.html` | 8 | hardcoded-hex | `#ffffff` | <path d="M10 18.5 15.2 23.5 26 12.5" fill="none" stroke="#ffffff" stroke-width="2.6" stroke-linecap="round" st |
| `prompt_matrix/templates/index.html` | 70 | hardcoded-hex | `#ffffff` | <rect width="36" height="36" rx="8" fill="#ffffff" stroke="#e5e7eb" stroke-width="1.5"/> |
| `prompt_matrix/templates/index.html` | 70 | hardcoded-hex | `#e5e7eb` | <rect width="36" height="36" rx="8" fill="#ffffff" stroke="#e5e7eb" stroke-width="1.5"/> |
| `prompt_matrix/templates/index.html` | 71 | hardcoded-hex | `#1a4b8c` | <path d="M10 18.5 15.2 23.5 26 12.5" fill="none" stroke="#1A4B8C" stroke-width="2.6" stroke-linecap="round" st |
| `prompt_matrix/templates/index.html` | 689 | tailwind-accent-class | `bg-green-200` | <span class="bg-green-200" data-i18n="jdf.confidence.verified">{{ gettext('Verified') }}</span> |
| `prompt_matrix/templates/index.html` | 690 | tailwind-accent-class | `bg-yellow-200` | <span class="bg-yellow-200" data-i18n="jdf.confidence.uncertain">{{ gettext('Uncertain') }}</span> |
| `prompt_matrix/templates/index.html` | 691 | tailwind-accent-class | `bg-red-200` | <span class="bg-red-200" data-i18n="jdf.confidence.hallucination">{{ gettext('Hallucination') }}</span> |
| `prompt_matrix/templates/index.html` | 775 | tailwind-accent-class | `bg-yellow-500/20` | <p class="diff-highlight bg-yellow-500/20 text-yellow-200 border-l-2 border-yellow-500 p-2 my-2 relative group |
| `prompt_matrix/templates/index.html` | 775 | tailwind-accent-class | `text-yellow-200` | <p class="diff-highlight bg-yellow-500/20 text-yellow-200 border-l-2 border-yellow-500 p-2 my-2 relative group |
| `prompt_matrix/templates/index.html` | 775 | tailwind-accent-class | `border-yellow-500` | <p class="diff-highlight bg-yellow-500/20 text-yellow-200 border-l-2 border-yellow-500 p-2 my-2 relative group |
| `prompt_matrix/templates/index.html` | 777 | tailwind-accent-class | `bg-blue-600` | <button type="button" class="push-to-main-btn absolute top-1 right-1 opacity-0 group-hover:opacity-100 bg-blue |
| `prompt_matrix/templates/index.html` | 784 | tailwind-accent-class | `bg-yellow-500/20` | <p class="diff-highlight bg-yellow-500/20 text-yellow-200 border-l-2 border-yellow-500 p-2 my-2 relative group |
| `prompt_matrix/templates/index.html` | 784 | tailwind-accent-class | `text-yellow-200` | <p class="diff-highlight bg-yellow-500/20 text-yellow-200 border-l-2 border-yellow-500 p-2 my-2 relative group |
| `prompt_matrix/templates/index.html` | 784 | tailwind-accent-class | `border-yellow-500` | <p class="diff-highlight bg-yellow-500/20 text-yellow-200 border-l-2 border-yellow-500 p-2 my-2 relative group |
| `prompt_matrix/templates/index.html` | 786 | tailwind-accent-class | `bg-blue-600` | <button type="button" class="push-to-main-btn absolute top-1 right-1 opacity-0 group-hover:opacity-100 bg-blue |

#### 2. Spacing violations (outside 4/8/12/16/24/32/48/64 px)

| File | Line | Value | Snippet |
| --- | ---: | --- | --- |
| `prompt_matrix/static/landing.css` | 166 | `1px` | border-bottom: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 536 | `3px` | border-left: 3px solid var(--trust-blue); |
| `prompt_matrix/static/landing.css` | 618 | `3px` | border-left: 3px solid rgba(255, 255, 255, 0.12); |
| `prompt_matrix/static/landing.css` | 622 | `3px` | border-left: 3px solid var(--accent-green); |
| `prompt_matrix/static/landing.css` | 649 | `1px` | border-bottom: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 735 | `1px` | border-top: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 736 | `1px` | border-bottom: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 769 | `1px` | border-bottom: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 829 | `1px` | border-bottom: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 894 | `1px` | border-top: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 913 | `3px` | border-left: 3px solid var(--accent-blue); |
| `prompt_matrix/static/landing.css` | 981 | `1px` | border-bottom: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 1006 | `1px` | border-bottom: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 1040 | `1px` | border-top: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 1150 | `2px` | border-left: 2px solid var(--accent-blue); |
| `prompt_matrix/static/landing.css` | 1172 | `1px` | border-top: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 1188 | `1px` | border-top: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 1314 | `1px` | border-bottom: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 1406 | `3px` | border-left: 3px solid var(--accent-blue); |
| `prompt_matrix/static/landing.css` | 1410 | `3px` | border-left: 3px solid var(--accent-green); |
| `prompt_matrix/static/landing.css` | 1414 | `3px` | border-left: 3px solid #a78bfa; |
| `prompt_matrix/static/landing.css` | 1456 | `1px` | border-left: 1px dashed var(--border-color); |
| `prompt_matrix/static/landing.css` | 1567 | `1px` | border-bottom: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 1580 | `1px` | border-top: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 1691 | `1px` | border-bottom: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 1704 | `6px` | gap: 6px; |
| `prompt_matrix/static/landing.css` | 1748 | `1px` | border-right: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 1849 | `3px` | padding: 3px 8px; |
| `prompt_matrix/static/landing.css` | 1876 | `14px` | padding: 14px 16px 16px; |
| `prompt_matrix/static/landing.css` | 1880 | `10px` | top: calc(100% + 10px); |
| `prompt_matrix/static/landing.css` | 1916 | `14px` | margin: 0 0 14px; |
| `prompt_matrix/static/landing.css` | 1928 | `9px` | padding: 9px 0; |
| `prompt_matrix/static/landing.css` | 1944 | `1px` | border-top: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 1976 | `2px` | border-right: 2px solid var(--trust-blue); |
| `prompt_matrix/static/landing.css` | 1994 | `18px` | padding: 0 18px; |
| `prompt_matrix/static/landing.css` | 2015 | `1px` | border-bottom: 1px solid var(--border-color); |
| `prompt_matrix/static/landing.css` | 2046 | `6px` | padding: 6px 10px; |
| `prompt_matrix/static/landing.css` | 2046 | `10px` | padding: 6px 10px; |
| `prompt_matrix/static/landing.css` | 2070 | `1px` | border-bottom: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2412 | `3px` | border-top: 3px solid #0a0a0a; |
| `prompt_matrix/static/landing.css` | 2653 | `1px` | border-top: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2813 | `1px` | border-top: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2829 | `2px` | border-bottom: 2px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2834 | `1px` | border-bottom: 1px solid #e2e8f0; |
| `prompt_matrix/static/landing.css` | 2853 | `3px` | border-left: 3px solid #16a34a; |
| `prompt_matrix/static/style.css` | 129 | `10px` | --wb-gap: 10px; |
| `prompt_matrix/static/style.css` | 531 | `1px` | border-bottom: 1px solid var(--assure-light-gray); |
| `prompt_matrix/static/style.css` | 546 | `1px` | border-bottom: 1px solid var(--assure-light-gray); |
| `prompt_matrix/static/style.css` | 564 | `2px` | border-bottom: 2px solid var(--color-primary); |
| `prompt_matrix/static/style.css` | 578 | `1px` | border-top: 1px solid var(--line); |
| `prompt_matrix/static/style.css` | 847 | `36px` | padding: var(--space-3) var(--space-3) var(--space-3) 36px; |
| `prompt_matrix/static/style.css` | 860 | `14px` | top: 14px; |
| `prompt_matrix/static/style.css` | 928 | `1px` | border-top: 1px solid var(--line); |
| `prompt_matrix/static/style.css` | 956 | `1px` | border-bottom: 1px solid var(--line); |
| `prompt_matrix/static/style.css` | 1002 | `1px` | border-top: 1px solid var(--line); |
| `prompt_matrix/static/style.css` | 1209 | `1px` | border-top: 1px solid var(--line); |
| `prompt_matrix/static/style.css` | 1244 | `1px` | border-top: 1px solid var(--line); |
| `prompt_matrix/static/style.css` | 1269 | `1px` | .reply { margin-top: var(--space-3); border-top: 1px solid var(--line); padding-top: var(--space-3); } |
| `prompt_matrix/static/style.css` | 1271 | `1px` | .save-box, .learn-fields { margin-top: var(--space-3); padding-top: var(--space-3); border-top: 1px solid var( |
| `prompt_matrix/static/style.css` | 1356 | `10px` | gap: 10px; |
| `prompt_matrix/static/style.css` | 1357 | `20px` | padding: 0 20px; |
| `prompt_matrix/static/style.css` | 1358 | `1px` | border-bottom: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 1394 | `10px` | gap: 10px; |
| `prompt_matrix/static/style.css` | 1481 | `2px` | padding: 2px 6px; |
| `prompt_matrix/static/style.css` | 1481 | `6px` | padding: 2px 6px; |
| `prompt_matrix/static/style.css` | 1548 | `6px` | padding: 6px 12px; |
| `prompt_matrix/static/style.css` | 1606 | `2px` | gap: 2px; |
| `prompt_matrix/static/style.css` | 1609 | `1px` | border-right: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 1620 | `10px` | padding: 10px 12px; |
| `prompt_matrix/static/style.css` | 1679 | `5px` | padding: 0 5px; |
| `prompt_matrix/static/style.css` | 1699 | `1px` | border-bottom: 1px solid #bbf7d0; |
| `prompt_matrix/static/style.css` | 1813 | `10px` | padding: 10px 12px; |
| `prompt_matrix/static/style.css` | 1866 | `2px` | padding: 2px 8px; |
| `prompt_matrix/static/style.css` | 1882 | `2px` | padding: 2px 10px; |
| `prompt_matrix/static/style.css` | 1882 | `10px` | padding: 2px 10px; |
| `prompt_matrix/static/style.css` | 1904 | `6px` | margin-bottom: 6px; |
| `prompt_matrix/static/style.css` | 1910 | `10px` | gap: 10px; |
| `prompt_matrix/static/style.css` | 1911 | `10px` | padding: 10px 12px; |
| `prompt_matrix/static/style.css` | 2059 | `10px` | padding: 8px 10px; |
| `prompt_matrix/static/style.css` | 2060 | `6px` | margin-bottom: 6px; |
| `prompt_matrix/static/style.css` | 2062 | `3px` | border-left: 3px solid #ef4444; |
| `prompt_matrix/static/style.css` | 2075 | `1px` | border-top: 1px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 2105 | `10px` | padding: 8px 10px; |
| `prompt_matrix/static/style.css` | 2286 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 2312 | `6px` | top: calc(100% + 6px); |
| `prompt_matrix/static/style.css` | 2326 | `10px` | padding: 8px 10px; |
| `prompt_matrix/static/style.css` | 2437 | `10px` | padding: 10px 14px; |
| `prompt_matrix/static/style.css` | 2437 | `14px` | padding: 10px 14px; |
| `prompt_matrix/static/style.css` | 2523 | `6px` | padding: 6px 10px; |
| `prompt_matrix/static/style.css` | 2523 | `10px` | padding: 6px 10px; |
| `prompt_matrix/static/style.css` | 2581 | `14px` | padding: 12px 14px; |
| `prompt_matrix/static/style.css` | 2592 | `10px` | margin: 0 0 10px; |
| `prompt_matrix/static/style.css` | 2624 | `52px` | inset: 52px 0 0; |
| `prompt_matrix/static/style.css` | 2657 | `88px` | padding-bottom: 88px; |
| `prompt_matrix/static/style.css` | 2667 | `1px` | border-top: 1px solid var(--border-color); |
| `prompt_matrix/static/style.css` | 2681 | `52px` | top: 52px; |
| `prompt_matrix/static/style.css` | 2723 | `6px` | padding: 6px 4px; |
| `prompt_matrix/static/style.css` | 2779 | `10px` | gap: 10px; |
| `prompt_matrix/static/style.css` | 2780 | `20px` | padding: 0 20px; |
| `prompt_matrix/static/style.css` | 2781 | `1px` | border-bottom: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 2815 | `6px` | padding: 6px 12px; |
| `prompt_matrix/static/style.css` | 2836 | `1px` | border-bottom: 1px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 2860 | `1px` | border-right: 1px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 2904 | `40px` | padding: 40px 16px; |
| `prompt_matrix/static/style.css` | 3001 | `1px` | border-bottom: 1px solid #f59e0b; |
| `prompt_matrix/static/style.css` | 3018 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 3068 | `7px` | padding: 7px 11px; |
| `prompt_matrix/static/style.css` | 3068 | `11px` | padding: 7px 11px; |
| `prompt_matrix/static/style.css` | 3086 | `11px` | margin: 0 11px 8px; |
| `prompt_matrix/static/style.css` | 3093 | `7px` | padding: 7px 11px 11px; |
| `prompt_matrix/static/style.css` | 3093 | `11px` | padding: 7px 11px 11px; |
| `prompt_matrix/static/style.css` | 3107 | `11px` | gap: 8px 11px; |
| `prompt_matrix/static/style.css` | 3108 | `7px` | padding: 7px 11px; |
| `prompt_matrix/static/style.css` | 3108 | `11px` | padding: 7px 11px; |
| `prompt_matrix/static/style.css` | 3109 | `1px` | border-bottom: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 3116 | `7px` | gap: 7px; |
| `prompt_matrix/static/style.css` | 3126 | `7px` | gap: 7px; |
| `prompt_matrix/static/style.css` | 3132 | `2px` | padding: 2px 7px; |
| `prompt_matrix/static/style.css` | 3132 | `7px` | padding: 2px 7px; |
| `prompt_matrix/static/style.css` | 3160 | `6px` | top: 6px; |
| `prompt_matrix/static/style.css` | 3161 | `6px` | bottom: 6px; |
| `prompt_matrix/static/style.css` | 3205 | `1px` | margin-left: 1px; |
| `prompt_matrix/static/style.css` | 3233 | `2px` | padding: 2px 4px; |
| `prompt_matrix/static/style.css` | 3261 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 3300 | `2px` | padding: 2px 6px; |
| `prompt_matrix/static/style.css` | 3300 | `6px` | padding: 2px 6px; |
| `prompt_matrix/static/style.css` | 3343 | `1px` | border-top: 1px solid var(--color-border, #E2E8F0); |
| `prompt_matrix/static/style.css` | 3364 | `1px` | border-bottom: 1px solid var(--color-border, #E2E8F0); |
| `prompt_matrix/static/style.css` | 3406 | `14px` | padding-right: calc(var(--space-lg) + 14px); |
| `prompt_matrix/static/style.css` | 3478 | `2px` | padding: 2px 4px; |
| `prompt_matrix/static/style.css` | 3492 | `2px` | border-bottom: 2px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 3503 | `2px` | padding: 2px 4px; |
| `prompt_matrix/static/style.css` | 3519 | `6px` | padding: 6px; |
| `prompt_matrix/static/style.css` | 3527 | `2px` | gap: 2px; |
| `prompt_matrix/static/style.css` | 3539 | `10px` | padding: 10px; |
| `prompt_matrix/static/style.css` | 3554 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 3565 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 3578 | `6px` | padding: 6px; |
| `prompt_matrix/static/style.css` | 3606 | `6px` | margin-top: 6px; |
| `prompt_matrix/static/style.css` | 3607 | `2px` | padding: 2px 8px; |
| `prompt_matrix/static/style.css` | 3617 | `6px` | margin-left: 6px; |
| `prompt_matrix/static/style.css` | 3625 | `18px` | padding-left: 18px; |
| `prompt_matrix/static/style.css` | 3644 | `10px` | padding: 8px 10px; |
| `prompt_matrix/static/style.css` | 3662 | `10px` | padding: 0 0 0 10px; |
| `prompt_matrix/static/style.css` | 3741 | `6px` | padding: 4px 6px; |
| `prompt_matrix/static/style.css` | 3809 | `6px` | top: calc(100% + 6px); |
| `prompt_matrix/static/style.css` | 3830 | `2px` | padding: 2px 4px; |
| `prompt_matrix/static/style.css` | 3847 | `2px` | padding: 2px 6px; |
| `prompt_matrix/static/style.css` | 3847 | `6px` | padding: 2px 6px; |
| `prompt_matrix/static/style.css` | 3883 | `2px` | margin-bottom: 2px; |
| `prompt_matrix/static/style.css` | 3914 | `1px` | border-bottom: 1px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 3934 | `2px` | padding: 2px 8px; |
| `prompt_matrix/static/style.css` | 3943 | `3px` | border-left: 3px solid var(--color-text-muted) !important; |
| `prompt_matrix/static/style.css` | 3975 | `2px` | margin: 2px 0; |
| `prompt_matrix/static/style.css` | 3995 | `2px` | border-top: 2px dashed transparent; |
| `prompt_matrix/static/style.css` | 4011 | `2px` | padding: 2px 10px; |
| `prompt_matrix/static/style.css` | 4011 | `10px` | padding: 2px 10px; |
| `prompt_matrix/static/style.css` | 4051 | `1px` | padding: 1px 4px; |
| `prompt_matrix/static/style.css` | 4058 | `1px` | padding: 1px 4px; |
| `prompt_matrix/static/style.css` | 4066 | `1px` | border-top: 1px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 4132 | `3px` | padding: 3px 10px; |
| `prompt_matrix/static/style.css` | 4132 | `10px` | padding: 3px 10px; |
| `prompt_matrix/static/style.css` | 4136 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 4169 | `3px` | padding: 3px 8px; |
| `prompt_matrix/static/style.css` | 4171 | `2px` | border-bottom: 2px dotted transparent; |
| `prompt_matrix/static/style.css` | 4219 | `2px` | border-bottom: 2px dotted var(--color-primary); |
| `prompt_matrix/static/style.css` | 4220 | `2px` | padding: 0 2px; |
| `prompt_matrix/static/style.css` | 4234 | `2px` | padding: 2px 8px; |
| `prompt_matrix/static/style.css` | 4249 | `3px` | border-left: 3px solid var(--color-primary, #1a4b8c); |
| `prompt_matrix/static/style.css` | 4256 | `1px` | border-top: 1px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 4261 | `2px` | border-left: 2px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 4338 | `1px` | border-left: 1px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 4401 | `1px` | border-top: 1px solid var(--border-color); |
| `prompt_matrix/static/style.css` | 4468 | `1px` | padding: 1px var(--space-3); |
| `prompt_matrix/static/style.css` | 4549 | `11px` | gap: 11px; |
| `prompt_matrix/static/style.css` | 4550 | `11px` | margin-bottom: 11px; |
| `prompt_matrix/static/style.css` | 4556 | `11px` | gap: 11px; |
| `prompt_matrix/static/style.css` | 4562 | `11px` | padding: 11px; |
| `prompt_matrix/static/style.css` | 4566 | `7px` | gap: 7px; |
| `prompt_matrix/static/style.css` | 4584 | `2px` | padding: 2px 7px; |
| `prompt_matrix/static/style.css` | 4584 | `7px` | padding: 2px 7px; |
| `prompt_matrix/static/style.css` | 4595 | `7px` | gap: 7px; |
| `prompt_matrix/static/style.css` | 4725 | `10px` | padding: 10px 12px; |
| `prompt_matrix/static/style.css` | 4726 | `14px` | margin-bottom: 14px; |
| `prompt_matrix/static/style.css` | 4841 | `3px` | border-left: 3px solid var(--success); |
| `prompt_matrix/static/style.css` | 4907 | `3px` | border-left: 3px solid var(--trust-blue); |
| `prompt_matrix/static/style.css` | 5037 | `2px` | padding: 2px 8px; |
| `prompt_matrix/static/style.css` | 5057 | `6px` | padding: 6px 12px !important; |
| `prompt_matrix/static/style.css` | 5313 | `2px` | padding: 0 2px; |
| `prompt_matrix/static/style.css` | 5315 | `1px` | border-bottom: 1px solid #A5D6A7; |
| `prompt_matrix/static/style.css` | 5320 | `2px` | padding: 0 2px; |
| `prompt_matrix/static/style.css` | 5322 | `1px` | border-bottom: 1px solid #FDD835; |
| `prompt_matrix/static/style.css` | 5332 | `1px` | border-top: 1px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 5375 | `6px` | padding: 4px 6px; |
| `prompt_matrix/static/style.css` | 5381 | `6px` | padding: 6px 12px; |
| `prompt_matrix/static/style.css` | 5421 | `1px` | border-bottom: 1px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 5447 | `1px` | border-bottom: 1px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 5491 | `2px` | right: 2px; |
| `prompt_matrix/static/style.css` | 5493 | `2px` | padding: 2px 4px; |
| `prompt_matrix/static/style.css` | 5497 | `2px` | padding: 2px 4px; |
| `prompt_matrix/static/style.css` | 5523 | `1px` | border-bottom: 1px solid var(--color-border); |
| `prompt_matrix/static/style.css` | 5605 | `11px` | gap: 11px; |
| `prompt_matrix/static/style.css` | 5621 | `10px` | gap: 10px; |
| `prompt_matrix/static/style.css` | 5622 | `14px` | padding: 14px; |
| `prompt_matrix/static/style.css` | 5650 | `3px` | padding: 3px 10px; |
| `prompt_matrix/static/style.css` | 5650 | `10px` | padding: 3px 10px; |
| `prompt_matrix/static/style.css` | 5709 | `6px` | gap: 6px 10px; |
| `prompt_matrix/static/style.css` | 5709 | `10px` | gap: 6px 10px; |
| `prompt_matrix/static/style.css` | 5739 | `1px` | border-top: 1px solid #f1f5f9; |
| `prompt_matrix/static/style.css` | 5750 | `2px` | gap: 2px; |
| `prompt_matrix/static/style.css` | 5791 | `2px` | gap: 2px; |
| `prompt_matrix/static/style.css` | 5818 | `2px` | gap: 2px; |
| `prompt_matrix/static/style.css` | 5831 | `2px` | padding: 2px 4px; |
| `prompt_matrix/static/style.css` | 5877 | `2px` | padding: 2px 10px; |
| `prompt_matrix/static/style.css` | 5877 | `10px` | padding: 2px 10px; |
| `prompt_matrix/static/style.css` | 5969 | `1px` | border-bottom: 1px solid rgba(26, 75, 140, 0.16); |
| `prompt_matrix/static/style.css` | 6082 | `2px` | padding: 2px 10px; |
| `prompt_matrix/static/style.css` | 6082 | `10px` | padding: 2px 10px; |
| `prompt_matrix/static/style.css` | 6252 | `2px` | margin: 0 0 2px; |
| `prompt_matrix/static/style.css` | 6286 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 6407 | `3px` | padding: 3px 4px; |
| `prompt_matrix/static/style.css` | 6420 | `3px` | padding: 3px 5px; |
| `prompt_matrix/static/style.css` | 6420 | `5px` | padding: 3px 5px; |
| `prompt_matrix/static/style.css` | 6472 | `14px` | padding-left: 14px; |
| `prompt_matrix/static/style.css` | 6480 | `1px` | padding: 1px 5px; |
| `prompt_matrix/static/style.css` | 6480 | `5px` | padding: 1px 5px; |
| `prompt_matrix/static/style.css` | 6482 | `2px` | border-bottom-width: 2px; |
| `prompt_matrix/static/style.css` | 6562 | `2px` | gap: 2px; |
| `prompt_matrix/static/style.css` | 6573 | `7px` | padding: 7px 8px; |
| `prompt_matrix/static/style.css` | 6584 | `3px` | gap: 3px; |
| `prompt_matrix/static/style.css` | 6592 | `3px` | padding: 3px 7px; |
| `prompt_matrix/static/style.css` | 6592 | `7px` | padding: 3px 7px; |
| `prompt_matrix/static/style.css` | 6624 | `10px` | padding: var(--space-sm) 10px; |
| `prompt_matrix/static/style.css` | 6638 | `6px` | margin-right: 6px; |
| `prompt_matrix/static/style.css` | 6650 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 6654 | `1px` | padding: 1px 7px; |
| `prompt_matrix/static/style.css` | 6654 | `7px` | padding: 1px 7px; |
| `prompt_matrix/static/style.css` | 6663 | `10px` | padding: 0 10px var(--space-sm); |
| `prompt_matrix/static/style.css` | 6679 | `6px` | padding: 6px 8px; |
| `prompt_matrix/static/style.css` | 6703 | `3px` | margin-top: 3px; |
| `prompt_matrix/static/style.css` | 6712 | `2px` | gap: 2px; |
| `prompt_matrix/static/style.css` | 6718 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 6754 | `2px` | padding: 2px 4px; |
| `prompt_matrix/static/style.css` | 6832 | `1px` | border-top: 1px solid var(--border-subtle, #e2e8f0); |
| `prompt_matrix/static/style.css` | 6847 | `18px` | padding-left: 18px; |
| `prompt_matrix/static/style.css` | 6848 | `2px` | margin-top: 2px; |
| `prompt_matrix/static/style.css` | 6852 | `6px` | margin-top: 6px; |
| `prompt_matrix/static/style.css` | 6863 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 6864 | `6px` | padding: 4px 6px; |
| `prompt_matrix/static/style.css` | 6885 | `2px` | padding: 0 2px; |
| `prompt_matrix/static/style.css` | 6929 | `14px` | margin-bottom: var(--wb-gap-md, 14px); |
| `prompt_matrix/static/style.css` | 6941 | `11px` | padding: 0 0 11px; |
| `prompt_matrix/static/style.css` | 6950 | `7px` | gap: 7px; |
| `prompt_matrix/static/style.css` | 6958 | `7px` | padding: 7px 11px; |
| `prompt_matrix/static/style.css` | 6958 | `11px` | padding: 7px 11px; |
| `prompt_matrix/static/style.css` | 7018 | `1px` | border-top: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 7026 | `7px` | padding: 7px 11px; |
| `prompt_matrix/static/style.css` | 7026 | `11px` | padding: 7px 11px; |
| `prompt_matrix/static/style.css` | 7032 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 7055 | `3px` | padding: 3px 8px; |
| `prompt_matrix/static/style.css` | 7562 | `2px` | left: 2px; |
| `prompt_matrix/static/style.css` | 7765 | `1px` | border-left: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7776 | `1px` | border-bottom: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7801 | `1px` | border-bottom: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7822 | `1px` | border-left: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7841 | `1px` | border-bottom: 1px solid var(--warm-gray, #e2e8f0); |
| `prompt_matrix/static/style.css` | 7881 | `3px` | border-left: 3px solid var(--primary, #0a0a0a); |
| `prompt_matrix/static/style.css` | 7981 | `10px` | margin-bottom: 10px; |
| `prompt_matrix/static/style.css` | 7999 | `6px` | padding: 4px 6px; |
| `prompt_matrix/static/style.css` | 8157 | `10px` | padding: 10px 16px !important; |
| `prompt_matrix/static/style.css` | 8338 | `1px` | border-bottom: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8348 | `1px` | border-bottom: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8371 | `28px` | .backstage-section { margin-top: 28px; } |
| `prompt_matrix/static/style.css` | 8384 | `10px` | padding: 8px 10px; |
| `prompt_matrix/static/style.css` | 8385 | `1px` | border-bottom: 1px solid var(--border-color, #e2e8f0); |
| `prompt_matrix/static/style.css` | 8444 | `1px` | border-top: 1px solid var(--border-subtle, #e2e8f0); |
| `prompt_matrix/static/style.css` | 8456 | `1px` | border-bottom: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 8469 | `10px` | padding: 8px 10px; |
| `prompt_matrix/static/style.css` | 8472 | `1px` | border-bottom: 1px solid var(--color-border, #e2e8f0); |
| `prompt_matrix/static/style.css` | 8553 | `14px` | padding: 14px 16px; |
| `prompt_matrix/static/style.css` | 8555 | `1px` | border-bottom: 1px solid var(--border, #e5e7eb); |
| `prompt_matrix/static/style.css` | 8574 | `10px` | padding: 10px 16px; |
| `prompt_matrix/static/style.css` | 8626 | `10px` | padding: 4px 10px; |
| `prompt_matrix/static/style.css` | 8703 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 8705 | `14px` | padding: 14px 16px; |
| `prompt_matrix/static/style.css` | 8769 | `20px` | padding: 20px; |
| `prompt_matrix/static/style.css` | 8853 | `20px` | padding: 16px 20px; |
| `prompt_matrix/static/style.css` | 8855 | `1px` | border-bottom: 1px solid var(--border-color, var(--border-subtle, #e5e7eb)); |
| `prompt_matrix/static/style.css` | 8868 | `10px` | gap: 10px; |
| `prompt_matrix/static/style.css` | 8878 | `10px` | padding: 10px 12px; |
| `prompt_matrix/static/style.css` | 8887 | `6px` | margin-bottom: 6px; |
| `prompt_matrix/static/style.css` | 8895 | `2px` | padding: 2px 8px; |
| `prompt_matrix/static/style.css` | 8925 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 8990 | `10px` | margin-bottom: 10px; |
| `prompt_matrix/static/style.css` | 8996 | `14px` | left: 14px; |
| `prompt_matrix/static/style.css` | 9009 | `14px` | padding: 12px 14px 12px 42px; |
| `prompt_matrix/static/style.css` | 9009 | `42px` | padding: 12px 14px 12px 42px; |
| `prompt_matrix/static/style.css` | 9025 | `14px` | padding: 14px; |
| `prompt_matrix/static/style.css` | 9108 | `1px` | border-bottom: 1px solid rgba(51, 65, 85, 0.5); |
| `prompt_matrix/static/style.css` | 9113 | `6px` | padding: 6px; |
| `prompt_matrix/static/style.css` | 9224 | `1px` | padding: 1px 6px; |
| `prompt_matrix/static/style.css` | 9224 | `6px` | padding: 1px 6px; |
| `prompt_matrix/static/style.css` | 9225 | `2px` | margin: 0 2px; |
| `prompt_matrix/static/style.css` | 9236 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 9287 | `10px` | gap: 10px; |
| `prompt_matrix/static/style.css` | 9321 | `18px` | margin-bottom: 18px; |
| `prompt_matrix/static/style.css` | 9330 | `10px` | margin: 0 0 10px; |
| `prompt_matrix/static/style.css` | 9336 | `14px` | padding: 12px 14px; |
| `prompt_matrix/static/style.css` | 9353 | `2px` | padding: 2px 8px; |
| `prompt_matrix/static/style.css` | 9373 | `10px` | top: 10px; |
| `prompt_matrix/static/style.css` | 9374 | `10px` | right: 10px; |
| `prompt_matrix/static/style.css` | 9378 | `3px` | padding: 3px 8px; |
| `prompt_matrix/static/style.css` | 9390 | `14px` | padding: 14px 16px; |
| `prompt_matrix/static/style.css` | 9391 | `36px` | padding-top: 36px; |
| `prompt_matrix/static/style.css` | 9414 | `2px` | padding: 0 2px; |
| `prompt_matrix/static/style.css` | 9424 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 9429 | `6px` | padding: 6px 8px; |
| `prompt_matrix/static/style.css` | 9437 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 9449 | `1px` | border-left: 1px solid var(--border-subtle, #e5e7eb); |
| `prompt_matrix/static/style.css` | 9488 | `2px` | padding: 2px 0; |
| `prompt_matrix/static/style.css` | 9550 | `10px` | padding: 10px 12px; |
| `prompt_matrix/static/style.css` | 9599 | `1px` | border-left: 1px solid var(--border-subtle, #e5e7eb); |
| `prompt_matrix/static/style.css` | 9618 | `20px` | padding: 16px 20px; |
| `prompt_matrix/static/style.css` | 9621 | `1px` | border-bottom: 1px solid var(--border-color, var(--border-subtle, #e5e7eb)); |
| `prompt_matrix/static/style.css` | 9632 | `20px` | margin: 12px 20px 0; |
| `prompt_matrix/static/style.css` | 9633 | `14px` | padding: 12px 14px; |
| `prompt_matrix/static/style.css` | 9648 | `10px` | margin-bottom: 10px; |
| `prompt_matrix/static/style.css` | 9673 | `10px` | padding: 10px 12px; |
| `prompt_matrix/static/style.css` | 9682 | `6px` | gap: 6px; |
| `prompt_matrix/static/style.css` | 9684 | `6px` | margin-bottom: 6px; |
| `prompt_matrix/static/style.css` | 9696 | `2px` | padding: 2px 6px; |
| `prompt_matrix/static/style.css` | 9696 | `6px` | padding: 2px 6px; |
| `prompt_matrix/static/style.css` | 9843 | `1px` | border-right: 1px solid var(--border-subtle, #e5e7eb); |
| `prompt_matrix/static/style.css` | 9876 | `1px` | border-left: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 9897 | `10px` | padding: 10px 12px; |
| `prompt_matrix/static/style.css` | 9898 | `3px` | border-left: 3px solid var(--border-subtle, #cbd5e1); |
| `prompt_matrix/static/style.css` | 9906 | `10px` | padding: 10px 12px; |
| `prompt_matrix/static/style.css` | 9920 | `10px` | padding: 10px 12px; |
| `prompt_matrix/static/style.css` | 9945 | `10px` | gap: 10px; |
| `prompt_matrix/static/style.css` | 9949 | `10px` | margin: 0 0 10px; |
| `prompt_matrix/static/style.css` | 9960 | `2px` | padding: 2px 6px; |
| `prompt_matrix/static/style.css` | 9960 | `6px` | padding: 2px 6px; |
| `prompt_matrix/static/style.css` | 9980 | `6px` | margin-left: 6px; |
| `prompt_matrix/static/style.css` | 9999 | `10px` | margin-top: 10px; |
| `prompt_matrix/static/style.css` | 10011 | `10px` | gap: 10px; |
| `prompt_matrix/static/style.css` | 10083 | `1px` | border-bottom: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 10097 | `10px` | margin-bottom: 10px; |
| `prompt_matrix/static/style.css` | 10101 | `6px` | margin: 0 0 6px; |
| `prompt_matrix/static/style.css` | 10106 | `10px` | margin: 0 0 10px; |
| `prompt_matrix/static/style.css` | 10114 | `10px` | gap: 10px; |
| `prompt_matrix/static/style.css` | 10115 | `20px` | padding: 16px 20px; |
| `prompt_matrix/static/style.css` | 10116 | `1px` | border-bottom: 1px solid var(--border-color, var(--border-subtle, #e2e8f0)); |
| `prompt_matrix/static/style.css` | 10149 | `6px` | padding: 6px 8px; |
| `prompt_matrix/static/style.css` | 10166 | `10px` | padding: 8px 10px; |
| `prompt_matrix/static/style.css` | 10172 | `10px` | padding: 12px 12px 10px; |
| `prompt_matrix/static/style.css` | 10221 | `10px` | padding: 8px 10px; |
| `prompt_matrix/static/style.css` | 10223 | `6px` | margin-bottom: 6px; |
| `prompt_matrix/static/style.css` | 10231 | `10px` | padding: 8px 10px; |
| `prompt_matrix/static/style.css` | 10301 | `1px` | border-left: 1px solid #e2e8f0; |
| `prompt_matrix/static/style.css` | 10326 | `1px` | border-right: 1px solid rgba(255, 255, 255, 0.08); |
| `prompt_matrix/static/style.css` | 10377 | `2px` | border-left-width: 2px; |
| `prompt_matrix/static/style.css` | 10523 | `1px` | border-right: 1px solid var(--border-color, var(--color-border, #e2e8f0)); |
| `prompt_matrix/static/style.css` | 10545 | `1px` | border-right: 1px solid rgba(255, 255, 255, 0.06); |
| `prompt_matrix/static/style.css` | 10636 | `10px` | left: calc(100% + 10px); |
| `prompt_matrix/static/style.css` | 10640 | `6px` | padding: 6px 10px; |
| `prompt_matrix/static/style.css` | 10640 | `10px` | padding: 6px 10px; |
| `prompt_matrix/static/style.css` | 10691 | `2px` | margin-top: 2px; |
| `prompt_matrix/static/style.css` | 10821 | `2px` | padding: 2px 10px; |
| `prompt_matrix/static/style.css` | 10821 | `10px` | padding: 2px 10px; |

#### 3. Typography violations (Inter-only standard)

| File | Line | Finding | Snippet |
| --- | ---: | --- | --- |
| `prompt_matrix/static/jdf.bundle.js` | 1 | js-fontFamily-without-Inter | (()=>{var te=(t=>typeof require<"u"?require:typeof Proxy<"u"?new Proxy(t,{get:(n,r)=>(typeof require<"u"?requi |
| `prompt_matrix/static/landing.css` | 34 | fallback-stack-after-Inter: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif | font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; |
| `prompt_matrix/static/landing.css` | 549 | primary-not-Inter: ui-monospace, "Cascadia Code", monospace | font-family: ui-monospace, "Cascadia Code", monospace; |
| `prompt_matrix/static/landing.css` | 1368 | primary-not-Inter: ui-monospace, monospace | font-family: ui-monospace, monospace; |
| `prompt_matrix/static/landing.css` | 1428 | primary-not-Inter: var(--font-sans, Inter, system-ui, sans-serif) | font-family: var(--font-sans, Inter, system-ui, sans-serif); |
| `prompt_matrix/static/landing.css` | 1441 | primary-not-Inter: ui-monospace, monospace | font-family: ui-monospace, monospace; |
| `prompt_matrix/static/landing.css` | 1554 | primary-not-Inter: ui-monospace, monospace | font-family: ui-monospace, monospace; |
| `prompt_matrix/static/landing.css` | 1698 | primary-not-Inter: "IBM Plex Mono", ui-monospace, monospace | font-family: "IBM Plex Mono", ui-monospace, monospace; |
| `prompt_matrix/static/landing.css` | 1756 | primary-not-Inter: "IBM Plex Mono", ui-monospace, monospace | font-family: "IBM Plex Mono", ui-monospace, monospace; |
| `prompt_matrix/static/landing.css` | 1764 | primary-not-Inter: Georgia, "Times New Roman", serif | font-family: Georgia, "Times New Roman", serif; |
| `prompt_matrix/static/landing.css` | 1845 | primary-not-Inter: "IBM Plex Mono", ui-monospace, monospace | font-family: "IBM Plex Mono", ui-monospace, monospace; |
| `prompt_matrix/static/landing.css` | 1965 | primary-not-Inter: "IBM Plex Mono", ui-monospace, monospace | font-family: "IBM Plex Mono", ui-monospace, monospace; |
| `prompt_matrix/static/landing.css` | 2061 | fallback-stack-after-Inter: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif | --font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; |
| `prompt_matrix/static/landing.css` | 2064 | primary-not-Inter: var(--font-family) | font-family: var(--font-family); |
| `prompt_matrix/static/landing.css` | 2860 | primary-not-Inter: ui-monospace, "IBM Plex Mono", monospace | font-family: ui-monospace, "IBM Plex Mono", monospace; |
| `prompt_matrix/static/style.css` | 20 | fallback-stack-after-Inter: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial | --font-family: Inter, -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-ser |
| `prompt_matrix/static/style.css` | 163 | primary-not-Inter: var(--font-family) !important | font-family: var(--font-family) !important; |
| `prompt_matrix/static/style.css` | 240 | primary-not-Inter: var(--font-family) | font-family: var(--font-family); |
| `prompt_matrix/static/style.css` | 338 | primary-not-Inter: var(--font-family) | font-family: var(--font-family); |
| `prompt_matrix/static/style.css` | 383 | primary-not-Inter: var(--font-family) | font-family: var(--font-family); |
| `prompt_matrix/static/style.css` | 401 | primary-not-Inter: var(--font-family) | font-family: var(--font-family); |
| `prompt_matrix/static/style.css` | 456 | primary-not-Inter: var(--font-mono, monospace) | font-family: var(--font-mono, monospace); |
| `prompt_matrix/static/style.css` | 484 | primary-not-Inter: var(--font-family) | font-family: var(--font-family); |
| `prompt_matrix/static/style.css` | 750 | primary-not-Inter: var(--font-family-mono) | font-family: var(--font-family-mono); |
| `prompt_matrix/static/style.css` | 759 | primary-not-Inter: var(--font-family-mono) | font-family: var(--font-family-mono); |
| `prompt_matrix/static/style.css` | 769 | primary-not-Inter: var(--font-family-mono) | font-family: var(--font-family-mono); |
| `prompt_matrix/static/style.css` | 1210 | primary-not-Inter: var(--font-family-mono) | font-family: var(--font-family-mono); |
| `prompt_matrix/static/style.css` | 1260 | primary-not-Inter: var(--font-family-mono) | font-family: var(--font-family-mono); |
| `prompt_matrix/static/style.css` | 1477 | primary-not-Inter: ui-monospace, SFMono-Regular, Menlo, monospace | font-family: ui-monospace, SFMono-Regular, Menlo, monospace; |
| `prompt_matrix/static/style.css` | 2976 | primary-not-Inter: ui-monospace, SFMono-Regular, Menlo, monospace | font-family: ui-monospace, SFMono-Regular, Menlo, monospace; |
| `prompt_matrix/static/style.css` | 3436 | primary-not-Inter: var(--font-mono) | font-family: var(--font-mono); |
| `prompt_matrix/static/style.css` | 4462 | primary-not-Inter: var(--font-family-mono) | font-family: var(--font-family-mono); |
| `prompt_matrix/static/style.css` | 4475 | primary-not-Inter: var(--font-family-mono) | font-family: var(--font-family-mono); |
| `prompt_matrix/static/style.css` | 4761 | primary-not-Inter: var(--font-mono, ui-monospace, monospace) | font-family: var(--font-mono, ui-monospace, monospace); |
| `prompt_matrix/static/style.css` | 5036 | primary-not-Inter: var(--font-mono) | font-family: var(--font-mono); |
| `prompt_matrix/static/style.css` | 5170 | primary-not-Inter: var(--font-family-mono) | font-family: var(--font-family-mono); |
| `prompt_matrix/static/style.css` | 6486 | primary-not-Inter: var(--font-mono) | font-family: var(--font-mono); |
| `prompt_matrix/static/style.css` | 6589 | primary-not-Inter: var(--font-mono) | font-family: var(--font-mono); |
| `prompt_matrix/static/style.css` | 7648 | primary-not-Inter: var(--font-mono, "IBM Plex Mono", monospace) | font-family: var(--font-mono, "IBM Plex Mono", monospace); |
| `prompt_matrix/static/style.css` | 9070 | fallback-stack-after-Inter: "Inter", system-ui, sans-serif | font-family: "Inter", system-ui, sans-serif; |
| `prompt_matrix/static/style.css` | 9149 | primary-not-Inter: "JetBrains Mono", ui-monospace, monospace | font-family: "JetBrains Mono", ui-monospace, monospace; |
| `prompt_matrix/static/style.css` | 9175 | primary-not-Inter: "JetBrains Mono", ui-monospace, monospace | font-family: "JetBrains Mono", ui-monospace, monospace; |
| `prompt_matrix/static/style.css` | 9395 | primary-not-Inter: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace | font-family: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, monospace; |
| `prompt_matrix/static/style.css` | 9576 | primary-not-Inter: ui-monospace, SFMono-Regular, Menlo, monospace | font-family: ui-monospace, SFMono-Regular, Menlo, monospace; |
| `prompt_matrix/static/style.css` | 9691 | primary-not-Inter: ui-monospace, SFMono-Regular, Menlo, monospace | font-family: ui-monospace, SFMono-Regular, Menlo, monospace; |
| `prompt_matrix/static/style.css` | 9910 | primary-not-Inter: ui-monospace, SFMono-Regular, Menlo, monospace | font-family: ui-monospace, SFMono-Regular, Menlo, monospace; |
| `prompt_matrix/static/style.css` | 10026 | primary-not-Inter: ui-monospace, SFMono-Regular, Menlo, monospace | font-family: ui-monospace, SFMono-Regular, Menlo, monospace; |
| `prompt_matrix/static/style.css` | 10802 | primary-not-Inter: "JetBrains Mono", "Fira Code", monospace | font-family: "JetBrains Mono", "Fira Code", monospace; |

#### 4. Copy violations (robotic / non-actionable UI text)

| File | Line | Type | Match | Snippet |
| --- | ---: | --- | --- | --- |
| `prompt_matrix/static/app.js` | 128 | vague-error | `Invalid` | 'Invalid API key. Verify your credentials in the <a href="' + |
| `prompt_matrix/static/inquire_client.js` | 347 | vague-error | `Failed` | if (statusEl) statusEl.textContent = data && data.ok ? "Complete" : "Failed"; |
| `prompt_matrix/static/jdf.bundle.js` | 1 | vague-error | `Failed` | (()=>{var te=(t=>typeof require<"u"?require:typeof Proxy<"u"?new Proxy(t,{get:(n,r)=>(typeof require<"u"?requi |
| `prompt_matrix/static/jdf.bundle.js` | 1 | vague-error | `Invalid` | (()=>{var te=(t=>typeof require<"u"?require:typeof Proxy<"u"?new Proxy(t,{get:(n,r)=>(typeof require<"u"?requi |
| `prompt_matrix/static/prompt_history.js` | 67 | vague-error | `Failed` | meta.textContent = t("generate.history_fail", "Failed"); |
| `prompt_matrix/static/sentry.bundle.js` | 5 | vague-error | `Failed` | Error:`,o)}}var Ze,Er,be=p(()=>{D();R();Me();Ze={},Er={}});function zt(e){let t="error";C(t,e),O(t,_s)}functio |
| `prompt_matrix/static/sentry.bundle.js` | 5 | vague-error | `Invalid` | Error:`,o)}}var Ze,Er,be=p(()=>{D();R();Me();Ze={},Er={}});function zt(e){let t="error";C(t,e),O(t,_s)}functio |
| `prompt_matrix/static/sentry.bundle.js` | 15 | vague-error | `Failed` | Url: ${At(e)}`),!0}return!1}function Oa(e,t){return t?.length?Rt(e).some(n=>Ne(n,t)):!1}function La(e,t){if(!t |
| `prompt_matrix/static/substrate_vault.js` | 233 | vague-error | `Failed` | status.textContent = t("substrate.vault.failed", "Failed"); |
| `prompt_matrix/static/tiptap.bundle.js` | 1 | vague-error | `Invalid` | (()=>{var N=(n,e)=>()=>(n&&(e=n(n=0)),e);var mf=(n,e)=>()=>(e\|\|n((e={exports:{}}).exports,e),e.exports);var Go |
| `prompt_matrix/static/tiptap.bundle.js` | 2 | vague-error | `Invalid` | `)}};ht.empty=new ht(!0);Oi=class{constructor(e,t){this.string=e,this.nodeTypes=t,this.inline=null,this.pos=0, |
| `prompt_matrix/static/tiptap.bundle.js` | 5 | vague-error | `Invalid` | `))}})}function Uf(n,e,t){let r=n.resolve(e),i=r.index();return r.parent.canReplaceWith(i,i+1,t)}function Jf(n |
| `prompt_matrix/static/tiptap.bundle.js` | 10 | vague-error | `Invalid` | `),d&&p==h.nodeValue.length)for(let m=h,g;m;m=m.parentNode){if(g=m.nextSibling){g.nodeName=="BR"&&(l=a={node:g |
| `prompt_matrix/static/tiptap.bundle.js` | 96 | vague-error | `Failed` | }`,vu=class extends vg{constructor(n={}){super(),this.css=null,this.className="tiptap",this.editorView=null,th |
| `prompt_matrix/static/tiptap.bundle.js` | 96 | vague-error | `Invalid` | }`,vu=class extends vg{constructor(n={}){super(),this.css=null,this.className="tiptap",this.editorView=null,th |
| `prompt_matrix/static/tiptap.bundle.js` | 126 | vague-error | `Invalid` | 3. "-" cannot repeat`);V.customSchemes.push([n,e])}function Fy(){V.scanner=Dy(V.customSchemes);for(let n=0;n<V |
| `prompt_matrix/static/tiptap.bundle.js` | 138 | vague-error | `Invalid` | `):"",markdownTokenizer:{name:"taskList",level:"block",start(n){var e;let t=(e=n.match(/^\s*[-+*]\s+\[([ xX])\ |
| `prompt_matrix/i18n.py` | 351 | absence-only-empty | `No nodes compiled.` | "landing.sandbox.node.empty": "No nodes compiled.", |
| `prompt_matrix/i18n.py` | 448 | absence-only-empty | `No grammar changes suggested.` | "founder.polish.no_changes": "No grammar changes suggested.", |
| `prompt_matrix/i18n.py` | 452 | absence-only-empty | `No verifiable issues found.` | "founder.scan.no_issues": "No verifiable issues found.", |
| `prompt_matrix/i18n.py` | 462 | absence-only-empty | `No Red-Hat audits found. Run Red-Hat on a draft run.` | "founder.runs.empty_redhat": "No Red-Hat audits found. Run Red-Hat on a draft run.", |
| `prompt_matrix/i18n.py` | 463 | absence-only-empty | `No export-ready runs found. Complete verification to build a` | "founder.runs.empty_dossier": "No export-ready runs found. Complete verification to build a dossier.", |
| `prompt_matrix/i18n.py` | 492 | absence-only-empty | `No saved versions yet.` | "founder.versions.empty": "No saved versions yet.", |
| `prompt_matrix/i18n.py` | 502 | absence-only-empty | `No Red-Hat findings for this run.` | "founder.drawer.redhat_empty": "No Red-Hat findings for this run.", |
| `prompt_matrix/i18n.py` | 554 | absence-only-empty | `No extracted text for this source.` | "evidence.inspector.no_text": "No extracted text for this source.", |
| `prompt_matrix/i18n.py` | 556 | absence-only-empty | `No source linked to this lock.` | "evidence.inspector.no_source": "No source linked to this lock.", |
| `prompt_matrix/i18n.py` | 590 | vague-error | `Failed` | "substrate.vault.failed": "Failed", |
| `prompt_matrix/i18n.py` | 595 | absence-only-empty | `No claims from this file yet — compile a document to ground ` | "substrate.vault.no_claims_yet": "No claims from this file yet — compile a document to ground it.", |
| `prompt_matrix/i18n.py` | 693 | absence-only-empty | `No stored answer on this plan.` | "history.preview.empty": "No stored answer on this plan.", |
| `prompt_matrix/i18n.py` | 700 | absence-only-empty | `No keys found. Paste one below to connect Claude, Gemini, De` | "api.none": "No keys found. Paste one below to connect Claude, Gemini, DeepSeek, or Kimi.", |
| `prompt_matrix/i18n.py` | 750 | passive-wait | `Please wait` | "error.rate_limit": "Too many requests. Please wait a moment and try again.", |
| `prompt_matrix/i18n.py` | 787 | absence-only-empty | `No classes yet. Generate a prompt and save it, or learn one ` | "classes.empty": "No classes yet. Generate a prompt and save it, or learn one from a pasted example.", |
| `prompt_matrix/i18n.py` | 790 | absence-only-empty | `No saved prompts in this class yet.` | "classes.none": "No saved prompts in this class yet.", |
| `prompt_matrix/i18n.py` | 826 | absence-only-empty | `No unsupported citation claims flagged.` | "trust.stripped.none": "No unsupported citation claims flagged.", |
| `prompt_matrix/i18n.py` | 841 | absence-only-empty | `No previous work yet. Get an answer on Compose and it shows ` | "history.empty": "No previous work yet. Get an answer on Compose and it shows up here.", |
| `prompt_matrix/i18n.py` | 882 | absence-only-empty | `No runner that is closed to the internet (Ollama, vLLM, SGLa` | "route.pill.none": "No runner that is closed to the internet (Ollama, vLLM, SGLang, Llamafile).", |
| `prompt_matrix/i18n.py` | 884 | absence-only-empty | `No model can answer yet.` | "route.none": "No model can answer yet.", |
| `prompt_matrix/i18n.py` | 894 | absence-only-empty | `No provider connected.` | "status.none": "No provider connected.", |
| `prompt_matrix/i18n.py` | 927 | absence-only-empty | `No credit activity yet.` | "usage.none": "No credit activity yet.", |
| `prompt_matrix/i18n.py` | 1006 | absence-only-empty | `No warranty` | "terms.as_is.h": "No warranty", |
| `prompt_matrix/i18n.py` | 1042 | absence-only-empty | `No Drafting workspaces found.` | "projects.empty.filter_drafting": "No Drafting workspaces found.", |
| `prompt_matrix/i18n.py` | 1043 | absence-only-empty | `No Audited workspaces found.` | "projects.empty.filter_audited": "No Audited workspaces found.", |
| `prompt_matrix/i18n.py` | 1044 | absence-only-empty | `No Archived workspaces found.` | "projects.empty.filter_archived": "No Archived workspaces found.", |
| `prompt_matrix/i18n.py` | 1054 | absence-only-empty | `No locks yet` | "projects.no_locks": "No locks yet", |
| `prompt_matrix/i18n.py` | 1090 | absence-only-empty | `No locks yet` | "projects.vitals.locks_none": "No locks yet", |
| `prompt_matrix/i18n.py` | 1136 | absence-only-empty | `No claims yet.` | "generate.audit_manifest.empty": "No claims yet.", |
| `prompt_matrix/i18n.py` | 1155 | absence-only-empty | `No recent prompts yet.` | "generate.recent_empty": "No recent prompts yet.", |
| `prompt_matrix/i18n.py` | 1158 | vague-error | `Failed` | "generate.history_fail": "Failed", |
| `prompt_matrix/i18n.py` | 1180 | absence-only-empty | `No high-confidence locks inferred.` | "generate.locks_none": "No high-confidence locks inferred.", |
| `prompt_matrix/i18n.py` | 1221 | absence-only-empty | `No provenance recorded for this node yet.` | "jdf.provenance.empty": "No provenance recorded for this node yet.", |
| `prompt_matrix/i18n.py` | 1229 | absence-only-empty | `No audit executed. Risk signals and contradictions populate ` | "role.widget.risk_hint": "No audit executed. Risk signals and contradictions populate after Full Audit.", |
| `prompt_matrix/i18n.py` | 1251 | absence-only-empty | `No content yet.` | "vault.prompts.empty_content": "No content yet.", |
| `prompt_matrix/i18n.py` | 1275 | absence-only-empty | `No high-confidence metrics found.` | "adoption.locks.none": "No high-confidence metrics found.", |
| `prompt_matrix/i18n.py` | 1283 | absence-only-empty | `No source available` | "adoption.citation.unknown": "No source available", |
| `prompt_matrix/i18n.py` | 1289 | absence-only-empty | `No audit data yet.` | "adoption.audit.empty": "No audit data yet.", |
| `prompt_matrix/i18n.py` | 1365 | absence-only-empty | `No matching commands` | "palette.empty": "No matching commands", |
| `prompt_matrix/i18n.py` | 1407 | absence-only-empty | `No claims flagged.` | "trust.flagged.none": "No claims flagged.", |
| `prompt_matrix/i18n.py` | 1418 | absence-only-empty | `No file selected` | "canvas.no_file": "No file selected", |
| `prompt_matrix/i18n.py` | 1484 | absence-only-empty | `No decision memories for this project yet.` | "compliance.decision_log.empty": "No decision memories for this project yet.", |
| `prompt_matrix/i18n.py` | 1499 | absence-only-empty | `No compliance data yet.` | "analytics.table.empty": "No compliance data yet.", |
| `prompt_matrix/i18n.py` | 1506 | absence-only-empty | `No feedback yet.` | "backstage.feedback.empty": "No feedback yet.", |
| `prompt_matrix/i18n.py` | 1524 | absence-only-empty | `No prior revisions for this node.` | "jdf.node.history.empty": "No prior revisions for this node.", |
| `prompt_matrix/i18n.py` | 1599 | absence-only-empty | `No issues found.` | "jdf.redhat.none": "No issues found.", |
| `prompt_matrix/i18n.py` | 1680 | absence-only-empty | `No buscamos en la web ni extraemos páginas. Solo usamos lo q` | "privacy.web": "No buscamos en la web ni extraemos páginas. Solo usamos lo que escribes y subes.", |
| `prompt_matrix/i18n.py` | 1932 | absence-only-empty | `No hay nodos compilados.` | "landing.sandbox.node.empty": "No hay nodos compilados.", |
| `prompt_matrix/i18n.py` | 1990 | absence-only-empty | `No se sugirieron cambios gramaticales.` | "founder.polish.no_changes": "No se sugirieron cambios gramaticales.", |
| `prompt_matrix/i18n.py` | 1994 | absence-only-empty | `No se encontraron problemas verificables.` | "founder.scan.no_issues": "No se encontraron problemas verificables.", |
| `prompt_matrix/i18n.py` | 2004 | absence-only-empty | `No hay auditorías Red-Hat. Ejecuta Red-Hat en un borrador.` | "founder.runs.empty_redhat": "No hay auditorías Red-Hat. Ejecuta Red-Hat en un borrador.", |
| `prompt_matrix/i18n.py` | 2032 | absence-only-empty | `No se pudieron cargar las fuentes.` | "founder.sources.error": "No se pudieron cargar las fuentes.", |
| `prompt_matrix/i18n.py` | 2038 | absence-only-empty | `No se pudo cargar el historial de versiones.` | "founder.versions.error": "No se pudo cargar el historial de versiones.", |
| `prompt_matrix/i18n.py` | 2043 | absence-only-empty | `No hay hallazgos de auditoría abiertos.` | "founder.drawer.empty": "No hay hallazgos de auditoría abiertos.", |
| `prompt_matrix/i18n.py` | 2044 | absence-only-empty | `No hay hallazgos Red-Hat para esta ejecución.` | "founder.drawer.redhat_empty": "No hay hallazgos Red-Hat para esta ejecución.", |
| `prompt_matrix/i18n.py` | 2064 | absence-only-empty | `No se pudo leer el borrador.` | "founder.export.jdf_error": "No se pudo leer el borrador.", |
| `prompt_matrix/i18n.py` | 2065 | absence-only-empty | `No se pudo leer el borrador.` | "founder.export.pdf_error": "No se pudo leer el borrador.", |
| `prompt_matrix/i18n.py` | 2066 | absence-only-empty | `No se pudo guardar el borrador antes de exportar.` | "founder.export.save_error": "No se pudo guardar el borrador antes de exportar.", |
| `prompt_matrix/i18n.py` | 2095 | absence-only-empty | `No hay texto extraído para esta fuente.` | "evidence.inspector.no_text": "No hay texto extraído para esta fuente.", |
| `prompt_matrix/i18n.py` | 2096 | absence-only-empty | `No se pudo cargar el documento fuente.` | "evidence.inspector.load_error": "No se pudo cargar el documento fuente.", |
| `prompt_matrix/i18n.py` | 2097 | absence-only-empty | `No hay fuente vinculada a este bloqueo.` | "evidence.inspector.no_source": "No hay fuente vinculada a este bloqueo.", |
| `prompt_matrix/i18n.py` | 2127 | absence-only-empty | `No se han subido archivos. Arrastra y suelta o haz clic para` | "substrate.vault.empty": "No se han subido archivos. Arrastra y suelta o haz clic para subir.", |
| `prompt_matrix/i18n.py` | 2206 | absence-only-empty | `No hay claves. Pega una abajo para conectar Claude, Gemini, ` | "api.none": "No hay claves. Pega una abajo para conectar Claude, Gemini, DeepSeek o Kimi.", |
| `prompt_matrix/i18n.py` | 2240 | absence-only-empty | `No hay respuesta guardada en este plan.` | "history.preview.empty": "No hay respuesta guardada en este plan.", |
| `prompt_matrix/i18n.py` | 2285 | absence-only-empty | `No se pudo enviar el comentario. Inténtalo de nuevo.` | "tester.feedback.error": "No se pudo enviar el comentario. Inténtalo de nuevo.", |
| `prompt_matrix/i18n.py` | 2288 | absence-only-empty | `No encontramos esa página.` | "error.not_found": "No encontramos esa página.", |
| `prompt_matrix/i18n.py` | 2330 | absence-only-empty | `No hay un ejecutor cerrado a internet (Ollama, vLLM, SGLang,` | "route.pill.none": "No hay un ejecutor cerrado a internet (Ollama, vLLM, SGLang, Llamafile).", |
| `prompt_matrix/i18n.py` | 2337 | absence-only-empty | `No se marcaron afirmaciones de cita no respaldadas.` | "trust.stripped.none": "No se marcaron afirmaciones de cita no respaldadas.", |
| `prompt_matrix/i18n.py` | 2427 | absence-only-empty | `No se pudo leer el estado de la API` | "error.status": "No se pudo leer el estado de la API", |
| `prompt_matrix/i18n.py` | 2444 | absence-only-empty | `No se pudo comprobar esa sesión. Inicia sesión de nuevo.` | "auth.fail": "No se pudo comprobar esa sesión. Inicia sesión de nuevo.", |
| `prompt_matrix/i18n.py` | 2519 | absence-only-empty | `No puedes usar Assure para generar o promover:` | "terms.prohibit": "No puedes usar Assure para generar o promover:", |
| `prompt_matrix/i18n.py` | 2565 | absence-only-empty | `No se pudieron cargar los proyectos.` | "projects.load_failed": "No se pudieron cargar los proyectos.", |
| `prompt_matrix/i18n.py` | 2566 | absence-only-empty | `No se pudieron cargar los proyectos.` | "projects.failed": "No se pudieron cargar los proyectos.", |
| `prompt_matrix/i18n.py` | 2576 | absence-only-empty | `No se pudo eliminar el proyecto.` | "projects.delete_failed": "No se pudo eliminar el proyecto.", |
| `prompt_matrix/i18n.py` | 2577 | absence-only-empty | `No se pudo crear el proyecto.` | "projects.create_failed": "No se pudo crear el proyecto.", |
| `prompt_matrix/i18n.py` | 2663 | absence-only-empty | `No se infirieron bloqueos con alta confianza.` | "generate.locks_none": "No se infirieron bloqueos con alta confianza.", |
| `prompt_matrix/i18n.py` | 2775 | absence-only-empty | `No se encontraron métricas de alta confianza.` | "adoption.locks.none": "No se encontraron métricas de alta confianza.", |
| `prompt_matrix/i18n.py` | 2790 | absence-only-empty | `No se pudo cargar el manifiesto de auditoría.` | "adoption.audit.failed": "No se pudo cargar el manifiesto de auditoría.", |
| `prompt_matrix/i18n.py` | 2865 | absence-only-empty | `No hay coincidencias` | "palette.empty": "No hay coincidencias", |
| `prompt_matrix/i18n.py` | 3030 | absence-only-empty | `No hay revisiones anteriores para este nodo.` | "jdf.node.history.empty": "No hay revisiones anteriores para este nodo.", |
| `prompt_matrix/i18n.py` | 3031 | absence-only-empty | `No se pudo cargar el historial del nodo.` | "jdf.node.history.error": "No se pudo cargar el historial del nodo.", |
| `prompt_matrix/i18n.py` | 3081 | absence-only-empty | `No se encontraron problemas.` | "jdf.redhat.none": "No se encontraron problemas.", |
| `prompt_matrix/i18n.py` | 3113 | absence-only-empty | `No se pudo cargar el catálogo` | "error.catalog": "No se pudo cargar el catálogo", |
| `prompt_matrix/i18n.py` | 3114 | absence-only-empty | `No se pudieron cargar las clases` | "error.classes": "No se pudieron cargar las clases", |
| `prompt_matrix/i18n.py` | 5641 | non-action-dismiss | `OK` | "projects.vitals.redhat_clear": "Test de stress OK", |
