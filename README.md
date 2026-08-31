# getassure.com

This GitHub repository is the public webpage only. The Assure workbench is not in this repo. It runs on the machine (`assure --web`).

ICP and objections: `ICP.md`, `objections.md`. Use cases: `use-cases/`. Pricing: `pricing.html`. Terms: `terms.html`. Launch drafts: `launch/`.

## Cloudflare

GitHub default branch is `webpage`. Cloudflare Worker `assure` deploys with `npx wrangler deploy` (assets only, no build). First connect failed because `wrangler.jsonc` still looked like a Pages project.

- Production branch: `webpage`
- Root directory: `/`
- Build command: empty
- Deploy command: `npx wrangler deploy`
- Custom domain: `getassure.com` (attach on the Worker, not on `main`)
