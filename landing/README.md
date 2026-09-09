# getassureai.com (canonical)

**Master reference:** [docs/webpage-all-content.md](../docs/webpage-all-content.md) — all routes, live vs static stacks, copy sources.

Public Assure AI is served **directly from EC2** through Cloudflare Tunnel:

- **https://getassureai.com/** — landing + paste sandbox
- **https://getassureai.com/app** — JDF / Z3 workspace

Legacy **app.getassureai.com** and **www** 301 to the apex (Flask + tunnel).

## DNS / tunnel (one-time)

```bash
bash scripts/aws/route-apex-dns.sh          # Mac: cloudflared login required
bash scripts/aws/apply-tunnel-config.sh     # SSM: push ingress + restart cloudflared on EC2
```

Remove Worker custom domains for `getassureai.com` in Cloudflare if they still point at the old static site.

Deploy this folder to Workers **only** for `assure.orhangorenn.workers.dev` (optional redirect):

```bash
npx wrangler deploy
```
