# Bot / edge policy audit — assureproof.com

Current-state audit of the Cloudflare edge policy for the marketing site.
Every "verified" statement below is backed by code in
`marketing/cloudflare/marketing-worker.js` and by a live `curl` check from
the final close-out session (2026-11-23, `main` @ `f4df31b` or later).
Regression tests live in `tests/test_cloudflare_policy.py` and
`tests/test_closeout_gates.py`; the deploy workflow re-verifies the live
behavior after every deploy.

## What is enforced in code (verified)

| Policy | Implementation | Verified live |
| --- | --- | --- |
| Legacy-domain consolidation | `getassureai.com` and `www.getassureai.com` 301 to `https://assureproof.com`, path and query kept, http upgraded in the same hop, before the www hop and the generic http→https rewrite | `getassureai.com/` → `301 https://assureproof.com/`; `getassureai.com/poc/` keeps the path; `http://getassureai.com/de/poc/` → `https://assureproof.com/de/poc/` |
| www canonicalization | `www.assureproof.com` → apex, path and query kept | 301 observed |
| Always HTTPS | `http://` → `https://`, path and query kept | 301 observed |
| Trust-center move | `/security` and `/security/` (with optional locale prefix) 301 to the matching `/trust/` route; the rule precedes the legacy `/app`/`/api` fallback and the static-asset 404 fallthrough | `/security/` → `301 /trust/`; `/de/security/` → `301 /de/trust/` |
| Legacy paths | `/contact`, `/government`, `/app`, `/api` (and `/use-cases/fuel-retail`) 301 to their current routes | 301s observed |
| Endpoint protection | `POST /contact-submit` (Resend) and `POST /api/z3-demo` are dispatched before the legacy `/app`//`/api` GET fallback; per-IP fixed-window limits (5/10 min, 30/min), `Retry-After` on 429, `company_website` honeypot accepted-and-dropped | 400 on invalid JSON, 200 on honeypot POST, 200 on valid z3-demo |

## Email delivery (verified — fixed 2026-11-23)

Contact submissions are delivered by the **Resend API** from the Worker:

- `from`: `AssureProof Website <website@getassureai.com>`
- `to`: `sales@getassureai.com`
- The verified sending domain in Resend is **getassureai.com**
  (owner-confirmed; it receives and sends).

**Incident record:** since the 2026-11-15 R2→Workers cutover the worker used
`website@assureproof.com` / `sales@assureproof.com`. `assureproof.com` is
**not verified** in Resend, so every form submission was rejected — a live
probe on 2026-11-23 returned `502` with Resend's error: *"The assureproof.com
domain is not verified."* The addresses were restored to the verified
getassureai.com domain in the same PR;
`test_worker_email_uses_the_verified_resend_domain` now fails the build if
the addresses drift off the verified domain again.

**Public contact address (owner decision, 2026-11-23):** the working inbox
`sales@getassureai.com` is also the published contact address — it appears
on `/contact-us/` ("Prefer email?"), in the contact/PoC error copy, in the
PoC FAQ (`faq.10.a`) and in `security.txt` (`Contact:
mailto:sales@getassureai.com`). `assureproof.com` is intentionally **not** a
sending domain — the owner keeps the primary domain out of send
authorization for security (tighter SPF/DMARC posture) — so do not "fix"
deliverability by verifying assureproof.com in Resend; sending stays on the
verified getassureai.com domain.
| Security headers | CSP (`frame-ancestors 'none'`, no `unsafe-inline` in script-src), HSTS, X-Frame-Options DENY, nosniff, Referrer-Policy, Permissions-Policy on every response | header checks pass post-deploy |

## Discovery files (verified)

- `/.well-known/security.txt` — **200**, fields `Contact:` (mailto),
  `Expires:`, `Canonical:`, `Preferred-Languages:`. This file previously
  returned 404 before the security.txt work shipped; it is now published
  from `marketing/security.txt` into `dist/.well-known/` on every build,
  and `test_security_txt_expires_is_valid_future_date` fails the build if
  `Expires` is not an aware UTC timestamp at least 30 days in the future
  (and within a year), so it cannot silently expire.
- `/robots.txt` — allows search crawlers, points at the sitemap, contains
  no `Disallow` directives. AI-crawler stance is **[CONFIRM]** (owner
  decision pending); do not add directives until it is answered.
- `/llms.txt` — generated at build time from `marketing/llms.txt` prose +
  shared `en.json` facts + the key-pages list; parity is gated by tests.

## Dashboard-only items — [UNREADABLE / CONFIRM IN DASHBOARD]

These cannot be read from code or asserted by CI; confirm each in the
Cloudflare dashboard (Zone assureproof.com → Security) and record the
result here:

- **Bot Fight Mode** — [UNREADABLE / CONFIRM IN DASHBOARD]. Expected off
  unless the owner says otherwise: it can challenge legitimate API/AI
  agents and has no per-route exemption.
- **AI Crawl Control (ai bots blocking)** — [UNREADABLE / CONFIRM IN
  DASHBOARD]. Stance decision is the same pending **[CONFIRM]** as the
  robots.txt AI directives above.
- **WAF rate-limiting rule** for the POST endpoints — [UNREADABLE /
  CONFIRM IN DASHBOARD]. The in-worker limiter is per-isolate and
  best-effort only (resets on deploy, counted per PoP); a durable
  domain-wide limit belongs in a WAF rule. Live probe of the in-worker
  limiter (32 rapid requests) returned 200s throughout, which is expected
  behavior for the per-isolate counter, not evidence of a WAF rule.
- **Zone-level redirect rule for getassureai.com** — [UNREADABLE / CONFIRM
  IN DASHBOARD]. The Worker rule is authoritative and verified; the
  dashboard rule (if present) mirrors it and should be kept consistent.

## How to re-verify after any deploy

```bash
curl -sSI https://assureproof.com/security/ | grep -i '^location'   # /trust/
curl -sSI https://assureproof.com/de/security/ | grep -i '^location' # /de/trust/
curl -sS  https://assureproof.com/.well-known/security.txt
curl -sS  https://assureproof.com/robots.txt
curl -sS  https://assureproof.com/llms.txt | head -5
curl -s  -o /dev/null -w '%{http_code} %{redirect_url}\n' https://getassureai.com/poc/
```
