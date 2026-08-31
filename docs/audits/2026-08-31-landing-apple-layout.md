# Audit: Assure landing, Apple-layout pass (`landing/`)

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Surfaces | `landing/index.html`, `landing/404.html`, `landing/assets/site.css` |
| Standard | `docs/WEBPAGE_AUDIT_STANDARD.md` §A |
| Cache | `?v=5` then Jobs copy pass; public CSS now `?v=6` with `audit.html` |

## What changed

The public page now uses the centered, numbered-step composition from the design HTML (hero H1, 3 steps, 6 features, 2 plans, install). Brand stays **Assure AI**. Font stays **IBM Plex**, not Inter.

## Honesty vs the pasted mock

| Pasted | Shipped |
|--------|---------|
| Inter | IBM Plex Sans / Mono |
| Pro $9, Team $29, Self-hosted $199 | Pro **$5**. Team / Self-hosted named in a note, not priced |
| Emoji trust badges in the first viewport | Removed |
| “Data never leaves” / “no cloud” | Footer lock: stays on your machine. Send only to chosen provider |
| `pip install prompt-matrix` + fake GitHub | `assure --web` |
| 4-plan grid | Free + Pro (in-app prices). 2-up at 768, no 2+1 orphan |

## Gates

| Gate | Result |
|------|--------|
| Composition | Centered hero. Header brand. One H1. One support. One CTA group. No promo chip strip. |
| Type | IBM Plex. Orphans glued with `&nbsp;`. `text-wrap: pretty` / `balance`. |
| Pricing | $0 / $5 from in-app copy. |
| Cache bust | `site.css?v=4`, `site.js?v=4`. |
