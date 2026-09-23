# Assure

Ask one question. Get one verified answer. The question is rewritten for the model you pick, then checked.

The compiler is PEM (`prompt_matrix`). `assure --web` opens your browser. First run: paste a provider key, then write a question. There is no password prompt on this computer unless you set one.


## Quick start

The workbench stays on this computer. GitHub `orhgor/assure` is the public site only.

### Desktop (no terminal)

```bash
./scripts/install.sh
./scripts/build-desktop.sh
```

Then double-click `dist/Assure.app` (macOS), `dist\Assure\Assure.exe` (Windows), or `dist/Assure/Assure` (Linux). The browser should open. If it does not, go to [http://127.0.0.1:8765](http://127.0.0.1:8765). There is no public download URL yet.

### From source

```bash
./scripts/install.sh
source prompt_matrix/.venv/bin/activate
assure --web
```

Windows: `scripts\install.ps1`. `pip install prompt-matrix` is not on PyPI yet. Do not clone `orhgor/assure` for the app.

Sign-in is off on this machine by default. Sharing on the LAN (`--host 0.0.0.0`) requires `--http-pass` (or `PEM_HTTP_PASS`). Do not commit the password.

Team edition (unlimited Sends on this machine):

```bash
assure --web --edition team
```

### Ask

Write a question in Compose, or click an example (Compare AWS vs GCP, Summarize a paper, Write a marketing email). Send and get my answer is the default. Copy the prompt keeps the compiled text on this computer.

First run opens Connect so you can paste a key. Copy stays on this computer. A Send goes only to the provider you chose.

Pro is $5 per month on the Pricing page in this tree.

Install, CLI, and MCP details: [prompt_matrix/README.md](prompt_matrix/README.md). Product overview: [prompt_matrix/PEM.md](prompt_matrix/PEM.md). Public landing: [landing/index.html](landing/index.html).

## Testing

**CI** runs `pytest` unit tests plus a **Playwright** suite under `tests/playwright/` (headless Chromium, local embedded Flask — no live model keys).

```bash
uv sync --extra dev
playwright install chromium
pytest tests/playwright/ -v
```

Optional against staging: `ASSURE_BASE_URL=https://staging.getassureai.com pytest tests/playwright/ -v` (requires auth and live compile quota).

## Cloudflare

Two branches, two surfaces:

| Branch | Deploy target | URL |
|--------|---------------|-----|
| `p4-account-wallet` | EC2 Docker — image built in **GitHub Actions**, pulled on EC2 ([deploy flow](docs/deploy-flow.md)) | [getassureai.com](https://getassureai.com) |
| `webpage` | Cloudflare Worker `assure` — 301 → app host | [getassureai.com](https://getassureai.com) → app |

GitHub [`orhgor/assure`](https://github.com/orhgor/assure) default branch is **`webpage`** (marketing site at repo root). The JDF Workstation and PEM engine live on **`p4-account-wallet`** and deploy to EC2 — not through Cloudflare Workers Builds.

Cloudflare Workers Builds for Worker **`assure`** must connect to **`webpage` only**. A red **Workers Builds: assure** check on an app PR is irrelevant (wrong branch / missing root `wrangler.jsonc`). Fix: [docs/cloudflare-fix.md](docs/cloudflare-fix.md) or `bash scripts/cloudflare/set_workers_branch.sh`.

Marketing deploy: copy `landing/` to a webpage checkout, then push `webpage`:

```bash
./scripts/sync-webpage.sh /path/to/webpage-checkout
```

Worker deploy command: `npx wrangler deploy`. `wrangler.jsonc` must list `assets.directory` (not a Pages `pages_build_output_dir`).

## UI Systems

Assure has two UI systems in this repository. Understanding which is which prevents confusion during development and debugging.

### Prototype UI (`prototype/`)

**The "real" Assure UI.** This is the current design system used by the live application.

```
prototype/
  index.html    # Main workbench shell — the actual app UI
  shell.css     # Design system: tokens, layout grid, typography
  shell.js      # Client-side behavior
  wow_effects.js # Visual effects (laser, stamps, etc.)
  about.html    # About page
  favicon.svg   # App icon
```

**Design principles:**
- One accent colour (ink `#0A0A0A`), four semantic colours, everything else monochrome
- Serif for document body, sans for chrome, mono for data
- 8px grid, no exceptions
- Layout: 48px rail | 280px left pane | 1fr center | 320px right pane | 48px rail
- Header is 56px with version control centered over document column

**CSS variables:**
```css
:root {
  --ink: #0A0A0A;           /* primary accent */
  --paper: #FAFAF7;         /* background */
  --surface: #FFFFFF;       /* cards, panels */
  --rule: #E8E8E4;          /* borders */
  --muted: #6B6B66;         /* secondary text */
  --verified: #0F6E3F;      /* supported */
  --partial: #B8730E;       /* partial */
  --unverified: #9A9A94;    /* unanchored */
  --contradicted: #A32D2D;  /* unsupported */
}
```

### Prompt Matrix UI (`prompt_matrix/templates/`)

**Legacy/older UI system.** Some pages still use this. It uses `founder_workbench.css` which is **not** the current design system.

```
prompt_matrix/templates/
  base.html          # Base template (loads founder_workbench.css)
  index.html         # Main app (workbench)
  parsing.html       # Parsing dashboard
  auth.html          # Sign in/up (Clerk integration)
  connect.html       # Provider connection
  ...                # Other pages
```

**Warning:** `founder_workbench.css` is a different design system from `prototype/shell.css`. Pages extending `base.html` get the wrong visual style for the current application.

## Parsing Page (`/parsing`)

**Route:** `GET /parsing` — no auth required (intentional, security deferred)

**Template:** `prompt_matrix/templates/parsing.html`

**Backend:** `prompt_matrix/web.py::parsing_page()`

**What it shows:**
- Summary bar: total documents, JDF CLI count, Textract count, avg parse confidence
- Document cards with parser badges (JDF CLI = green, Textract = amber)
- Per-document: filename, pages, parse/OCR confidence, table/image/figure counts
- Empty state when no documents parsed

**Data flow:**
```
web.py parsing_page()
  → list_substrate_for_project(project_id)
  → builds documents[] and summary{}
  → renders parsing.html with summary=summary, documents=documents
```

**Known bug (fixed):** Template expected `{{ total }}` but `web.py` passed `summary={total: ...}`. Fixed by changing template to use `{{ summary.total }}`, `{{ summary.jdf_count }}`, etc.

**Design system:** Uses `style.css` (v2.0 design tokens) + inline styles matching prototype design principles. Does NOT extend `base.html` (avoids `founder_workbench.css`).

## File Inventory — Where Things Live

### Application Core
| Path | Purpose |
|------|---------|
| `prompt_matrix/web.py` | Flask app, all routes including `/parsing` |
| `prompt_matrix/cloud_auth.py` | Clerk auth (optional, gated by `ASSURE_EDITION`) |
| `prompt_matrix/ui_cache.py` | CSS/JS version strings (`assure-98`) |
| `prompt_matrix/static/` | Shared static files (style.css, script.js, etc.) |

### UI — Current (prototype)
| Path | Purpose |
|------|---------|
| `prototype/index.html` | Main workbench — the actual app |
| `prototype/shell.css` | Current design system |
| `prototype/shell.js` | Current client behavior |

### UI — Legacy (prompt_matrix)
| Path | Purpose |
|------|---------|
| `prompt_matrix/templates/base.html` | Base template (uses founder_workbench.css) |
| `prompt_matrix/templates/parsing.html` | Parsing dashboard |
| `prompt_matrix/static/founder_workbench.css` | Legacy design system (NOT current) |
| `prompt_matrix/static/style.css` | v2.0 design tokens (shared) |

### Configuration
| Path | Purpose |
|------|---------|
| `.env.staging` | Staging environment (EC2) |
| `.env.production` | Production environment |
| `prompt_matrix/.env` | Local development |

### Deploy
| Path | Purpose |
|------|---------|
| `docker-compose.staging.yml` | Staging compose |
| `base_dc.yml` | Base deployment config |
| `ec2-live.patch` | Snapshot of EC2-local changes before GitHub merge |

## Branches

| Branch | Purpose |
|--------|---------|
| `main` | Default. Contains prototype UI + prompt_matrix legacy UI. Marketing site at root. |
| `staging` | Staging EC2 deploy. Same as main + staging-specific changes. |
| `webpage` | Cloudflare Worker marketing site. |

**Default branch is `webpage`** (marketing site). The app code lives on `main` and `staging`.

## Clerks and Auth

- `/parsing` is **not** in `PROTECTED_HTML` — accessible without Clerk session
- `base.html` conditionally renders Account/Sign in based on Clerk auth state
- `ASSURE_EDITION=self-hosted` disables Clerk entirely
- Auth protection is deferred — security is a later concern

## Revisions

When making changes to the parsing page or UI:

1. **Prototype UI changes** → edit `prototype/index.html`, `prototype/shell.css`, `prototype/shell.js`
2. **Parsing page changes** → edit `prompt_matrix/templates/parsing.html`
3. **Route/backend changes** → edit `prompt_matrix/web.py`
4. **Design system changes** → edit `prototype/shell.css` (current) or `prompt_matrix/static/style.css` (shared tokens)

Do NOT use `founder_workbench.css` for new work — it is the legacy system.

