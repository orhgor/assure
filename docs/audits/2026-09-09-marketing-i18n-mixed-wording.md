# Audit: Marketing locale mixed wording

| Field | Value |
|-------|-------|
| Date | 2026-09-09 |
| Surfaces | `landing.html` × 7 locales (static export) |
| Standard | i18n-align — no `**EN` fallbacks on visible marketing strings |

## Finding

Built `dist/` for all locales. Non-English home pages showed **English H2 + pricing lead** amid otherwise translated copy:

| Key | Visible EN (before) | Locales |
|-----|---------------------|---------|
| `landing.trust.title` | Enterprise trust architecture | es zh fr de ja tr |
| `landing.plans.lead` | You bring your own keys… | es zh fr de ja tr |

Privacy pages (`/privacy`) use locale bodies in `prompt_matrix/content/privacy/*.html` — **pass**.

## Fix

Added translations for `landing.trust.title` and `landing.plans.lead` in all six non-EN catalogs in `i18n.py`. Bumped `LANDING_JS=44`.

## Re-verify (after R2 deploy)

```bash
DIST_DIR=/tmp/dist-audit bash scripts/build-marketing-static.sh
# tr/index.html visible body: Kurumsal güven mimarisi; no "Enterprise trust architecture"
bash scripts/deploy-marketing-r2.sh
```

## Deferred

Legacy catalog keys (`landing.compare.*`, `landing.competitor.*`) still English in JSON embed — not rendered on live `landing.html`.
