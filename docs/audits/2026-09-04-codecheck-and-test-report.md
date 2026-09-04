# Assure — code check & test report

| Field | Value |
|---|---|
| **Date** | 2026-09-04 |
| **Branch** | `p4-account-wallet` |
| **HEAD (local)** | `917e969` |
| **Author** | Cursor agent session (copy rewrite + CI hardening arc) |
| **Related docs** | [product-status.md](../product-status.md) (stale — see §6), [launch-checklist.md](../launch-checklist.md), prior audits in `docs/audits/` |

---

## 1. Where is the “full code check” report?

**There was no single delivered 12-area codebase audit** from the session that requested architecture, SQLite, JDF, Z3, SSE, cost governance, frontend, security, observability, error handling, testing, and performance.

What exists instead:

| Artifact | Path | Scope |
|---|---|---|
| **This report** | `docs/audits/2026-09-04-codecheck-and-test-report.md` | Test output snapshot, lint/CI status, gap list, next actions |
| Product status (Aug–Sep snapshot) | `docs/product-status.md` | Feature checklist; **not** a security/architecture deep-dive |
| Compose/webapp audit | `docs/audits/2026-09-03-webapp-compose-audit.md` | Compose-era UI |
| Product audit | `docs/audits/2026-08-31-product-audit.md` | Pre–Document Compiler |
| Launch registers | `docs/audits/assure-final-action-register.md`, `assure-launch-action-plan.md` | Launch gates, not engine audit |
| CI workflow | `.github/workflows/ci.yml` | Automated checks on push/PR |
| Security workflow | `.github/workflows/security.yml` | Trivy + Bandit (artifact upload in CI) |
| Pre-commit | `.pre-commit-config.yaml` | Staged ruff + quick unittest |
| Bandit JSON (CI only) | `bandit-report.json` artifact | Not stored in repo |

**Conclusion:** Use **this file** as the index until `2026-09-04-full-codebase-audit.md` is written (planned in §5).

---

## 2. Test output report (local run — 2026-09-04)

Environment: macOS, `uv sync --extra dev`, workspace `/Users/og/Untitled`.

### 2.1 Summary

| Suite | Command | Result |
|---|---|---|
| **unittest** | `uv run python -m unittest discover -s tests -p 'test_*.py' -q` | **205 passed** |
| **pytest (unit/integration)** | `uv run pytest tests/ --ignore=tests/e2e -q` | **280 passed**, 2 warnings |
| **pytest (e2e, collected)** | `uv run pytest tests/e2e/ --collect-only -q` | **10 tests** (Playwright; not run locally in this snapshot) |

### 2.2 Warnings (non-failing)

- `PytestCollectionWarning` on `tests/test_swarm.py` — `TestResults` Pydantic model name collides with pytest collection (pre-existing).

### 2.3 Fixes applied during this report

- `tests/test_credit_wallet.py::test_landing_turkish_brand_option2` — demo tab `Hukuk ve M&A` must assert `Hukuk ve M&amp;A` in raw HTML (entity escape).

### 2.4 CI parity (`.github/workflows/ci.yml`)

On push/PR to `p4-account-wallet`:

1. `ruff check` — E9, F821, F822, F823
2. `ruff format --check` — core DB/ledger/logger modules
3. `mypy` — advisory (`continue-on-error`) on 4 core modules
4. `unittest discover -s tests -v`
5. `pytest tests/ --ignore=tests/e2e -q`
6. `bandit -ll` → uploads `bandit-report.json`
7. `pem --ci` compile + eval sample
8. **E2E** (PR only, optional): `pytest tests/e2e/` after Playwright install

### 2.5 E2E status

| Item | Status |
|---|---|
| Playwright fixtures | Moved to `tests/e2e/conftest.py` |
| User simulation | `tests/e2e/test_user_simulation.py` (was root `tests/test_user_simulation.py`) |
| Local run | **Not executed** in this snapshot (requires `playwright install chromium`) |
| CI | Runs on PR; `continue-on-error: true` |

**Recommended local command:**

```bash
uv sync --extra dev
uv run playwright install chromium
uv run pytest tests/e2e/ -q --tb=short
```

---

## 3. Code check report (static analysis)

### 3.1 Ruff (syntax / undefined names)

```bash
uv run ruff check prompt_matrix/ --select E9,F821,F822,F823
```

**Result:** All checks passed (2026-09-04).

### 3.2 Ruff format (CI subset)

Checked paths: `prompt_matrix/db`, `prompt_matrix/ledger`, `prompt_matrix/cost_governance.py`, `prompt_matrix/lib/logger.py`.

**Local status:** Run in CI; not re-run for this doc.

### 3.3 Mypy

Advisory on 4 core modules in CI. Full-package mypy **not** gated.

### 3.4 Bandit

Configured in CI (`-ll`, medium+). Local run is slow; rely on CI artifact `bandit-report.json` or:

```bash
uv run bandit -r prompt_matrix/ -ll -f json -o bandit-report.json
```

### 3.5 Pre-commit

`.pre-commit-config.yaml` — ruff on staged files + quick unittest. **Install:**

```bash
uv run pre-commit install
uv run pre-commit run --all-files   # first full pass
```

---

## 4. Work landed this session (uncommitted local tree)

Large working tree on `p4-account-wallet` — **not pushed** as of report time.

