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

## Cloudflare

GitHub [`orhgor/assure`](https://github.com/orhgor/assure) holds the **webpage only** (branch `webpage`, site at repo root). The workbench and PEM engine stay on this machine. Do not push `main` to that remote.

The connected Worker is `assure`. Deploy command is `npx wrangler deploy`. `wrangler.jsonc` must list `assets.directory` (not a Pages `pages_build_output_dir`). Public site: [https://getassureai.com/](https://getassureai.com/) (HTTPS 200 as of 2026-09-01).

Copy the site files, then push the webpage branch yourself:

```bash
./scripts/sync-webpage.sh /path/to/webpage-checkout
```
