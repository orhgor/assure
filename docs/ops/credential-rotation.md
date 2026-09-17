# Credential rotation runbook

Plan only. Nothing in this document is executed; it was produced from a read-only
inventory on 2026-09-17 (staging `i-03e39eccc57572191`, prod `i-09d0ad0b561113abe`).

Naming: **OMP** = the Open Memory Protocol memory server (`omp.service`, port 3456).
Not Pi, the coding agent.

Provenance of every claim here:

- `ls -la /home/ubuntu/assure-prototype/.env*` → `.env.staging` (1727 B, mode `600`, Sep 17 05:47) and `.env.staging.bak` (1729 B, mode `600`, Sep 17 05:20)
- `sudo systemctl cat assure-prototype.service | grep -iE "EnvironmentFile|Environment="` → `Environment=ASSURE_ENV=staging` and a `PATH` line only — **no `EnvironmentFile=`**
- `prompt_matrix/keys.py:59-78` → `load_dotenv(ENV_PATH, override=False)` at `:60`, then with `ASSURE_ENV` set (`:62`) it loads `repo_root / ".env.staging"` at `:73` via `load_dotenv(f, override=True)` at `:75`
- `sudo systemctl cat omp.service | grep -iE "Environment"` → `Environment=OMP_API_KEY=<redacted>`, `OMP_DB_PATH=/home/ubuntu/.omp/omp.db`, `OMP_PORT=3456`
- `sudo docker inspect assure-assure-app-1 --format '{{range .Config.Env}}…'` (prod) → keys arrive as container env
- `systemctl is-active cloudflared` → `active`; `grep -cE "credentials-file|tunnel:" /etc/cloudflared/config.yml` → `2`

## Keys, providers, and blast radius

| Key | Provider | Loaded by | Reload = restart? | Blast radius |
|---|---|---|---|---|
| `DEEPSEEK_API_KEY` | DeepSeek | staging: `.env.staging` → `keys.py:59-78`; prod: container env | **Yes** — read at import, no per-request reload | every compile fails |
| `OPENROUTER_API_KEY` | OpenRouter | staging `.env.staging` (`keys.py:73-75`); **absent from the prod container env** (docker inspect list has no `OPENROUTER_API_KEY`) | Yes (staging) | routed model unavailable |
| `ANTHROPIC_API_KEY` | Anthropic | staging `.env.staging`; prod container env; `CLAUDE_API_KEY` is an accepted alias (`llm/orchestrator.py:113`, `orchestrator_routes.py:51`) | Yes | alternate routed model unavailable |
| `OMP_API_KEY` | self-hosted OMP (`omp-server` 0.2.0) | staging: `.env.staging` **and** inline `Environment=` in `omp.service`; prod: container env + `omp.service` | **Yes, and `systemctl daemon-reload`** for the inline unit value | search returns nothing |
| `SUBSTRATE_INGEST_SECRET` | self-hosted (shared secret) | staging `.env.staging`; prod container env; **also in `.env.staging.bak`** | Yes | uploads rejected; shared across prod, staging and both laptops — rotate everywhere in one window |
| `CLOUDFLARE_TUNNEL_TOKEN` | Cloudflare | staging `.env.staging`; prod container env; written into env files by `scripts/aws/setup-tunnel.sh:69-76` and consumed by `scripts/aws/remote-bootstrap.sh:26,52` | Yes + `cloudflared` reload/restart | all three hostnames go dark |
| `RESEND_API_KEY` | Resend | staging `.env.staging` (`feedback_routes.py:29,48`); **absent from the prod container env** | Yes | email only |
| `GROQ_API_KEY` | Groq | staging `.env.staging` (`orchestrator_routes.py:53`); **absent from the prod container env** | Yes | free-model routing only |

Note on prod: the container env list from `docker inspect` contains `ANTHROPIC_API_KEY`, `DEEPSEEK_API_KEY`, `CLOUDFLARE_TUNNEL_TOKEN`, `OMP_API_KEY`, `SUBSTRATE_INGEST_SECRET`, `GEMINI_API_KEY`/`GOOGLE_API_KEY`, `STRIPE_WEBHOOK_SECRET`, `GHCR_TOKEN`, `SENTRY_*` — and does **not** contain `OPENROUTER_API_KEY`, `RESEND_API_KEY` or `GROQ_API_KEY`. Verify before planning any prod-side rotation of those three.

