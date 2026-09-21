# GitHub Actions minutes

**Included cap for this account: 3,000 minutes** per billing cycle.

The previous 2,000-minute allotment was exhausted. Treat 3,000 as a hard budget, not a target.

## What burned minutes

- **CI on every `push` and every `pull_request`** (often two full pytest + Playwright jobs for the same commit).
- **Synthetic canary every 10 minutes** (thousands of `ubuntu-latest` starts per month if that workflow is enabled).

## Current policy (in the repo)

- CI: pull requests targeting `main`/`staging`, and pushes to `main`/`staging` only. Docs-only changes skip CI. In-progress runs on the same ref are cancelled.
- Canary: Monday 06:00 UTC or **Run workflow**. Not every 10 minutes.
- Deploy workflows stay on `staging`/`main` pushes and `workflow_dispatch`.

## How to see usage

1. Workbench **More → Backstage** (`/backstage`) — shows the 3,000 cap and used/remaining when configured.
2. [github.com/settings/billing](https://github.com/settings/billing)
3. Optional env on the app host: `GITHUB_ACTIONS_MINUTE_LIMIT=3000`, `GITHUB_ACTIONS_MINUTES_USED=<from billing>`, or `GITHUB_BILLING_TOKEN` with billing read.
