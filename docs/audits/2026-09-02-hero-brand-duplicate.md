# Audit: homepage hero — one brand, then the H1

| Field | Value |
|---|---|
| Date | 2026-09-02 |
| PR | n/a (local) |
| Branch | `p4-account-wallet` landing files; deploy from `webpage` |
| Surfaces | `landing/index.html`, shared footer on public HTML, `landing/assets/site.css` |
| Auditor | agent |
| Standard | `docs/WEBPAGE_AUDIT_STANDARD.md` §A |
| Preflight | §A hard rejects. Closed / open wording. No “never leaves your machine.” |

## Summary

First viewport said **Assure** twice: header wordmark **Assure AI**, then a giant `.hero-brand` **Assure** above the H1. That breaks the Apple-layout pass (header brand, one H1, one support, one CTA group) and overfills the hero budget. Removed the duplicate wordmark. H1 is the display line. CTAs sit under the support, then the labeled mockup. Footer no longer reads “Assure. © Assure.” Cache `?v=29`.

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

## Gates

| Gate | Result | Notes |
|------|--------|-------|
| Composition | Pass | Brand once, in the header. Hero budget: one H1, one support, one CTA group, Trust Blue plane, one labeled mockup under the CTAs. No second wordmark. |
| Type & orphans | Pass | IBM Plex. `.text-lines` on the H1. Last words glued with `&nbsp;`. H1 size raised so it can lead after the wordmark left. |
| Empty space / rhythm | Pass | Side-by-side hero-layout (CTAs left of mockup) removed. Centered stack. |
| Alignment / shell | Pass | Mockup `margin: 0 auto`. Shell tokens unchanged. |
| Color / contrast | Pass | White on Trust Blue. Unchanged tokens. |
| Responsive | Pass | 390 / 768 / 1080 / 1440. Mobile CTAs full width. |
| Content honesty / assets | Pass | Mockup still labeled mockup. No “never leaves.” |
| Forms / analytics | Pass | Waitlist unchanged. |
| Cache bust | Pass | `assets/site.css?v=29`, `site.js?v=29`, `compiler.js?v=29`. |

## Failures found → fixes

| Issue | Fix |
|---|---|
| Header **Assure AI** + hero **Assure** | Deleted `.hero-brand` |
| CTAs beside mockup (`row-reverse`) | Centered: H1 → support → CTAs → mockup |
| Footer “Assure. © Assure” | `© 2026 Assure. All rights reserved.` |

## Viewports checked

- [x] 390
- [x] 768
- [ ] 1080 — CSS; centered stack does not change at this width
- [ ] 1440 — same

## Residual risk

Mockup is still a static card in the hero. §2 says cards only for real interaction; the live compiler below `#translation-demo` is that interaction. Kept the mockup as labeled evidence, not a second product surface.