## Rotation order

Rotate in order of increasing blast radius. Each rotation is one window:
**generate → update env on every box that uses it → restart the affected service → verify → revoke the old value.**

1. `RESEND_API_KEY` — email, lowest blast
2. `GROQ_API_KEY` — routing fallback
3. `ANTHROPIC_API_KEY` — single model path
4. `OPENROUTER_API_KEY` — primary routing
5. `DEEPSEEK_API_KEY` — all compiles
6. `OMP_API_KEY` — search
7. `SUBSTRATE_INGEST_SECRET` — uploads; must update every consumer
8. `CLOUDFLARE_TUNNEL_TOKEN` — all hostnames; do this alone, with no other rotation the same day

**Never rotate more than one key per window. Never rotate the tunnel token and the OMP key the same day.**

Staging carries a second copy of the whole variable set: `/home/ubuntu/assure-prototype/.env.staging.bak`
(1729 B, mode `600`, same 25 variable names as `.env.staging`). Whatever happens to a key in
`.env.staging`, the `.bak` still holds the old value — rotate it in the same window or delete it,
otherwise a rollback silently restores a revoked credential.

## The commands, per key

Each verifies the key **after** rotation. These are the exact checks from the read-only pass on
2026-09-17, which returned `200` for every provider listed.

```bash
# run on staging as ubuntu; sources the env in a subshell and prints only statuses
sudo -u ubuntu -H bash -c '
  set -a; for f in /home/ubuntu/assure-prototype/.env.staging; do [ -f "$f" ] && . "$f"; done; set +a
  printf "anthropic:  "; curl -s -o /dev/null -w "%{http_code}\n" -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" https://api.anthropic.com/v1/models
  printf "openrouter: "; curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $OPENROUTER_API_KEY" https://openrouter.ai/api/v1/models
  printf "deepseek:   "; curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $DEEPSEEK_API_KEY" https://api.deepseek.com/models
  printf "groq:       "; curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $GROQ_API_KEY" https://api.groq.com/openai/v1/models
  printf "resend:     "; curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $RESEND_API_KEY" https://api.resend.com/domains
  printf "omp:        "; curl -s -o /dev/null -w "%{http_code}\n" -H "Authorization: Bearer $OMP_API_KEY" http://127.0.0.1:3456/v1/health
'
```

`SUBSTRATE_INGEST_SECRET` — presence only, never exercised (exercising it writes data):

```bash
sudo -u ubuntu -H bash -c '
  set -a; . /home/ubuntu/assure-prototype/.env.staging; set +a
  [ -n "$SUBSTRATE_INGEST_SECRET" ] && echo "substrate: present" || echo "substrate: ABSENT"
'
```

`CLOUDFLARE_TUNNEL_TOKEN` — do not probe the tunnel with the token. After rotating, confirm the
tunnel is up and the hostnames resolve:

```bash
systemctl is-active cloudflared
curl -s -o /dev/null -w "staging=%{http_code}\n"   https://staging.getassureai.com/
curl -s -o /dev/null -w "prototype=%{http_code}\n" https://prototype.getassureai.com/
```

## Rollback

Every env file must be backed up before editing:

```bash
cp <file> <file>.bak-$(date +%Y%m%d-%H%M%S)
```

Rollback = restore the `.bak`, restart the affected service, re-run that key's verification command
above. Note the trap: `.env.staging.bak` already exists and is **not** a timestamped backup — do not
let a rollback pick it up by accident.

## What a rotation window requires

- The demo is not imminent.
- The user is present (dashboard access is needed to generate and revoke).
- A second person or a second terminal watching the affected service.

## Related

- `docs/live.md` — what serves what on staging, and the constraints that bite.
- `docs/decisions.md` — why the current shape is what it is.
