# Edge restructure — final code check

**Date:** 2026-09-04
**Branch:** `p4-account-wallet` @ `b395c7a` (production) + local rate-limit patch (uncommitted → commit pending)
**Production:** https://getassureai.com/health

---

## Executive summary

| Verdict | Detail |
|---------|--------|
| **Production** | **GO** — healthy, edge worker + `/api/substrate` verified live |
| **Local tests** | **GO** — 289 pytest + 205 unittest (494 total) |
| **Staging isolation** | **PARTIAL** — Worker/R2 staging exist; **no staging EC2**, **no `staging` branch**, **no GitHub `staging` environment** |
| **CI (GitHub Actions)** | **FAIL** on latest push — segfault in `test_sandbox_verify_success` (exit 139); Security workflow passed |
| **Rate limiting** | **Added locally** — Flask-Limiter + Worker KV; needs commit + deploy |

---

## Checklist

| # | Check | Status | Notes |
|---|-------|--------|-------|
| 1 | Unit tests (pytest) | ✅ | 289 passed locally (`--ignore=tests/e2e`) |
| 2 | Unit tests (unittest) | ✅ | 205 passed locally |
| 3 | Ruff E9/F821/F822/F823 | ✅ | All checks passed |
| 4 | Ruff format (db, ledger) | ✅ | 9 files formatted |
| 5 | Bandit -ll | ⚠️ | Runs; many low/medium findings (pre-existing litellm tree) |
| 6 | Integration `/api/substrate` | ✅ | Live POST with worker secret → `{"ok": true}` |
| 7 | Worker health | ✅ | prod + staging workers return `ok: true` |
| 8 | Worker upload-url | ✅ | prod returns signed upload URL + R2 key |
| 9 | Production `/health` | ✅ | `status: healthy`, `build_sha: b395c7a`, ~19.6 GB disk free |
| 10 | App Docker GHCR deploy | ⚠️ | Actions job failed (health timeout during disk crisis); **manual SSM redeploy succeeded** |
| 11 | CI workflow | ❌ | Exit 139 segfault in `tests/test_sandbox.py::test_sandbox_verify_success` on `ubuntu-latest` Python 3.12 |
| 12 | Staging branch deploy | ⬜ | No `staging` branch; `cd-staging.yml` not on default branch |
| 13 | GitHub Environment `staging` | ⬜ | Only `production` environment configured |
| 14 | Staging EC2 | ⬜ | `STAGING_INSTANCE_ID` secret not set |
| 15 | Documentation | ✅ | `deploy-flow.md`, `PEM.md`, `product-status.md` updated (pre rate-limit) |
| 16 | Rate limiting | ✅ (local) | Worker KV + Flask-Limiter + `daily_compile_limits` table |

---

## Environment isolation matrix

| Resource | Staging | Production |
|----------|---------|------------|
| **EC2** | Not provisioned | `i-09d0ad0b561113abe` |
| **Worker** | `assure-worker-staging.orhangorenn.workers.dev` | `assure-worker-prod.orhangorenn.workers.dev` |
| **R2 PDF bucket** | `assure-pdf-uploads-staging` | `assure-pdf-uploads-prod` |
| **R2 backup bucket** | — | `assure-prod-backups` |
| **KV rate limit** | `701c7f9cc71f46e0a6c8519bb6db9070` | `ccf7cdf70a004c4f97ccbc4f8f102c7a` |
| **EC2 backend URL (worker secret)** | Both point to `https://getassureai.com` today | Same — **staging EC2 needed for full isolation** |
| **GitHub Environment** | Missing | `production` (created 2026-09-04) |

**Isolation verdict:** Cloudflare edge resources are separated. Backend is **not** isolated until a second EC2 + `staging` branch workflow runs.

---

## Edge flow verification

```
Browser → Worker GET /api/upload-url     ✅ (prod tested)
       → Worker PUT /upload/{key}        ✅ (implemented; not E2E tested with real PDF this pass)
       → Worker POST /api/process        ✅ (implemented; unpdf/Textract → EC2)
       → EC2 POST /api/substrate         ✅ (live, secret-gated)
       → R2 delete PDF                   ✅ (implemented in worker)
```

