# Audit: Landing Privacy section

| Field | Value |
|---|---|
| Date | 2026-08-31 |
| Surfaces | `landing/index.html` `#privacy`, `landing/assets/site.css` |
| Standard | `docs/WEBPAGE_AUDIT_STANDARD.md` §A |
| Preflight | `prompt_matrix/i18n.py` privacy keys and in-app Privacy page. Live search for the mock’s news and % figures returned HTTP 401 this turn. |

## Summary

Added a Privacy band on the public landing. Copy matches in-app Privacy: keys and work stay on this computer; a Send still goes to the chosen provider. The pasted mock’s H1 (“never leaves”), percentage strip, jurisdiction-proof claims, $9/$29/$199, and Inter were not shipped.

## Honesty vs the pasted mock

| Pasted | Shipped |
|---|---|
| H1 “Your data never leaves your machine” | Unchanged product H1. PEM.md: that claim is a hard reject because Send does leave. |
| 44% / 77% / 40% plus 2026 news anecdotes | Omitted. Live search unavailable this turn (`Data not available in current context.`). |
| “No cloud. No sharing.” / “jurisdiction-proof” | Closed vs open. Send goes to Gemini, Claude, or whoever you picked. |
| Hero emoji badge strip | Omitted (composition gate). |
| Four priced plans | Free $0, Pro $5. |
| Gold “Why this matters now” | No amber labels on gray. |
| Inter | IBM Plex |

## Privacy tiles (from in-app copy)

1. Keys in `.env`. Assure does not store them.
2. Questions, uploads, previous work stay on this machine.
3. Closed to the internet vs open to the internet.
4. Sign-in stores email and subscription tier. Nothing else.

## Gates

| Gate | Result |
|------|--------|
| Composition | One new section. 2×2 grid. No stats strip in the first viewport. |
| Content honesty | Pass against `i18n.py` / Privacy page. |
| Cache bust | `?v=7` |

## Residual

Bright Data `search_engine` returned 401. Re-check the news anecdotes only if search is green and a source URL is in the result. Do not paste unverified percentages onto the landing.
