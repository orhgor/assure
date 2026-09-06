# Cloudflare cache rules — Assure app (v2.0 Sprint 1)

Apply in **Cloudflare Dashboard → Caching → Cache Rules** (zone: `getassureai.com`).

## Rule 1 — Versioned static assets (long TTL)

**When:** URI Path starts with `/static/` AND query string contains `v=`
**Then:** Cache eligibility = Eligible, Edge TTL = 7 days, Browser TTL = 1 day
**Cache key:** Include query string (`v=assure-97`)

Rationale: `style.css?v=assure-97` and bundled JS change only on release.

## Rule 2 — Unversioned static (short TTL)

**When:** URI Path starts with `/static/`
**Then:** Edge TTL = 1 hour, Browser TTL = 5 minutes

## Rule 3 — Health (never cache)

**When:** URI Path equals `/health` OR `/api/health`
**Then:** Bypass cache

## Rule 4 — API (never cache)

**When:** URI Path starts with `/api/`
**Then:** Bypass cache

## Rule 5 — HTML shell (short edge cache)

**When:** URI Path equals `/workbench` OR starts with `/projects/`
**Then:** Edge TTL = 60 seconds, Browser TTL = 0 (respect origin `Cache-Control`)

Origin headers (Flask):

- HTML workbench: `Cache-Control: no-store` (production)
- Landing: `Cache-Control: public, max-age=300`
- `/static/*`: set via `after_request` hook with `public, max-age=86400, immutable` when `?v=` present

## Verification

```bash
curl -sI "https://getassureai.com/static/style.css?v=assure-97" | grep -i cache
curl -sI "https://getassureai.com/health" | grep -i cache
```

Expect `CF-Cache-Status: HIT` on versioned static after second request; `DYNAMIC` or `BYPASS` on `/health`.