**Limits enforced (worker):** 10 MB, 5 pages/doc, 50 pages/project, 20 pages/day (Textract), 20 uploads/project/day (after rate-limit deploy).

---

## Failures and fixes

### 1. GitHub Actions CI — segfault (exit 139)

- **Symptom:** `test_sandbox_verify_success` crashes on `ubuntu-latest` during pytest.
- **Local:** Passes on Python 3.13.
- **Likely cause:** Native extension (Z3/LiteLLM stack) on CI Python 3.12.
- **Mitigation options:** Pin CI to 3.13; mark test `@pytest.mark.skip` on CI; or run sandbox tests in a separate job with `--forked`.
- **Status:** Open — does not block production (manual test pass).

### 2. App Docker deploy job — health timeout

- **Symptom:** GHCR pull succeeded but health check failed during deploy.
- **Cause:** EC2 disk at **99%** (~390 MB free).
- **Fix applied:** `docker system prune -af` reclaimed **~23 GB**; manual `ssm-redeploy-and-wait.sh` succeeded.
- **Recommendation:** Add weekly `docker system prune` cron or image retention policy on EC2.

### 3. Staging not end-to-end

- **Gap:** No staging EC2, branch, or GitHub Environment.
- **Next steps:**
  1. Create GitHub Environment `staging` + `STAGING_INSTANCE_ID` secret.
  2. Launch staging t4g.small (or share account with separate instance).
  3. Create `staging` branch from `p4-account-wallet`.
  4. Point staging worker `EC2_BACKEND_URL` to staging host.

### 4. Rate limiting (implemented this pass)

- **Worker:** `worker/rate-limiter.js` + KV — 20 req/IP/min on `/api/upload-url` and `/api/process`.
- **Backend:** `flask-limiter` on compile/refine streams (30/min), export (10/min), substrate (60/min, worker exempt).
- **SQLite:** `daily_compile_limits` — 100 compiles/project/day (configurable via `ASSURE_COMPILE_DAILY_LIMIT`).
- **Status:** Tested locally (289 pytest); **requires commit + EC2 redeploy**.

---

## Rate limits summary (after deploy)

| Endpoint | Limit | Layer |
|----------|-------|-------|
| `/api/upload-url` | 20 / IP / min | Worker KV |
| `/api/process` | 20 / IP / min | Worker KV |
| `/api/projects/*/inquire/stream` | 30 / IP / min + 100 / project / day | EC2 |
| `/api/projects/*/draft/stream` | 30 / IP / min + 100 / project / day | EC2 |
| `/api/projects/*/export` | 10 / IP / min | EC2 |
| `/api/substrate` | Worker secret exempt; else 60 / IP / min | EC2 |
| PDF uploads | 20 / project / day | Worker R2 counters |
| Textract pages | 20 / day, 50 / project | Worker R2 counters |

---

## Commands run (this audit)

```bash
uv run pytest tests/ --ignore=tests/e2e -q          # 289 passed
uv run python -m unittest discover -s tests -q        # 205 passed
uv run ruff check prompt_matrix/ --select E9,F821,F822,F823
uv run ruff format --check prompt_matrix/db prompt_matrix/ledger
uv run bandit -r prompt_matrix/ -ll
curl https://getassureai.com/health
curl -X POST https://getassureai.com/api/substrate (with worker secret)
curl https://assure-worker-prod.orhangorenn.workers.dev/health
curl "https://assure-worker-prod.../api/upload-url?projectId=...&filename=test.pdf"
```

---

## Recommended follow-ups (priority)

1. **Commit + deploy rate limiting** to production EC2.
2. **Fix CI segfault** — pin Python 3.13 in `ci.yml` or isolate sandbox tests.
3. **Provision staging EC2** + GitHub `staging` environment + branch.
4. **Enable backup cron** — `/health` still reports `backup: never_run`.
5. **EC2 disk monitor** — alert when `disk_free_gb` < 3.
