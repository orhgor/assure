# Audit: Assure landing grounded-copy, audit-fix pass

| Field | Value |
|---|---|
| Date | 2026-09-01 |
| PR | [#1](https://github.com/orhgor/assure/pull/1) (product). Public site ships on `webpage`. |
| Branch | `p4-account-wallet` landing files; deploy from `webpage` |
| Surfaces | `landing/index.html` and shared chrome on pricing, terms, check outputs, use-cases, audit, 404 |
| Auditor | agent |
| Standard | `docs/WEBPAGE_AUDIT_STANDARD.md` §A |
| Preflight | §A hard rejects. Pricing 10 / $5 / 100. Closed / open wording. |

## Summary

Audit Failures from the grounded-copy rewrite are fixed on this tree: Trust Blue full-bleed masthead, no first-viewport promo chip, no how-it-works or audience card/pill chrome, `.text-lines` on the H1, shell tokens match §A, honesty copy no longer claims data never leaves. Cache `?v=25`. `POST /api/waitlist` on the Worker uses same-origin on production.

### Webpage audit

Standard: `docs/WEBPAGE_AUDIT_STANDARD.md`

| Gate | Result |
|------|--------|
| Composition | Pass |
| Type & orphans | Pass |
| Empty space / rhythm | Pass |
| Alignment / shell | Pass |
| Color / contrast | Pass |
| Responsive (profile viewports) | Pass |
| Content honesty / assets | Pass |
| Forms / analytics | Pass |
| Cache bust | Pass |

Fixes landed in: this landing pass (deployed to `webpage` / Worker `assure`).

## Gates

| Gate | Result | Notes |
|------|--------|-------|
| Composition | Pass | Masthead is Trust Blue. Brand wordmark is larger than the H1. Budget is brand, one H1, one support, one CTA group. No `$5/mo` chip. How-steps and prompt preview have no card chrome. Audience is a text link list. |
| Type & orphans | Pass | IBM Plex Sans. `.text-lines` on H1, how H2, audience H2, privacy H2. At 1440 H1 is two intentional lines. Last words glued with `&nbsp;`. |
| Empty space / rhythm | Pass | Shared section spacing. Hero is a solid plane, not leftover gradient air. |
| Alignment / shell | Pass | `--shell-pad-x: 3rem` (1.25rem below 768). `--prose-max: 760px`. No horizontal overflow at 390 or 1440. |
| Color / contrast | Pass | White on Trust Blue. Body muted on white. `:focus-visible` rings. Primary CTAs 52px. |
| Responsive | Pass | 390 / 768 / 1080 / 1440 via CDP. Nav stays available. Waitlist modal opens; Full name focused. |
| Content honesty / assets | Pass | Footer: “Copy stays on this computer” / “A Send goes only to the provider you chose.” Privacy H2 is closed or open. No Local-first. No waitlist emoji. Pricing 10 / $5 / 100. |
| Forms / analytics | Pass | Localhost posts to Flask. Production posts to same-origin `/api/waitlist`. No Formspree. |
| Cache bust | Pass | `assets/site.css?v=25` and `assets/site.js?v=25` on every public HTML file. |

## Failures found → fixes

| Issue | Fix |
|---|---|
| Footer / privacy H2 / Local-first | Honest closed/open copy on all landing HTML |
| Hero `$5/mo` promo chip | Removed |
| How-step cards; audience pills; prompt cards | Editorial steps, text links, no card chrome |
| Shell pad 1.5rem / prose 600px | 3rem / 760px |
| H1 accidental wraps | `.text-lines` |
| Live waitlist POST to 127.0.0.1 | Same-origin Worker `POST /api/waitlist` |

## Viewports checked

- [x] 390 — pad 1.25rem; Trust Blue; no overflow
- [x] 768 — CSS stack
- [x] 1080 — steps 3-up
- [x] 1440 — pad 3rem; H1 two lines; CTAs 52px

## Residual risk

- Screenshot crops from the browser tool can clip the right edge; overflow was measured with `scrollWidth`.
- Waitlist on production needs `SUPABASE_URL` and `SUPABASE_SECRET_KEY` as Wrangler secrets. Missing secrets return 503, not a silent localhost POST.
- GitHub `webpage` and `npx wrangler deploy` must stay in sync or a later Git build can overwrite a direct deploy.