| Area | What changed |
|---|---|
| **CI / quality** | Expanded `ci.yml`, new `security.yml`, `requirements-dev.txt`, ruff/mypy/bandit in `pyproject.toml`, pre-commit |
| **Test isolation** | E2E conftest split; audit logger re-init on `DATABASE_PATH` change |
| **Landing copy** | Steve Jobs–style hero, features, 3-act engine, comparison table |
| **Workbench copy** | Human labels (Compile, Refine, Stress Test, Math Check, Export, Working…) |
| **i18n** | All 7 locales in `prompt_matrix/i18n.py`; demo tab profession labels fixed (no calques) |
| **Cache bust** | `ui_cache.py` — `APP_JS=assure-50`, landing CSS/JS bumps |
| **Tests** | `test_credit_wallet`, `test_workbench_safeguards`, market readiness |

**Untracked (should commit with above):**

- `.pre-commit-config.yaml`, `.github/workflows/security.yml`, `requirements-dev.txt`
- `tests/e2e/`, `scripts/apply_jobs_copy.py`, `prompt_matrix/static/landing-demo.js`, `.cursor/settings.json`

---

## 5. Full codebase audit — **NOT DONE** (planned matrix)

The original 12-area audit was **never written**. Use this matrix for the follow-up report `docs/audits/2026-09-04-full-codebase-audit.md`:

| # | Area | Priority | Starting points | Deliverable |
|---|---|---|---|---|
| 1 | **Architecture** | P1 | `web.py`, `routers/`, `models/jdf.py`, SSE draft pipeline | Diagram + coupling notes |
| 2 | **SQLite / WAL** | P1 | `db/connection.py`, repositories, busy_timeout retries | Concurrency + migration risks |
| 3 | **JDF AST** | P1 | `models/jdf.py`, `exporters/docx_ast.py` | Schema drift, annotation vs export |
| 4 | **Z3 / Truth ledger** | P1 | `ledger/truth_engine.py`, pool limits | SAT/UNSAT UX, false positives |
| 5 | **SSE / streaming** | P2 | `draft.py`, `inquire_stream.py`, client reconnect | Timeout, retry, backpressure |
| 6 | **Cost governance** | P2 | `cost_governance.py`, LiteLLM runner | 429 handling, model routing table |
| 7 | **Frontend / workbench** | P2 | `index.html`, `jdf_canvas.js`, `generate.js` | State machine, mobile lockout |
| 8 | **Security** | P1 | auth (Clerk), upload limits, Bandit/Trivy CI | Threat model + open findings |
| 9 | **Observability** | P2 | logger, Sentry, Plausible, `/health` | Gaps in production tracing |
| 10 | **Error handling** | P2 | routers, SSE error events, user-facing i18n errors | Consistency audit |
| 11 | **Testing** | P1 | coverage map: unit vs e2e vs PEM eval | Gaps list + 5 highest-value tests |
| 12 | **Performance** | P3 | Z3 pool, Textract, large PDF path | Bottleneck notes |

**Method:** Read code + run targeted tests; no invented stats; cite file paths; split [Verified from Context] vs [Logical Inference] for external claims.

---

## 6. Next actions (prioritized)

### Immediate (before next deploy)

1. **Commit or PR** the local quality + copy tree (see §4). Run full CI locally:
   ```bash
   uv run ruff check prompt_matrix/ --select E9,F821,F822,F823
   uv run pytest tests/ --ignore=tests/e2e -q
   uv run python -m unittest discover -s tests -q
   ```
2. **Run e2e locally** once Playwright is installed; fix failures before relying on CI optional job.
3. **Update `docs/product-status.md`** — test count (280+205), UI cache `assure-50`, Steve Jobs copy, workbench labels (still describes old “Intellectual Compiler” workbench strings in places).

### Short term (this week)

4. **Write** `docs/audits/2026-09-04-full-codebase-audit.md` using §5 matrix (estimate: 2–4 focused agent passes).
5. **i18n QA pass** — spot-check ES/ZH/FR/DE/JA/TR for remaining `**EN` inheritances on landing feature/compare bodies and architecture page.
6. **Remove or gate** `scripts/apply_jobs_copy.py` if one-shot copy helper is no longer needed.
7. **Review Bandit artifact** from latest GitHub Actions run; triage medium+ findings.

### Medium term

8. **Humanize `/architecture`** copy (optional) to match landing/workbench tone without dumbing down JDF/Z3 for technical readers.
9. **Mypy expansion** — widen from 4 files or keep advisory; document decision.
10. **Production verify** — `curl https://getassureai.com/health` vs local `build_sha` after deploy.

### Deferred (user mentioned, not started)

- Token usage UI in workbench
- Full Sonatype dependency audit via MCP (optional)

---

## 7. Quick reference — commands

```bash
# Full unit/integration
uv run pytest tests/ --ignore=tests/e2e -q
uv run python -m unittest discover -s tests -q

# E2E
uv run playwright install chromium
uv run pytest tests/e2e/ -q

# Lint
uv run ruff check prompt_matrix/ tests/
uv run ruff format --check prompt_matrix/db prompt_matrix/ledger

# Pre-commit
uv run pre-commit run --all-files

# Security (local)
uv run bandit -r prompt_matrix/ -ll
```

---

## 8. Document history

| Date | Change |
|---|---|
| 2026-09-04 | Initial report: no prior 12-area audit; local test snapshot 205+280 pass; plan for full audit doc |
