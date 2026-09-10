# Assure Quality-Check Suite

Unified pre-promotion gate covering API keys, backend routes, Operator Cockpit UI, design tokens, and visual regression.

## Run locally

```bash
# Uses embedded Flask server via tests/e2e/conftest.py (no manual flask run needed)
pytest tests/quality_check/ -v

# Playwright UI + visual tests only
pytest tests/quality_check/test_frontend_ui.py tests/quality_check/test_visual_regression.py -v

# Strict API key audit (self-hosted / staging runner with .env.production)
QUALITY_CHECK_STRICT=1 pytest tests/quality_check/test_api_keys.py -v

# Live provider connectivity (costs a few cents)
QUALITY_CHECK_LIVE_CALLS=1 pytest tests/quality_check/test_api_keys.py -v -k reachable

# Regenerate visual baselines after intentional UI changes
QUALITY_CHECK_UPDATE_SNAPSHOTS=1 pytest tests/quality_check/test_visual_regression.py -v
```

## What each module covers

| File | Coverage |
| --- | --- |
| `test_api_keys.py` | Required keys present (`set` / `missing` / `invalid`); optional live 1-token probes |
| `test_backend_endpoints.py` | `/health`, projects, substrate, history, runs, draft, jdf, redhat status |
| `test_frontend_ui.py` | Founder layout, ⌘K prompt, evidence drawer, export btn, sync status, console errors |
| `test_design_tokens.py` | Cockpit-scoped hex color and px spacing allowlists |
| `test_visual_regression.py` | Screenshot diffs for workbench, operator prompt, evidence drawer |

## CI

`.github/workflows/quality-check.yml` runs on PRs to `staging` / `main` and on pushes to `staging` using the self-hosted runner.

Visual regression is `continue-on-error: true` for the first adoption period — remove once baselines stabilize.

## Environment variables

| Variable | Purpose |
| --- | --- |
| `QUALITY_CHECK_STRICT=1` | Fail when required API keys are missing |
| `QUALITY_CHECK_LIVE_CALLS=1` | Run cheap live API connectivity checks |
| `QUALITY_CHECK_PROJECT_ID` | Project id for founder route tests (default `founder`) |
| `ASSURE_BASE_URL` | Override target host (default: ephemeral local server) |
