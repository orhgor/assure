# Audit: Final Jobs-style landing copy (legal-safe)

| Field | Value |
|---|---|
| Date | 2026-09-09 |
| Surfaces | `landing.html`, `i18n.py` (7 locales), `landing.css` |
| Standard | `docs/WEBPAGE_AUDIT_STANDARD.md` |
| Preflight | User final revision doc; privacy policy §8 disclaimer alignment |

## Summary

Applied final landing copy pass: hero certainty framing, risk-reduction (not antidote), server-processing footer disclosure, closed-mode chip tooltip, trust cards without legal overclaims, hero badge without zero-retention. All 7 locales updated.

## Gates

| Gate | Result | Notes |
|------|--------|-------|
| Composition | Pass | Hero + badge + H1 + sub + CTAs; no stats strip |
| Type & orphans | Pass | Prior `.text-line` glue retained; new hero html updated |
| Content honesty | Pass | No "court-defensible"; "reduces risk" aligns with §8 disclaimer |
| Legal safety | Pass | Footer restores "Processed on our servers"; closed mode explicit |
| Cache bust | Pass | `LANDING_CSS=57` |
| i18n | Pass | ES/ZH/FR/DE/JA/TR overrides for all changed keys |

## Reject list verified absent

- [x] `court-defensible` — removed from trust card 4 (all locales)
- [x] `zero retention` — not in marketing copy
- [x] Antidote / eliminates-risk overclaims — uses "reduces the risk"

## Changed keys (EN)

| Key | Final value (abbrev) |
|-----|----------------------|
| `brand.hero_title` | Draft at the speed of AI… |
| `landing.hero.subtitle` | Plausible AI is a liability… reduces the risk… |
| `landing.hero.badge` | Enterprise ready. BYOK. 7 languages. |
| `landing.why.title` | …Certainty is a competitive advantage. |
| `landing.trust.card4` | Audit-Ready Export / complete audit dossier |
| `landing.footer.engineer_tagline` | Secure by design. Processed on our servers… |
| `privacy.chip.tip.closed` | …processed on Assure's secure servers… |

## Viewports checked

- [ ] 390 (deferred — copy-only)
- [ ] 768 (deferred)
- [ ] 1080 (deferred)
- [ ] 1440 (deferred)

## Deploy

Static marketing rebuilt and uploaded to R2 (`assure-marketing-prod`) post-audit.
