# Staging recovery & launch execution (2026-09-09)

**Status:** Staging EC2 unavailable until disk is cleared today. Production marketing on R2; production workbench healthy.

**Instance:** `i-03e39eccc57572191` · **Git target:** `staging` @ `fe73790` · **UI cache:** `assure-127` (workbench), `landing.css?v=59` / `landing.js?v=45` (marketing)

Execute phases **in order**. Do not run Phase 4 QA until Phases 1–2 are done.

---

## Phase 1 — Unblock staging infrastructure

**Goal:** `https://staging.getassureai.com/health` → 200 with `build_sha` from `fe73790` lineage.

| Step | Command / action |
|------|------------------|
| 1 | AWS Console → EC2 → **Connect** → Session Manager (or SSH if SSM still fails after cleanup) |
| 2 | `cd /home/ubuntu/assure && git fetch origin && git checkout staging && git pull origin staging` |
| 3 | `bash scripts/aws/free-disk-cleanup.sh` — Docker prune, journal vacuum, SQLite WAL checkpoint |
| 4 | `docker compose -f docker-compose.yml exec -T assure-app flask prune-cache` (or `.venv/bin/flask prune-cache` if bare metal) |
| 5 | `bash scripts/aws/redeploy-app.sh` — pull GHCR image / restart container |
| 6 | Verify: `curl -sS https://staging.getassureai.com/health \| jq .` |
| 7 | **Runner:** `sudo systemctl status actions-runner` → if inactive: `sudo systemctl restart actions-runner` |

**If disk still full:** remove old GHCR images manually (`docker images`), truncate large logs under `/var/log`, then re-run cleanup.

**Risk:** GitHub Actions `ubuntu-latest` jobs may still fail on billing cap; EC2 manual deploy is the workaround per [deploy-flow](../.cursor/rules/deploy-flow.mdc).

---

## Phase 2 — Eliminate Stack B legal risk

**Goal:** No live or sync path serves outdated “zero retention” / BYOK-auditor copy from `landing/`.

| Option | Action | Recommendation |
|--------|--------|----------------|
| **A (recommended)** | Delete `landing/` from repo after confirming R2 export is canonical | ✅ Do this when Phase 1 green |
| **B** | Port Truth Engine copy into `landing/index.html` | ⚠️ Drift risk — avoid unless you need workers.dev draft |
| **Required** | Remove or archive `landing/sitemap.xml` stale paths | Before any future Stack B publish |

**Live marketing source of truth:** `prompt_matrix/i18n.py` → `scripts/build-marketing-static.sh` → `scripts/deploy-marketing-r2.sh` → R2.

---

## Phase 3 — Marketing visual polish (Stack A)

**Goal:** Enterprise “precision instrument” feel — 8px grid, tactile CTAs, trust cards, footer, modal.

| File | Changes |
|------|---------|
| `prompt_matrix/static/landing.css` | Spacing vars, hero rhythm, CTA hover, trust grid, footer, lang switcher, waitlist modal |
| `prompt_matrix/templates/includes/landing_footer.html` | Back-to-top link |
| `prompt_matrix/templates/includes/landing_header.html` | Visible language label |
| `prompt_matrix/static/landing-pilot.js` | Success animation class |
| `prompt_matrix/ui_cache.py` | Bump `LANDING_CSS` / `LANDING_JS` |

**Checklist:** [marketing-visual-qa.md](../audits/2026-09-09-marketing-visual-qa.md)

---

## Phase 4 — Visual QA & locale verification

**Prerequisites:** Phase 1 staging healthy; Phase 2 Stack B decision recorded; Phase 3 merged.

| Step | Action |
|------|--------|
| 1 | `bash scripts/build-marketing-static.sh` → open `dist/index.html` at **390 / 768 / 1080 / 1440** |
| 2 | Spot-check **all 7 locales**: `/`, `/privacy`, `/architecture` — no mixed EN/local copy |
| 3 | Hero spacing, CTA balance, trust card hover, footer disclaimer, waitlist modal success state |
| 4 | Production smoke: `curl -sI https://getassureai.com/` · `/privacy` · `app.getassureai.com/app` |

**Deploy after QA:**

```bash
bash scripts/deploy-marketing-r2.sh --dry-run   # preview
bash scripts/deploy-marketing-r2.sh             # changed files only (free tier)
```

---

## Phase 5 — Workbench verification (staging)

| Step | Action |
|------|--------|
| 1 | Staging `/app` — state rail 48px, `#runs-stack` filters, button hierarchy |
| 2 | Page source shows `assure-127` (workbench cache) |
| 3 | `/app?lang=tr` — no **yönerge**; ⌘K shows **Soru** |

---

## Phase 6 — Research synthesis (post-recovery)

**Spec:** [research-synthesis-pr1-pr2.md](./research-synthesis-pr1-pr2.md)

| PR | Branch | Scope |
|----|--------|-------|
| **PR 1** | `feat/research-synthesis-pr1` | Schema v23, draft snapshots, iterate, draft verify/redhat, run detail UI |
| **PR 2** | `feat/research-synthesis-pr2` | Compare, selective merge, runs search (frontend; reuse existing APIs) |

Do **not** start PR 1 until Phase 1 staging health is green — implementation QA needs a running EC2 app.

---

## Phase 7 — Log audit & deploy

| Step | Action |
|------|--------|
| 1 | Log row in `docs/audits/` per [WEBPAGE_AUDIT_STANDARD.md](../WEBPAGE_AUDIT_STANDARD.md) |
| 2 | R2 deploy (if not done in Phase 4) |
| 3 | Update [product-status.md](../product-status.md) snapshot |
| 4 | Begin [research-synthesis-pr1-pr2.md](./research-synthesis-pr1-pr2.md) when workbench baseline verified |

---

## Marketing i18n audits

| Doc | Topic |
|-----|-------|
| [2026-09-09-marketing-i18n-mixed-wording.md](../audits/2026-09-09-marketing-i18n-mixed-wording.md) | Locale fallbacks |
| [2026-09-09-workbench-i18n-redundancy.md](../audits/2026-09-09-workbench-i18n-redundancy.md) | Workbench + TR terminology |
| [2026-09-09-marketing-visual-qa.md](../audits/2026-09-09-marketing-visual-qa.md) | Visual checklist |

---

## Related docs

| Doc | Role |
|-----|------|
| [webpage-all-content.md](../webpage-all-content.md) | Marketing master |
| [marketing-r2-free-tier.md](./marketing-r2-free-tier.md) | R2 deploy guards |
| [launch-checklist.md](../launch-checklist.md) | Full launch gates |
