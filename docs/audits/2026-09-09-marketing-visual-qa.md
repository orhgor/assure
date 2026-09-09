# Marketing visual QA checklist — 2026-09-09

Stack A source: `prompt_matrix/static/landing.css` · cache `LANDING_CSS=59`, `LANDING_JS=45`

## Applied in CSS (this pass)

| Check | Target | Status |
|-------|--------|--------|
| Hero padding-top | 96px (`--space-12`) | ✅ |
| H1 → subheadline | 24px (`--space-3`) | ✅ |
| Subheadline → CTAs | 48px (`--space-6`) | ✅ |
| CTA → next section | ≤160px (`--space-20` hero padding-bottom) | ✅ |
| CTA balance | Equal flex width, 44px min-height, hover scale + shadow | ✅ |
| Trust cards | `auto-fit minmax(280px)`, hover lift, `font-weight: 600` titles | ✅ |
| One Engine typography | H2 2.25rem, line-height 1.2, rule margin 24px | ✅ |
| Footer | `#f8fafc` background, visible disclaimer, back-to-top link | ✅ |
| Language switcher | Visible label + bold select | ✅ |
| Waitlist modal | 32–48px padding, 44px close, success check animation | ✅ |

## Verify after build

```bash
bash scripts/build-marketing-static.sh
# Open dist/index.html at 390 / 768 / 1080 / 1440
bash scripts/deploy-marketing-r2.sh --dry-run
```

| Viewport | Focus |
|----------|-------|
| 390px | Hero CTAs stack; trust cards single column |
| 768px | Trust cards 2-col; nav menu toggle |
| 1080px | Trust cards 4-col; CTA row balanced |
| 1440px | Max-width containers centered; no excess hero whitespace |

## Deploy sequence (with runbook)

1. Phase 1 — Staging EC2 disk + redeploy
2. Phase 2 — Delete `landing/` (Stack B)
3. Phase 3 — This visual pass (done locally)
4. Phase 4 — Breakpoint QA + locale spot-check
5. Phase 5 — Workbench `assure-127` on staging
6. Phase 6 — R2 deploy + audit log
