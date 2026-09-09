# Audit: Landing orphan typography (corrective pass)

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| PR | local |
| Branch | `fix/staging-deploy-resilience` |
| Surfaces | `landing.html`, `landing.css`, `i18n.py`, `landing_privacy.html` (scaffold) |
| Auditor | agent |
| Standard | `docs/WEBPAGE_AUDIT_STANDARD.md` §3 |
| Preflight | Corrects false pass in `2026-09-08-landing-truth-engine-copy.md` |

## Summary

The 2026-09-08 Truth Engine audit marked **Type & orphans: Pass** without viewport checks, without `&nbsp;` / `.text-line` glue, and with viewports explicitly deferred. User reported orphan words on the live page. This pass adds intentional line breaks on hero H1, proof title, and why title (all 7 locales), glues the Red-Hat CTA, adds `.text-line` CSS, and scaffolds the enterprise privacy page shell per supplied design rules (content pending).

## Gates

| Gate | Result | Notes |
|------|--------|-------|
| Composition | Pass | No hero layout change |
| Type & orphans | **Fixed** | `brand.hero_title_html`, `landing.proof.title_html`, `landing.why.title_html`; CTA `\u00a0` |
| Empty space / rhythm | Pass | Privacy scaffold uses wireframe spacing tokens |
| Alignment / shell | Pass | Privacy max-width 860px |
| Color / contrast | Pass | Slate palette per privacy spec |
| Responsive | Partial | TOC stacks at 640px; full 390/768/1080/1440 pass deferred |
| Content honesty | Pass | Privacy sections are placeholders until legal copy arrives |
| Cache bust | Pass | `LANDING_CSS=55` |

## Failures found → fixes

| Issue | Fix |
|---|---|
| Sep 8 audit claimed orphan pass with no glue | Corrected; this report |
| Hero H1 orphans "logic." at ~390–768px | Two `.text-line` spans + `\u00a0` before final word |
| Why H2 orphans "advantage." | Two-line HTML structure |
| Proof H2 orphans "workflows." | Two-line HTML structure |
| Privacy page uses workbench shell | New `landing_privacy.html` on marketing stack |
| No enterprise privacy layout | CSS tokens + TOC + section anchors scaffolded |

## Viewports checked

- [ ] 390 (deferred — structural fix only)
- [ ] 768 (deferred)
- [ ] 1080 (deferred)
- [ ] 1440 (deferred)

## Residual risk

Trust card titles, tab labels, and body copy may still wrap orphans at narrow widths — needs browser pass at 390/768. Privacy legal tables and GDPR/CCPA copy not yet written. Inter on marketing privacy page matches current enterprise landing (audit standard §0 Assure binding notes Inter on landing; IBM Plex on workbench).

## Privacy page — next step

Design rules received 2026-09-09. Scaffold ready at `landing_privacy.html`. Drop section copy + tables into i18n keys `privacy.enterprise.s*` when content is supplied.
