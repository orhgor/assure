# getassureai.com

This GitHub repository is the public webpage only. The Assure workbench is not in this repo. It runs on the machine (`assure --web`).

ICP and objections: `ICP.md`, `objections.md`. Use cases: `use-cases/`. Pricing: `pricing.html`. Privacy: `/privacy`. Install: `/install`. About: `/about`. Terms: `/terms`. Launch drafts: `launch/`.

## Cloudflare

GitHub default branch is `webpage`. Cloudflare Worker `assure` deploys with `npx wrangler deploy`. Static files plus `POST /api/waitlist` (`worker/index.js`). First connect failed because `wrangler.jsonc` still looked like a Pages project.

- Production branch: `webpage`
- Root directory: `/`
- Build command: empty
- Deploy command: `npx wrangler deploy`
- Waitlist secrets: `SUPABASE_URL` and `SUPABASE_SECRET_KEY` via `wrangler secret put` (never in git)
- Custom domain: `getassureai.com` (HTTPS 200 as of 2026-09-01)
