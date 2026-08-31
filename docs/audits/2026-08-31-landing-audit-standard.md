# Audit: Assure landing (`landing/`)

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| PR | n/a (local) |
| Branch | current workspace |
| Surfaces | `landing/index.html`, `landing/404.html` |
| Auditor | agent |
| Standard | `docs/WEBPAGE_AUDIT_STANDARD.md` (portable file from SeroState PR #148, §A bound to Assure) |
| Preflight | Claims from `prompt_matrix/i18n.py` / in-app Pricing and Privacy. PEM MCP was unavailable this session. |

## Summary

First landing draft failed composition (inset fake Compose widget, gradient field, card chrome in the hero), honesty (toy UI), contrast (gold labels on white), and ops leakage (`data-app-origin` in a section lead). This pass rebuilds the public page to the Assure profile: Trust Blue full-bleed hero, brand-first type, no cards except Decision pricing, IBM Plex, 1080px shell.

## Gates

| Gate | Result | Notes |
|------|--------|-------|
| Composition | Pass | One hero composition. Mark + Assure are larger than the H1. Budget is brand, H1, one lede, one CTA group, solid Trust Blue plane. Fake product mock removed. |
| Type & orphans | Pass | IBM Plex Sans. `.text-lines` on hero H1 and how H2. Last words glued with `&nbsp;` on headings, CTAs, and leads. `text-wrap: pretty` / `balance`. |
| Empty space / rhythm | Pass | Shared `--spacing-16` section padding. Hero fills leftover viewport on large screens; no unused two-column hole. |
| Alignment / shell | Pass | `--shell-max: 1080px`, `--shell-pad-x: 3rem` (1.25rem below 768). Prose 760px on privacy/about. 3-up only from 1080px so no 2+1 orphan tile. |
| Color / contrast | Pass | Tokens only. Eyebrows moved from gold to Trust Blue (gold on white fails AA at label size). Body muted `#4a5a6a`. Focus rings on light and navy. |
| Responsive | Pass (CSS) | 390 / 768 stack; 1080 / 1440 equal tracks. Nav links stay available (no hide-on-mobile). Buttons `min-height: 44px`. |
| Content honesty / assets | Pass | No demo widget. Pricing 10 / $5 / 100 from in-app copy. No “never leaves your machine.” Terminal is the real `assure --web` command. |
| Forms / analytics | N/A | No marketing form. No analytics injector on this static page. |
| Cache bust | Pass | `assets/site.css?v=2`, `assets/site.js?v=2`. |

## Failures found → fixes

| Issue | Fix |
|---|---|
| Inset Compose mock in the hero (toy surface, card, overlay chip) | Removed. Solid Trust Blue plane is the visual. |
| H1 overpowered the brand; extra hero note and localhost privacy link | Brand-first hero type; implementer copy out of leads. |
| Gold uppercase labels on white | Trust Blue eyebrows. |
| 3-column cards wrapping to 2+1 | No card chrome. 3-up only at `min-width: 1080px`. |
| Tap targets under 44px; nav links hidden at 640px | `min-height: 44px`; links remain in the wrap. |

## Viewports checked

- [ ] 390 — CSS media (`max-width: 767px`); not screenshot in a browser this turn
- [ ] 768 — CSS; not screenshot
- [ ] 1080 — CSS `min-width: 1080px` 3-up / 2-up; not screenshot
- [ ] 1440 — same as 1080 shell; not screenshot

## Residual risk

No browser MCP in this session, so orphans and first-viewport crop were judged from CSS and copy, not measured pixels. After Cloudflare is attached, re-check the four widths live and bump `?v=` if type wraps. Product UI at `127.0.0.1:8765` was not re-audited against this pass.
